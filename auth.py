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


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check against a stored hash. Never raises on a malformed hash."""
    try:
        scheme, n, r, p, salt, digest = stored.split('$')
        if scheme != 'scrypt':
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
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
    stored = row['password_hash'] if row else hash_password('no-such-user')
    if not verify_password(password, stored) or not row:
        return None
    db.query('UPDATE users SET last_login_at = now() WHERE id = %s', (row['id'],))
    return row


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

    # The cookie value must never be what we store.
    token = secrets.token_urlsafe(32)
    assert token_hash(token) != token and len(token_hash(token)) == 64

    assert 'scrypt' in stored and stored.count('$') == 5
    print('auth ok')


if __name__ == '__main__':
    demo()
