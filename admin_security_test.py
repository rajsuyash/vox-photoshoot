"""Real DB MFA gate/replay/recovery check; only KMS is stubbed. No production changes."""
import hashlib
import os
import time
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import db


def main():
    base=os.environ.get('ADMIN_TEST_DSN','');url=urlsplit(base)
    assert url.hostname in {'localhost','127.0.0.1','::1'}
    name='vox_mfa_test_'+uuid.uuid4().hex[:12]
    with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ.update(DATABASE_URL=urlunsplit(url._replace(path='/'+name)),ADMIN_KMS_KEY='test-key')
    try:
        db.migrate()
        import auth
        import admin_security as security
        from app import app
        class KMS:
            values={}
            def encrypt(self,**r):
                cipher=hashlib.sha256(r['Plaintext']).digest();self.values[cipher]=(r['Plaintext'],r['EncryptionContext']);return {'CiphertextBlob':cipher}
            def decrypt(self,**r):
                value,context=self.values[r['CiphertextBlob']];assert context==r['EncryptionContext'];return {'Plaintext':value}
        uid=str(auth.create_user('admin@example.com','admin password',is_admin=True)['id'])
        raw=auth.start_session(uid,None);old=auth.start_session(uid,None)
        client=TestClient(app);client.cookies.set(auth.COOKIE,raw)
        assert client.get('/api/admin/customers').status_code==403
        # RFC 6238 SHA-1 example (8 digits -> last 6 digits).
        assert security.code('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ',1)=='287082'
        with patch.object(security,'kms',return_value=KMS()):
            enrolled=client.post('/api/admin/security/enroll').json()
            otp=security.code(enrolled['secret'],int(time.time())//30)
            response=client.post('/api/admin/security/verify',data={'code':otp,'enrollment':'true'})
            assert response.status_code==200,response.text
            recovery=response.json()['recovery_codes'];assert len(recovery)==10
            assert not auth.lookup(old)
            assert client.get('/api/admin/customers').status_code==200
            assert client.post('/api/admin/security/verify',data={'code':otp}).status_code==400,'OTP replay accepted'
            db.query("UPDATE sessions SET mfa_verified_until=now()-interval '1 minute' WHERE token_hash=%s",(auth.token_hash(raw),))
            assert client.get('/api/admin/customers').status_code==403
            assert client.post('/api/admin/security/verify',data={'code':recovery[0]}).status_code==200
            assert client.post('/api/admin/security/verify',data={'code':recovery[0]}).status_code==400
            for _ in range(5):assert client.post('/api/admin/security/verify',data={'code':'bad'}).status_code==400
            assert client.post('/api/admin/security/verify',data={'code':recovery[1]}).status_code==400
        print('admin MFA ok: RFC vector, mandatory gate, enrollment, replay, expiry, recovery, lockout')
    finally:
        db.close()
        with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__=='__main__':main()
