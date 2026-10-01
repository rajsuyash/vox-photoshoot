"""Single-use account links. Only hashes/IDs persist; the signing key stays in Secrets Manager."""
import hashlib
import hmac
import os
import uuid

from psycopg.rows import dict_row

import auth
import db
import notifications


def token(ident, purpose, user_id):
    key = os.environ['ACCOUNT_LINK_KEY']
    if len(key) < 64:
        raise RuntimeError('account link key must contain at least 256 bits')
    message = f'{ident}:{purpose}:{user_id}'
    return str(ident) + '.' + hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def issue(conn, kind, user_id, email, *, workspace_id=None, role=None, allow_password=False):
    ident = uuid.uuid4()
    raw = token(ident, kind, user_id)
    # Replace earlier reset links. Invitations to different workspaces remain independent.
    if kind == 'reset':
        conn.execute("UPDATE account_actions SET consumed_at=now() WHERE user_id=%s "
                     "AND kind='reset' AND consumed_at IS NULL", (user_id,))
    conn.execute('INSERT INTO account_actions(id,token_hash,kind,user_id,workspace_id,role,allow_password,expires_at) '
                 "VALUES(%s,%s,%s,%s,%s,%s,%s,now()+make_interval(mins => %s))",
                 (ident, auth.token_hash(raw), kind, user_id, workspace_id, role, allow_password,
                  30 if kind == 'reset' else 1440))
    notifications.enqueue(conn, f'{kind}:{ident}', kind, email, {'action_id': str(ident)},
                          workspace_id=workspace_id, user_id=user_id)
    return str(ident)


def delivery(ident):
    row = db.query('SELECT * FROM account_actions WHERE id=%s AND consumed_at IS NULL '
                   'AND expires_at>now()', (str(uuid.UUID(ident)),), one=True)
    if not row:
        raise ValueError('account link expired or already used')
    return token(row['id'], row['kind'], row['user_id'])


def request_reset(email, ip):
    """Same result for missing/Google/suspended accounts. Persistent limits span workers."""
    email = str(email).strip().lower()[:254]
    email_hash = auth.token_hash(email)
    ip_hash = auth.token_hash('recovery:' + str(ip)[:100])
    with db.tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('vox:recovery',0))")
        counts = conn.execute("SELECT count(*) FILTER(WHERE email_hash=%s),count(*) FILTER(WHERE ip_hash=%s) "
                              "FROM recovery_attempts WHERE created_at>now()-interval '15 minutes'",
                              (email_hash, ip_hash)).fetchone()
        if counts[0] >= 3 or counts[1] >= 20:
            return
        conn.execute('INSERT INTO recovery_attempts(email_hash,ip_hash) VALUES(%s,%s)',
                     (email_hash, ip_hash))
        user = conn.execute('SELECT id,password_hash FROM users WHERE lower(email)=%s '
                            'AND suspended_at IS NULL FOR UPDATE', (email,)).fetchone()
        if user and user[1]:
            issue(conn, 'reset', user[0], email)


def accept(raw, password='', session=None):
    if not isinstance(raw, str) or len(raw) > 110:
        raise ValueError('invalid or expired link')
    hashed = auth.token_hash(raw)
    with db.tx() as conn:
        # Consistent user-before-action locking avoids reset/reissue deadlocks.
        user = conn.execute('SELECT u.id,u.suspended_at,u.password_hash,u.google_sub,u.email '
                            'FROM users u JOIN account_actions a ON a.user_id=u.id '
                            'WHERE a.token_hash=%s FOR UPDATE OF u', (hashed,)).fetchone()
        if not user or user[1]:
            raise ValueError('invalid or expired link')
        with conn.cursor(row_factory=dict_row) as cur:
            action = cur.execute('SELECT * FROM account_actions WHERE token_hash=%s '
                                 'AND consumed_at IS NULL AND expires_at>now() FOR UPDATE',
                                 (hashed,)).fetchone()
        if not action:
            raise ValueError('invalid or expired link')
        setup = action['allow_password'] and not user[2] and not user[3]
        if action['kind'] == 'reset' or setup:
            if not 10 <= len(password) <= 256:
                raise ValueError('use a password between 10 and 256 characters')
            if action['kind'] == 'reset' and not user[2]:
                raise ValueError('use Google sign-in for this account')
            conn.execute('UPDATE users SET password_hash=%s WHERE id=%s',
                         (auth.hash_password(password), user[0]))
            conn.execute('DELETE FROM sessions WHERE user_id=%s', (user[0],))
            if action['kind'] == 'reset':
                notifications.enqueue(conn, f"password_changed:{action['id']}",
                                      'password_changed', user[4], {}, user_id=user[0])
        elif not session or str(session['user_id']) != str(user[0]):
            raise PermissionError('sign in with the invited email first')
        if action['kind'] == 'invite':
            workspace = conn.execute('SELECT id FROM workspaces WHERE id=%s AND archived_at IS NULL '
                                     'AND suspended_at IS NULL FOR UPDATE', (action['workspace_id'],)).fetchone()
            if not workspace:
                raise ValueError('workspace unavailable')
            # A late invitation must never demote an already promoted owner.
            conn.execute('INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,%s) '
                         'ON CONFLICT(user_id,workspace_id) DO NOTHING',
                         (user[0], action['workspace_id'], action['role']))
        conn.execute('UPDATE account_actions SET consumed_at=now() WHERE id=%s', (action['id'],))
        return {'ok': True, 'kind': action['kind']}
