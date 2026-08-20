"""Login, sessions, and who is allowed to spend a workspace's credits.

Self-hosted rather than Clerk or Auth0: sales-led means there is no signup funnel to
build, so what is left is a password check and a cookie, and that does not justify a
per-seat vendor.

Password hashing is stdlib `hashlib.scrypt`. passlib is the usual reach here but it is
a dependency with a shaky maintenance story, and scrypt is a memory-hard KDF that ships
with Python. Parameters below are the interactive-login defaults from the scrypt paper.

    .venv/bin/python auth.py          # self-check, no database needed for the crypto
"""

import base64
import hashlib
import hmac
import os
import secrets

from fastapi import Cookie, HTTPException

import db

COOKIE = 'donna_session'
SESSION_DAYS = 14

# 128 * n * r bytes per hash, so n=2^14 is 16MB — the standard interactive parameter,
# and deliberately memory-hard: that is what makes a stolen dump expensive to crack.
# Not 2^15: that needs exactly 32MB, which is OpenSSL's default maxmem, and it refuses.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return 'scrypt${}${}${}${}${}'.format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check against a stored hash. Never raises on a malformed hash."""
    try:
        scheme, n, r, p, salt, digest = stored.split('$')
        if scheme != 'scrypt':
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (AttributeError, ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- sessions ---------------------------------------------------------------------

def start_session(user_id: str, workspace_id: str | None) -> str:
    """Returns the raw token for the cookie. Only its hash is ever stored."""
    token = secrets.token_urlsafe(32)
    db.query(
        """INSERT INTO sessions (token_hash, user_id, workspace_id, expires_at)
           VALUES (%s, %s, %s, now() + make_interval(days => %s))""",
        (token_hash(token), user_id, workspace_id, SESSION_DAYS))
    return token


def end_session(token: str) -> None:
    db.query('DELETE FROM sessions WHERE token_hash = %s', (token_hash(token),))


def end_session_by_hash(hashed: str) -> None:
    """Logout path: the session row is already loaded, so the raw token is not needed."""
    db.query('DELETE FROM sessions WHERE token_hash = %s', (hashed,))


def set_session_workspace(hashed: str, workspace_id: str) -> None:
    db.query('UPDATE sessions SET workspace_id = %s WHERE token_hash = %s',
             (workspace_id, hashed))


def lookup(token: str | None) -> dict | None:
    """The session, its user and its workspace in one round trip, or None."""
    if not token:
        return None
    return db.query(
        """SELECT s.token_hash, s.workspace_id, u.id AS user_id, u.email, u.name,
                  u.is_admin, w.name AS workspace_name
             FROM sessions s
             JOIN users u ON u.id = s.user_id
        LEFT JOIN workspaces w ON w.id = s.workspace_id
            WHERE s.token_hash = %s AND s.expires_at > now()""",
        (token_hash(token),), one=True)


def sweep_sessions() -> None:
    db.query('DELETE FROM sessions WHERE expires_at < now()')


# --- users and workspaces ----------------------------------------------------------

def create_user(email: str, password: str, name: str = '', is_admin: bool = False) -> dict:
    return db.query(
        """INSERT INTO users (email, password_hash, name, is_admin)
           VALUES (%s, %s, %s, %s) RETURNING id, email, is_admin""",
        (email.strip(), hash_password(password), name.strip() or None, is_admin),
        one=True)


def authenticate(email: str, password: str) -> dict | None:
    row = db.query('SELECT id, password_hash FROM users WHERE lower(email) = lower(%s)',
                   (email.strip(),), one=True)
    # Hash anyway when the user does not exist, so a missing account and a wrong
    # password take the same time and the endpoint cannot be used to enumerate emails.
    # A Google-only account has password_hash NULL. It must behave exactly like a
    # missing account — same message, same timing — or this endpoint reveals which
    # addresses signed up with Google.
    stored = (row['password_hash'] if row else None) or hash_password('no-such-user')
    if not verify_password(password, stored) or not row:
        return None
    db.query('UPDATE users SET last_login_at = now() WHERE id = %s', (row['id'],))
    return row


def sign_in_with_google(claims: dict) -> dict:
    """Find or make the account behind a set of verified Google claims.

    Returns {user_id, workspace_id, created, granted}. `claims` must already have been
    through oauth_google.validate() — this trusts email_verified, and says so loudly
    because case 2 below is where that trust is spent.

    The whole of case 3 is one transaction. A user row that commits without its
    workspace is an account that can log in and do nothing: current_workspace() raises
    403 for it, and nothing in the app can repair that without you doing it by hand.
    """
    import credits

    existing = db.query(
        'SELECT id, email FROM users WHERE google_sub = %s', (claims['sub'],), one=True)
    if existing:
        db.query('UPDATE users SET last_login_at = now() WHERE id = %s', (existing['id'],))
        return {'user_id': str(existing['id']), 'created': False, 'granted': 0,
                'workspace_id': _first_workspace(str(existing['id']))}

    # A password account with the same address. Linking rather than refusing is what
    # lets a hand-provisioned client switch to Google without losing their workspace.
    # Safe ONLY because Google has asserted the address is verified — without that check
    # upstream, registering the address anywhere would inherit someone else's workspace.
    by_email = db.query('SELECT id FROM users WHERE lower(email) = lower(%s)',
                        (claims['email'],), one=True)
    if by_email:
        db.query('UPDATE users SET google_sub = %s, last_login_at = now() '
                 'WHERE id = %s', (claims['sub'], by_email['id']))
        return {'user_id': str(by_email['id']), 'created': False, 'granted': 0,
                'workspace_id': _first_workspace(str(by_email['id']))}

    name = claims['name'] or claims['email'].split('@')[0]
    with db.tx() as conn:
        user_id = str(conn.execute(
            """INSERT INTO users (email, password_hash, name, google_sub, last_login_at)
               VALUES (%s, NULL, %s, %s, now()) RETURNING id""",
            (claims['email'], claims['name'] or None, claims['sub'])).fetchone()[0])

        workspace_id = str(conn.execute(
            'INSERT INTO workspaces (name, billing_email) VALUES (%s, %s) RETURNING id',
            (name, claims['email'])).fetchone()[0])

        conn.execute(
            "INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, 'owner')",
            (user_id, workspace_id))

        # Over the daily cap the account is still created, with nothing in it. Refusing
        # the signup instead would turn a spend ceiling into a closed front door, and
        # the person on the other side of it is more likely a customer than an attacker.
        granted = 0
        if credits.free_trial_remaining_today(conn) > 0:
            credits.grant(workspace_id, credits.FREE_CREDITS, credits.WELCOME_NOTE,
                          key=f'welcome:{user_id}', conn=conn)
            granted = credits.FREE_CREDITS

    return {'user_id': user_id, 'workspace_id': workspace_id,
            'created': True, 'granted': granted}


def _first_workspace(user_id: str) -> str | None:
    spaces = workspaces_for(user_id)
    return str(spaces[0]['id']) if spaces else None


def workspaces_for(user_id: str) -> list[dict]:
    return db.query(
        """SELECT w.id, w.name, m.role FROM memberships m
             JOIN workspaces w ON w.id = m.workspace_id
            WHERE m.user_id = %s AND w.archived_at IS NULL
         ORDER BY w.name""", (user_id,))


# --- FastAPI dependencies ----------------------------------------------------------

def current_session(donna_session: str | None = Cookie(default=None)) -> dict:
    """Every authenticated route depends on this. 401 rather than a redirect: the
    frontend is JavaScript talking to /api, and it handles the redirect itself."""
    session = lookup(donna_session)
    if session is None:
        raise HTTPException(401, 'please sign in')
    return session


def current_workspace(session: dict) -> str:
    """The workspace id every query must be scoped by. Never trust a client-sent id."""
    if not session.get('workspace_id'):
        raise HTTPException(403, 'this account has no workspace yet')
    return str(session['workspace_id'])


def require_admin(session: dict) -> dict:
    if not session.get('is_admin'):
        raise HTTPException(403, 'admins only')
    return session


def demo() -> None:
    """Self-check: the crypto, which needs no database."""
    stored = hash_password('correct horse battery staple')
    assert verify_password('correct horse battery staple', stored)
    assert not verify_password('wrong', stored)

    # Same password twice must not produce the same hash, or the salt is not working.
    assert hash_password('x') != hash_password('x')

    # A malformed or empty stored hash is a failed login, never a crash.
    for junk in ('', 'nonsense', 'scrypt$bad', 'bcrypt$1$2$3$4$5', 'scrypt$a$b$c$d$e'):
        assert verify_password('x', junk) is False, junk

    # A Google-only account stores NULL. Password login against it must be a refusal,
    # not an AttributeError that 500s and tells an attacker the row exists.
    assert verify_password('x', None) is False

    # The cookie value must never be what we store.
    token = secrets.token_urlsafe(32)
    assert token_hash(token) != token and len(token_hash(token)) == 64

    assert 'scrypt' in stored and stored.count('$') == 5

    if os.environ.get('DATABASE_URL'):
        _check_google_signup()
    print('auth ok')


def _check_google_signup() -> None:
    """The signup path, against a real database. This one mints credits."""
    import uuid

    import credits

    db.migrate()
    tag = uuid.uuid4().hex[:8]
    claims = {'sub': f'sub-{tag}', 'email': f'g-{tag}@test', 'name': 'Test Jeweller'}
    made = []

    try:
        first = sign_in_with_google(claims)
        made.append(first)
        assert first['created'] is True
        assert first['granted'] == credits.FREE_CREDITS, first
        assert credits.balance(first['workspace_id']) == credits.FREE_CREDITS

        # Signing in again is not a second trial, however many times it happens.
        again = sign_in_with_google(claims)
        assert again['created'] is False and again['granted'] == 0
        assert again['workspace_id'] == first['workspace_id'], 'a second workspace'
        assert credits.balance(first['workspace_id']) == credits.FREE_CREDITS, \
            'signing in twice granted the trial twice'

        # Even a replayed grant with the same key must be a no-op at the ledger, which
        # is the guarantee that survives a bug in the caller.
        credits.grant(first['workspace_id'], credits.FREE_CREDITS, credits.WELCOME_NOTE,
                      key=f'welcome:{first["user_id"]}')
        assert credits.balance(first['workspace_id']) == credits.FREE_CREDITS, \
            'the welcome key was not idempotent'

        # A password account that later signs in with Google keeps its workspace.
        pw_email = f'pw-{tag}@test'
        pw_user = create_user(pw_email, 'a-password', 'Legacy Client')
        linked = sign_in_with_google({'sub': f'sub2-{tag}', 'email': pw_email.upper(),
                                      'name': 'Legacy Client'})
        made.append(linked)
        assert linked['created'] is False, 'linking made a duplicate account'
        assert linked['user_id'] == str(pw_user['id']), 'linked to the wrong account'
        assert linked['granted'] == 0, 'an existing account got a free trial'

        # That account has no workspace, so it must report none rather than invent one.
        assert linked['workspace_id'] is None

        # Password login must still work for it, and must not crash on the Google-only
        # account whose password_hash is NULL.
        assert authenticate(pw_email, 'a-password') is not None
        assert authenticate(claims['email'], 'anything') is None

        # The spend cap. Over it, signup still succeeds — with nothing in the account.
        # A cap that refused the signup would turn a budget into a closed door.
        ceiling = credits.MAX_FREE_GRANTS_PER_DAY
        credits.MAX_FREE_GRANTS_PER_DAY = 0
        try:
            capped = sign_in_with_google({'sub': f'sub3-{tag}',
                                          'email': f'capped-{tag}@test', 'name': 'Capped'})
            made.append(capped)
            assert capped['created'] is True, 'the cap refused a signup'
            assert capped['granted'] == 0, 'the cap did not stop the grant'
            assert credits.balance(capped['workspace_id']) == 0
            assert capped['workspace_id'], 'a capped signup got no workspace'
        finally:
            credits.MAX_FREE_GRANTS_PER_DAY = ceiling

        total, tail = credits.reconcile(first['workspace_id'])
        assert total == tail, (total, tail)
    finally:
        for entry in made:
            if entry.get('workspace_id'):
                db.query('DELETE FROM credit_ledger WHERE workspace_id = %s',
                         (entry['workspace_id'],))
                db.query('DELETE FROM memberships WHERE workspace_id = %s',
                         (entry['workspace_id'],))
                db.query('DELETE FROM workspaces WHERE id = %s', (entry['workspace_id'],))
            db.query('DELETE FROM sessions WHERE user_id = %s', (entry['user_id'],))
            db.query('DELETE FROM users WHERE id = %s', (entry['user_id'],))
        db.close()


if __name__ == '__main__':
    demo()
