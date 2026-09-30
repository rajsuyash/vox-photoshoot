"""Audited support mutations. HTTP routes supply an authenticated administrator."""
import re
import uuid

from psycopg.types.json import Jsonb

import db
import notifications


def change(actor: str, request_key: str, action: str, target: str,
           reason: str, **data) -> dict:
    actor, request_key, target = map(str, map(uuid.UUID, (actor, request_key, target)))
    reason = reason.strip()
    if not 3 <= len(reason) <= 500:
        raise ValueError('give a reason between 3 and 500 characters')
    allowed = {
        'suspend_user': {'suspended'}, 'suspend_workspace': {'suspended'},
        'revoke_sessions': set(), 'member': {'user_id', 'role'},
        'billing_contact': {'name', 'billing_email', 'gstin'},
        'invite': {'email', 'role'},
        'provision': {'name', 'billing_email', 'gstin', 'owner_email', 'credits'},
        'credit_adjustment': {'amount', 'confirm_debit'}, 'resend': set(),
        'review_deletion': {'status'},
    }
    if action not in allowed or set(data) != allowed[action]:
        raise ValueError('unsupported action or fields')
    for field in allowed[action] - {'suspended', 'credits', 'amount', 'confirm_debit'}:
        if not isinstance(data[field], str):
            raise ValueError('invalid ' + field)
    request = {'reason': reason, **data}
    with db.tx() as conn:
        # ponytail: one lock serializes support changes; split by workspace if needed.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('vox:support',0))")
        admin = conn.execute('SELECT is_admin, suspended_at FROM users WHERE id=%s',
                             (actor,)).fetchone()
        if not admin or not admin[0] or admin[1]:
            raise PermissionError('active administrator required')
        prior = conn.execute('SELECT actor_id, action, target_id, request, result '
                             'FROM admin_actions WHERE request_key=%s',
                             (request_key,)).fetchone()
        if prior:
            if (str(prior[0]), prior[1], str(prior[2]), prior[3]) != (actor, action, target, request):
                raise ValueError('request key already used for another action')
            return prior[4]
        result = {}
        if action.startswith('suspend_'):
            if type(data['suspended']) is not bool:
                raise ValueError('suspended must be boolean')
            table = 'users' if action == 'suspend_user' else 'workspaces'
            existing = conn.execute(f'SELECT id FROM {table} WHERE id=%s FOR UPDATE',
                                    (target,)).fetchone()
            if not existing:
                raise ValueError('target does not exist')
            if table == 'users' and data['suspended']:
                active = conn.execute('SELECT id FROM users WHERE is_admin '
                                      'AND suspended_at IS NULL').fetchall()
                if len(active) == 1 and str(active[0][0]) == target:
                    raise ValueError('cannot suspend the last active administrator')
            conn.execute(f'UPDATE {table} SET suspended_at=CASE WHEN %s THEN now() '
                         'ELSE NULL END WHERE id=%s', (data['suspended'], target))
            if data['suspended']:
                column = 'user_id' if table == 'users' else 'workspace_id'
                conn.execute(f'DELETE FROM sessions WHERE {column}=%s', (target,))
            result = {'suspended': data['suspended']}
        elif action == 'revoke_sessions':
            if not conn.execute('SELECT id FROM users WHERE id=%s', (target,)).fetchone():
                raise ValueError('user does not exist')
            result = {'revoked': conn.execute('DELETE FROM sessions WHERE user_id=%s',
                                              (target,)).rowcount}
        elif action == 'member':
            user_id = str(uuid.UUID(data['user_id']))
            role = data['role']
            if role not in ('owner', 'member', 'remove'):
                raise ValueError('invalid role')
            if not conn.execute('SELECT id FROM workspaces WHERE id=%s FOR UPDATE',
                                (target,)).fetchone():
                raise ValueError('workspace does not exist')
            member = conn.execute('SELECT role FROM memberships WHERE workspace_id=%s '
                                  'AND user_id=%s', (target, user_id)).fetchone()
            if not member:
                raise ValueError('invite a new member first')
            owners = conn.execute("SELECT count(*) FROM memberships WHERE workspace_id=%s "
                                  "AND role='owner'", (target,)).fetchone()[0]
            if member[0] == 'owner' and role != 'owner' and owners <= 1:
                raise ValueError('cannot remove the last owner')
            if role == 'remove':
                conn.execute('DELETE FROM memberships WHERE workspace_id=%s AND user_id=%s',
                             (target, user_id))
                conn.execute('DELETE FROM sessions WHERE workspace_id=%s AND user_id=%s',
                             (target, user_id))
            else:
                conn.execute('UPDATE memberships SET role=%s WHERE workspace_id=%s AND user_id=%s',
                             (role, target, user_id))
            result = {'role': role}
        elif action == 'billing_contact':
            name = data['name'].strip()
            email = notifications.address(data['billing_email'])
            gstin = data['gstin'].strip().upper()
            if not 1 <= len(name) <= 160 or (gstin and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gstin)):
                raise ValueError('invalid brand name or GSTIN')
            if not conn.execute('UPDATE workspaces SET name=%s,billing_email=%s,gstin=%s '
                                'WHERE id=%s RETURNING id', (name, email, gstin or None, target)).fetchone():
                raise ValueError('workspace does not exist')
            result = {'updated': True}
        elif action in {'invite', 'provision'}:
            import account_actions
            import credits
            email = notifications.address(data['owner_email'] if action == 'provision' else data['email'])
            role = 'owner' if action == 'provision' else data['role']
            if role not in ('owner', 'member'):
                raise ValueError('invalid role')
            if action == 'provision':
                name, gstin = data['name'].strip(), data['gstin'].strip().upper()
                billing_email = notifications.address(data['billing_email'] or email)
                count = data['credits']
                if (not 1 <= len(name) <= 160 or type(count) is not int or not 0 <= count <= 10000
                        or (gstin and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gstin))):
                    raise ValueError('invalid workspace details')
                conn.execute('INSERT INTO workspaces(id,name,gstin,billing_email) VALUES(%s,%s,%s,%s)',
                             (target, name, gstin or None, billing_email))
                if count:
                    credits.grant(target, count, 'opening balance: ' + reason,
                                  key='admin:' + request_key, conn=conn)
            elif not conn.execute('SELECT id FROM workspaces WHERE id=%s AND archived_at IS NULL '
                                  'AND suspended_at IS NULL FOR UPDATE', (target,)).fetchone():
                raise ValueError('workspace unavailable')
            user = conn.execute('SELECT id,suspended_at,password_hash,google_sub FROM users WHERE lower(email)=lower(%s)',
                                (email,)).fetchone()
            if not user:
                user = conn.execute('INSERT INTO users(email,password_hash) VALUES(%s,NULL) RETURNING id,suspended_at,password_hash,google_sub',
                                    (email,)).fetchone()
            if user[1]:
                raise ValueError('invited account is suspended')
            if action == 'provision':
                conn.execute('INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,%s)',
                             (user[0], target, role))
            invite = account_actions.issue(conn, 'invite', user[0], email,
                                           workspace_id=target, role=role, allow_password=not user[2] and not user[3])
            result = {'id': target, 'invite_id': invite, 'owner': email}
        elif action == 'credit_adjustment':
            import credits
            amount = data['amount']
            if (type(amount) is not int or amount == 0 or abs(amount) > 10000
                    or type(data['confirm_debit']) is not bool
                    or (amount < 0 and not data['confirm_debit'])):
                raise ValueError('adjustment must be nonzero, at most 10,000 credits; confirm debits')
            if not conn.execute('SELECT id FROM workspaces WHERE id=%s FOR UPDATE', (target,)).fetchone():
                raise ValueError('workspace does not exist')
            result = {'balance': credits.grant(target, amount, reason,
                                               key='admin:' + request_key, conn=conn)}
        elif action == 'resend':
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                mail = cur.execute('SELECT * FROM notifications WHERE id=%s FOR UPDATE', (target,)).fetchone()
            if not mail or mail['status'] in {'queued', 'sending', 'bounced', 'complained', 'suppressed'}:
                raise ValueError('only completed or failed, unsuppressed messages may be resent')
            ident = notifications.enqueue(conn, 'resend:' + request_key, mail['kind'],
                                           mail['recipient'], mail['payload'],
                                           workspace_id=mail['workspace_id'], user_id=mail['user_id'])
            result = {'notification_id': str(ident)}
        elif action == 'review_deletion':
            status = data['status']
            if status not in ('reviewing', 'rejected'):
                raise ValueError('purge is disabled until a retention policy is approved')
            if not conn.execute('UPDATE data_requests SET status=%s,reviewer_id=%s,review_note=%s,reviewed_at=now() '
                                'WHERE id=%s RETURNING id', (status,actor,reason,target)).fetchone():
                raise ValueError('request not found')
            result = {'status':status,'purge_enabled':False}
        conn.execute('INSERT INTO admin_actions(request_key,actor_id,action,target_id,reason,request,result) '
                     'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                     (request_key, actor, action, target, reason, Jsonb(request), Jsonb(result)))
        return result
