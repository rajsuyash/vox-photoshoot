"""RFC 6238 step-up with KMS-encrypted secrets, single-use recovery and DB throttling."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import boto3
from psycopg.rows import dict_row

import auth
import db


def kms():
    return boto3.client('kms', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))


def code(secret, counter):
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f'{(int.from_bytes(digest[offset:offset+4], "big") & 0x7fffffff) % 1000000:06d}'


def _context(user_id):
    return {'application': 'vox-photoshoot-admin', 'user_id': str(user_id)}


def status(session):
    row = db.query('SELECT mfa_secret IS NOT NULL AS configured FROM users WHERE id=%s',
                   (session['user_id'],), one=True)
    return {'configured': row['configured'], 'verified': bool(row['configured'] and
            session.get('mfa_verified_until') and session['mfa_verified_until'] > datetime.now(timezone.utc))}


def enroll(session):
    if session['session_created_at'] < datetime.now(timezone.utc) - timedelta(minutes=5):
        raise ValueError('sign out and sign in again before enrolling an authenticator')
    if status(session)['configured']:
        auth.require_admin(session)
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    encrypted = kms().encrypt(KeyId=os.environ['ADMIN_KMS_KEY'], Plaintext=secret.encode(),
                               EncryptionContext=_context(session['user_id']))['CiphertextBlob']
    db.query('UPDATE users SET mfa_pending=%s,mfa_pending_until=now()+interval \'10 minutes\' WHERE id=%s',
             (encrypted, session['user_id']))
    label = quote('Donna Photoshoot:' + session['email'], safe='')
    return {'secret': secret, 'uri': f'otpauth://totp/{label}?secret={secret}&issuer=Donna%20Photoshoot&digits=6&period=30'}


def verify(session, value, *, enrollment=False):
    """Return failure after committing its counter; never rollback the throttle."""
    success = False
    recovery = []
    with db.tx() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            user = cur.execute('SELECT * FROM users WHERE id=%s AND is_admin AND suspended_at IS NULL FOR UPDATE',
                               (session['user_id'],)).fetchone()
        now = datetime.now(timezone.utc)
        if not user or (user['mfa_locked_until'] and user['mfa_locked_until'] > now):
            raise ValueError('authentication temporarily locked; try again later')
        encrypted = user['mfa_pending'] if enrollment else user['mfa_secret']
        expired = enrollment and (not user['mfa_pending_until'] or user['mfa_pending_until'] <= now)
        if encrypted and not expired and len(value) == 6 and value.isascii() and value.isdigit():
            secret = kms().decrypt(CiphertextBlob=bytes(encrypted), EncryptionContext=_context(user['id']))['Plaintext'].decode()
            current = int(time.time()) // 30
            matched = next((c for c in (current-1,current,current+1)
                            if c > (user['mfa_last_counter'] if not enrollment else -1)
                            and hmac.compare_digest(code(secret,c), value)), None)
            if matched is not None:
                success = True
                conn.execute('UPDATE users SET mfa_last_counter=%s WHERE id=%s', (matched,user['id']))
        elif not enrollment and encrypted and 20 <= len(value) <= 80:
            success = bool(conn.execute('DELETE FROM admin_recovery_codes WHERE user_id=%s AND code_hash=%s RETURNING user_id',
                                        (user['id'], auth.token_hash(value.strip()))).fetchone())
        if success:
            if enrollment:
                conn.execute('UPDATE users SET mfa_secret=mfa_pending,mfa_pending=NULL,mfa_pending_until=NULL WHERE id=%s', (user['id'],))
                conn.execute('DELETE FROM admin_recovery_codes WHERE user_id=%s', (user['id'],))
                recovery = [secrets.token_hex(16) for _ in range(10)]
                for raw in recovery:
                    conn.execute('INSERT INTO admin_recovery_codes(user_id,code_hash) VALUES(%s,%s)', (user['id'],auth.token_hash(raw)))
                conn.execute('DELETE FROM sessions WHERE user_id=%s AND token_hash<>%s', (user['id'],session['token_hash']))
            conn.execute('UPDATE users SET mfa_failures=0,mfa_locked_until=NULL WHERE id=%s', (user['id'],))
            conn.execute('UPDATE sessions SET mfa_verified_until=now()+interval \'15 minutes\' WHERE token_hash=%s', (session['token_hash'],))
        else:
            conn.execute('UPDATE users SET mfa_failures=CASE WHEN mfa_locked_until<now() THEN 1 ELSE mfa_failures+1 END,'
                         'mfa_locked_until=CASE WHEN mfa_failures>=4 AND NOT COALESCE(mfa_locked_until<now(),false) '
                         'THEN now()+interval \'15 minutes\' ELSE NULL END WHERE id=%s', (user['id'],))
    if not success:
        raise ValueError('invalid, expired or already used code')
    return {'verified': True, 'recovery_codes': recovery}
