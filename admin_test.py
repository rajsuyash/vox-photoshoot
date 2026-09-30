"""Real PostgreSQL / HTTP security check, only a new disposable local database."""
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import db


def main():
    base = os.environ.get('ADMIN_TEST_DSN', '')
    url = urlsplit(base)
    assert url.hostname in {'localhost', '127.0.0.1', '::1'}, 'local test DSN required'
    name = 'vox_admin_test_' + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ['DATABASE_URL'] = urlunsplit(url._replace(path='/' + name))
    try:
        db.migrate()
        assert db.migrate() == []
        import auth
        import support
        from app import app
        admin = str(auth.create_user('admin@example.com', 'long password', is_admin=True)['id'])
        uid = str(auth.create_user('member@example.com', 'long password')['id'])
        wid = str(db.query("INSERT INTO workspaces(name) VALUES('private') RETURNING id", one=True)['id'])
        db.query("INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,'owner'),(%s,%s,'member')",
                 (admin, wid, uid, wid))
        token = auth.start_session(uid, wid)
        client = TestClient(app)
        client.cookies.set(auth.COOKIE, token)
        assert client.get('/api/pieces').status_code == 200
        assert client.get('/api/admin/workspaces').status_code == 403
        db.query('DELETE FROM memberships WHERE user_id=%s AND workspace_id=%s', (uid, wid))
        assert client.get('/api/pieces').status_code == 401, 'revoked membership still reads products'
        db.query("INSERT INTO memberships(user_id,workspace_id) VALUES(%s,%s)", (uid, wid))
        key = str(uuid.uuid4())
        args = (admin, key, 'suspend_user', uid, 'support security check')
        assert support.change(*args, suspended=True) == {'suspended': True}
        assert support.change(*args, suspended=True) == {'suspended': True}
        assert db.query('SELECT count(*) AS n FROM admin_actions', one=True)['n'] == 1
        assert not auth.lookup(token)
        assert not auth.authenticate('member@example.com', 'long password')
        try:
            auth.sign_in_with_google({'sub': 'test-sub', 'email': 'member@example.com', 'name': 'M'})
            raise AssertionError('Google bypassed suspension')
        except Exception as error:
            assert getattr(error, 'status_code', None) == 403
        def refused(actor, action, target, **data):
            try:
                support.change(actor, str(uuid.uuid4()), action, target, 'security test', **data)
                raise AssertionError('unsafe action accepted')
            except (ValueError, PermissionError):
                pass
        refused(admin, 'suspend_user', admin, suspended=True)
        refused(admin, 'member', wid, user_id=admin, role='remove')
        refused(uid, 'suspend_workspace', wid, suspended=True)
        support.change(admin, str(uuid.uuid4()), 'suspend_user', uid, 'restore account', suspended=False)
        assert auth.authenticate('member@example.com', 'long password')
        token = auth.start_session(uid, wid)
        support.change(admin, str(uuid.uuid4()), 'suspend_workspace', wid, 'pause account', suspended=True)
        assert not auth.lookup(token)
        assert not auth.workspaces_for(uid)
        assert db.query('SELECT count(*) AS n FROM admin_actions', one=True)['n'] == 3
        support.change(admin, str(uuid.uuid4()), 'suspend_workspace', wid, 'restore workspace', suspended=False)
        client.cookies.set(auth.COOKIE, auth.start_session(admin, wid))
        # This fixture represents completed step-up; the real enrollment is checked separately.
        db.query('UPDATE users SET mfa_secret=%s WHERE id=%s', (b'test-ciphertext', admin))
        db.query("UPDATE sessions SET mfa_verified_until=now()+interval '15 minutes' WHERE user_id=%s", (admin,))
        request = {'request_key': str(uuid.uuid4()), 'action': 'credit_adjustment', 'target_id': wid,
                   'reason': 'customer goodwill', 'data': {'amount': 20, 'confirm_debit': False}}
        assert client.post('/api/admin/action', json=request).json()['balance'] == 20
        assert client.post('/api/admin/action', json=request).json()['balance'] == 20
        assert db.query("SELECT count(*) AS n FROM credit_ledger WHERE kind='grant'", one=True)['n'] == 1
        request['data']['amount'] = 25
        assert client.post('/api/admin/action', json=request).status_code == 400
        request.update(request_key=str(uuid.uuid4()), data={'amount': -5, 'confirm_debit': False})
        assert client.post('/api/admin/action', json=request).status_code == 400
        request['data']['confirm_debit'] = True
        assert client.post('/api/admin/action', json=request).json()['balance'] == 15
        assert client.get('/api/admin/workspace/' + wid).json()['balance'] == 15
        assert client.get('/api/admin/reconcile/' + wid).json()['ledger_matches']
        assert client.get('/api/admin/summary').json()['credits_consumed'] == 0
        assert client.get('/api/admin/jobs').status_code == 200
        import jobs
        job = jobs.create(wid, admin, 'shoot', 'admin-inspection', {})
        jid = str(job['id'])
        db.query("UPDATE jobs SET status='failed',error='secret diagnostic' WHERE id=%s", (jid,))
        db.query("INSERT INTO job_images(job_id,shoot_id,framing,s3_key) VALUES(%s,%s,'closeup','test/output.jpg')", (jid,jid))
        response = client.get('/api/admin/job/' + jid)
        assert response.status_code == 200 and response.json()['outputs'][0]['key'] == 'test/output.jpg'
        assert 'secret diagnostic' not in response.text
        assert client.post('/api/admin/action', json={**request, 'action':'invite', 'data': {'email':'invite@example.com','role':'member'}}).status_code == 503
        db.query('UPDATE sessions SET mfa_verified_until=NULL WHERE user_id=%s', (admin,))
        assert client.get('/api/admin/job/' + jid).status_code == 403
        assert client.get('/api/campaigns').status_code == 403
        db.query("UPDATE sessions SET mfa_verified_until=now()+interval '15 minutes' WHERE user_id=%s", (admin,))
        assert client.get('/api/admin/notifications').status_code == 200
        from concurrent.futures import ThreadPoolExecutor
        import credits
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda i: credits.grant(wid,1,'parallel adjustment',key='parallel:'+str(i)),range(20)))
        assert credits.balance(wid)==35
        assert credits.reconcile(wid)==(35,35)
        client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))
        for path in ['/api/admin/customers', '/api/admin/workspace/' + wid,
                     '/api/admin/reconcile/' + wid, '/api/admin/jobs', '/api/admin/notifications', '/api/admin/job/' + jid]:
            assert client.get(path).status_code == 403, path
        print('admin security ok: membership revocation, suspension, last-owner/admin guards, audit replay')
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
