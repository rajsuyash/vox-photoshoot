"""Account token security check on a new disposable local PostgreSQL database."""
import os
os.environ['NOTIFICATIONS_ENABLED'] = '1'  # sender is stubbed; no mail leaves this check
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

import db


def main():
    base = os.environ.get('ADMIN_TEST_DSN', '')
    url = urlsplit(base)
    assert url.hostname in {'localhost', '127.0.0.1', '::1'}
    name = 'vox_account_test_' + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ.update(DATABASE_URL=urlunsplit(url._replace(path='/' + name)), ACCOUNT_LINK_KEY='a'*64)
    try:
        db.migrate()
        import auth
        import account_actions as a
        from fastapi.testclient import TestClient
        from app import app
        client = TestClient(app)
        assert client.get('/account.html').status_code == 200
        assert client.post('/api/account/recovery', data={'email': 'missing@example.com'},
                           headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/api/admin/customers').status_code == 401
        uid = str(auth.create_user('owner@example.com', 'old password')['id'])
        session = auth.start_session(uid, None)
        a.request_reset('owner@example.com', 'local')
        action = db.query("SELECT id,token_hash FROM account_actions WHERE kind='reset'", one=True)
        raw = a.delivery(str(action['id']))
        assert action['token_hash'] != raw
        notification = db.query("SELECT payload FROM notifications WHERE kind='reset'", one=True)
        assert raw not in str(notification), 'reset secret leaked into outbox'
        def consume(_):
            try:
                return a.accept(raw, 'new secure password')['ok']
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            assert sum(pool.map(consume, range(2))) == 1
        assert not auth.lookup(session)
        assert auth.authenticate('owner@example.com', 'new secure password')
        assert not auth.authenticate('owner@example.com', 'old password')
        google = db.query("INSERT INTO users(email,password_hash,google_sub) VALUES('google@example.com',NULL,'sub') RETURNING id", one=True)['id']
        for email in ['missing@example.com', 'google@example.com', 'owner@example.com']:
            for _ in range(4):
                a.request_reset(email, email)
        assert not db.query('SELECT id FROM account_actions WHERE user_id=%s', (google,))
        assert len(db.query("SELECT id FROM account_actions WHERE kind='reset'")) <= 4
        wid = str(db.query("INSERT INTO workspaces(name) VALUES('invite') RETURNING id", one=True)['id'])
        with db.tx() as conn:
            invite = a.issue(conn, 'invite', uid, 'owner@example.com', workspace_id=wid, role='member')
        raw = a.delivery(invite)
        try:
            a.accept(raw, 'hijack password')
            raise AssertionError('existing invitation bypassed login')
        except PermissionError:
            pass
        a.accept(raw, session={'user_id': uid})
        assert auth.authenticate('owner@example.com', 'new secure password'), 'invite overwrote existing password'
        assert db.query('SELECT role FROM memberships WHERE user_id=%s AND workspace_id=%s', (uid,wid), one=True)['role'] == 'member'
        import support
        admin = str(auth.create_user('admin@example.com', 'admin password', is_admin=True)['id'])
        client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))
        assert client.get('/api/admin/customers').status_code == 403
        client.cookies.set(auth.COOKIE, auth.start_session(admin, None))
        db.query('UPDATE users SET mfa_secret=%s WHERE id=%s', (b'test-ciphertext', admin))
        db.query("UPDATE sessions SET mfa_verified_until=now()+interval '15 minutes' WHERE user_id=%s", (admin,))
        assert client.get('/api/admin/customers?q=owner').json()[0]['email'] == 'owner@example.com'
        new_ws = str(uuid.uuid4())
        request = {'request_key': str(uuid.uuid4()), 'action': 'provision', 'target_id': new_ws,
                   'reason': 'new customer setup', 'data': {'name': 'New Brand', 'gstin': '',
                       'owner_email': 'new@example.com', 'billing_email': '', 'credits': 6}}
        response = client.post('/api/admin/action', json=request)
        assert response.status_code == 200, response.text
        assert client.post('/api/admin/action', json=request).json() == response.json()
        invite_token = a.delivery(response.json()['invite_id'])
        assert client.post('/api/account/accept', data={'token': invite_token,
                            'password': 'new owner password'}).status_code == 200
        assert auth.authenticate('new@example.com', 'new owner password')
        assert client.post('/api/account/accept', data={'token': invite_token,
                            'password': 'new owner password'}).status_code == 400
        assert client.get('/api/admin/workspace/' + new_ws).json()['balance'] == 6
        db.query("UPDATE account_actions SET expires_at=now()-interval '1 minute' WHERE kind='reset'")
        reset = db.query("SELECT id FROM account_actions WHERE kind='reset' ORDER BY created_at DESC LIMIT 1", one=True)
        try:
            a.accept(a.token(reset['id'], 'reset', uid), 'not permitted')
            raise AssertionError('expired link accepted')
        except ValueError:
            pass
        print('account actions ok: single-use races, expiry, session revocation, Google-only exclusion, invite ownership')
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
