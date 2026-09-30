"""Data-export isolation and deletion-request check, only a new local database."""
import os
import uuid
from urllib.parse import urlsplit,urlunsplit

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import db


def main():
    base=os.environ.get('ADMIN_TEST_DSN','');url=urlsplit(base)
    assert url.hostname in {'localhost','127.0.0.1','::1'}
    name='vox_data_test_'+uuid.uuid4().hex[:12]
    with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ['DATABASE_URL']=urlunsplit(url._replace(path='/'+name))
    try:
        db.migrate()
        import auth
        from app import app
        uid=str(auth.create_user('owner@example.com','long password')['id'])
        member=str(auth.create_user('member@example.com','long password')['id'])
        wid=str(db.query("INSERT INTO workspaces(name,billing_email) VALUES('My Brand','owner@example.com') RETURNING id",one=True)['id'])
        other=str(db.query("INSERT INTO workspaces(name,billing_email) VALUES('Other Secret Brand','secret@example.com') RETURNING id",one=True)['id'])
        db.query("INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,'owner'),(%s,%s,'member')",(uid,wid,member,wid))
        client=TestClient(app);client.cookies.set(auth.COOKIE,auth.start_session(uid,wid))
        response=client.get('/api/account/export')
        assert response.status_code==200,response.text
        assert 'Other Secret' not in response.text and 'secret@example.com' not in response.text
        assert 'password_hash' not in response.text and 'token_hash' not in response.text
        assert client.get('/api/admin/export/'+other).status_code==403
        body={'request_key':str(uuid.uuid4()),'reason':'Close my workspace','confirm_name':'wrong'}
        assert client.post('/api/account/deletion',data=body).status_code==400
        body['confirm_name']='My Brand'
        first=client.post('/api/account/deletion',data=body)
        assert first.status_code==200 and first.json()['purge_enabled'] is False
        assert client.post('/api/account/deletion',data=body).json()==first.json()
        assert db.query('SELECT count(*) AS n FROM data_requests',one=True)['n']==1
        client.cookies.set(auth.COOKIE,auth.start_session(member,wid))
        assert client.get('/api/account/export').status_code==403
        assert client.post('/api/account/deletion',data=body).status_code==403
        assert db.query('SELECT name FROM workspaces WHERE id=%s',(wid,),one=True)['name']=='My Brand'
        db.query('UPDATE users SET is_admin=true,mfa_secret=%s WHERE id=%s',(b'fixture',uid))
        admin_token=auth.start_session(uid,wid)
        db.query("UPDATE sessions SET mfa_verified_until=now()+interval '15 minutes' WHERE token_hash=%s",(auth.token_hash(admin_token),))
        client.cookies.set(auth.COOKIE,admin_token)
        assert client.get('/api/admin/export/'+wid).status_code==200
        audit_count=db.query('SELECT count(*) AS n FROM admin_actions',one=True)['n']
        assert client.get('/api/admin/export/'+str(uuid.uuid4())).status_code==404
        assert db.query('SELECT count(*) AS n FROM admin_actions',one=True)['n']==audit_count
        action={'request_key':str(uuid.uuid4()),'action':'review_deletion','target_id':first.json()['id'],
                'reason':'retain financial records pending policy','data':{'status':'rejected'}}
        assert client.post('/api/admin/action',json=action).json()['status']=='rejected'
        assert client.post('/api/admin/action',json=action).json()['purge_enabled'] is False
        assert client.post('/api/account/deletion',data=body).json()['status']=='rejected'
        action.update(request_key=str(uuid.uuid4()),data={'status':'purged'})
        assert client.post('/api/admin/action',json=action).status_code==400
        assert db.query("SELECT count(*) AS n FROM admin_actions WHERE outcome='failed'",one=True)['n']==1
        print('data controls ok: owner-only export, tenant isolation, secret exclusion, idempotent request, no purge')
    finally:
        db.close()
        with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__=='__main__':main()
