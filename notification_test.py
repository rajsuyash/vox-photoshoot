"""Notification integration check. Creates/drops ONLY a new local test database.

NOTIFICATION_TEST_DSN=postgresql://postgres@localhost:55439/postgres?sslmode=disable \
    .venv/bin/python notification_test.py
No email is sent: the SES client is stubbed, PostgreSQL is real.
"""
import concurrent.futures
import contextlib
import os
import threading
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from botocore.exceptions import ClientError

import db


def main():
    base = os.environ.get('NOTIFICATION_TEST_DSN', '')
    url = urlsplit(base)
    assert url.hostname in {'localhost', '127.0.0.1', '::1'}, 'local test DSN required'
    name = 'vox_notification_test_' + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ['DATABASE_URL'] = urlunsplit(url._replace(path='/' + name))
    try:
        # Initialize the pool before launching two migration contenders.
        db.pool()
        barrier = threading.Barrier(2)
        def migrate(_):
            barrier.wait()
            return db.migrate()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            applied = list(ex.map(migrate, range(2)))
        assert sum(map(len, applied)) == len(list(db.MIGRATIONS.glob('*.sql')))
        assert db.migrate() == []

        import notifications as n
        import notification_worker as worker
        workspace = db.query("INSERT INTO workspaces(name) VALUES('notification test') "
                             'RETURNING id', one=True)['id']
        def enqueue(key, recipient='test@example.com', **payload):
            with db.tx() as conn:
                return n.enqueue(conn, key, 'welcome', recipient,
                                 {'name': '<script>alert(1)</script>', 'credits': 6, **payload},
                                 workspace_id=workspace)
        def row(ident):
            return db.query('SELECT * FROM notifications WHERE id=%s', (ident,), one=True)
        def due(ident):
            db.query("UPDATE notifications SET next_attempt_at=now()-interval '1 minute' "
                     'WHERE id=%s', (ident,))

        first = enqueue('welcome:one')
        assert enqueue('welcome:one') == first
        with contextlib.suppress(RuntimeError):
            with db.tx() as conn:
                n.enqueue(conn, 'rollback', 'welcome', 'test@example.com', {'credits': 0})
                raise RuntimeError('transaction rollback')
        assert db.query("SELECT id FROM notifications WHERE event_key='rollback'") == []
        for address in ['test@example.com\r\nBcc:evil@example.com', 'a,b@example.com', 'bad']:
            try:
                enqueue('bad:' + uuid.uuid4().hex, address)
                raise AssertionError('unsafe address accepted')
            except ValueError:
                pass
        rendered = n.render('welcome', {'name': '<script>', 'credits': 0})
        assert '<script>' not in rendered['html'] and '&lt;script&gt;' in rendered['html']
        assert '0 credits' in rendered['text']
        # A new Google account gets one welcome, including the actual capped grant.
        import auth
        claims = {'sub': 'notification-signup', 'email': 'signup@example.com', 'name': 'Signup'}
        created = auth.sign_in_with_google(claims)
        auth.sign_in_with_google(claims)
        welcome = db.query('SELECT * FROM notifications WHERE event_key=%s',
                           ('welcome:' + created['user_id'],), one=True)
        assert welcome['payload']['credits'] == created['granted']
        assert db.query('SELECT count(*) AS n FROM notifications WHERE user_id=%s',
                        (created['user_id'],), one=True)['n'] == 1
        # Remove this fixture from the dispatch queue; subsequent checks control each claim.
        db.query("UPDATE notifications SET status='suppressed' WHERE id=%s", (welcome['id'],))
        import subscriptions
        import billing
        db.query('UPDATE workspaces SET billing_email=%s WHERE id=%s', ('accounts@example.com', workspace))
        db.query("INSERT INTO subscriptions(id,workspace_id,plan_id,pack,credits,amount_paise,status) "
                 "VALUES('sub_receipts',%s,'plan_test','starter',30,105000,'active')", (workspace,))
        subscription = db.query("SELECT * FROM subscriptions WHERE id='sub_receipts'", one=True)
        payment = {'id': 'pay_receipts', 'invoice_id': 'inv_receipts', 'status': 'captured',
                   'currency': 'INR', 'amount': 105000}
        invoice = {'id': 'inv_receipts', 'payment_id': 'pay_receipts', 'subscription_id': 'sub_receipts',
                   'status': 'paid', 'gross_amount': 105000}
        with db.tx() as conn:
            conn.execute('SELECT id FROM workspaces WHERE id=%s FOR UPDATE', (workspace,))
            subscriptions._credit(conn, subscription, payment, invoice)
            subscriptions._credit(conn, subscription, payment, invoice)
        receipt = db.query("SELECT * FROM notifications WHERE event_key='receipt:pay_receipts'", one=True)
        assert receipt['recipient'] == 'accounts@example.com'
        assert receipt['payload']['credits'] == 30 and receipt['payload']['balance'] == 30
        assert receipt['payload']['plan'] == 'Starter'
        assert db.query("SELECT count(*) AS n FROM notifications WHERE kind='receipt'", one=True)['n'] == 1
        payment = dict(payment, id='pay_reversed', invoice_id='inv_reversed',
                       status='refunded', amount_refunded=105000)
        invoice = dict(invoice, id='inv_reversed', payment_id='pay_reversed')
        with db.tx() as conn:
            conn.execute('SELECT id FROM workspaces WHERE id=%s FOR UPDATE', (workspace,))
            subscriptions._credit(conn, subscription, payment, invoice)
            subscriptions._credit(conn, subscription, payment, invoice)
        assert not db.query("SELECT id FROM notifications WHERE event_key='receipt:pay_reversed'")
        assert db.query("SELECT count(*) AS n FROM notifications WHERE kind='refund'", one=True)['n'] == 1
        # Exclude financial fixtures from subsequent lease/dispatcher ordering checks.
        db.query("UPDATE notifications SET status='suppressed' WHERE kind IN ('receipt','refund')")
        try:
            n.render('receipt', {'credits': 30, 'balance': 30, 'amount_paise': 123900,
                                'invoice_url': 'javascript:alert(1)'})
            raise AssertionError('unsafe invoice URL accepted')
        except ValueError:
            pass

        # A leased row is claimed once across concurrent consumers.
        def claim(_):
            return n.claim()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            claims = list(ex.map(claim, range(2)))
        assert sum(bool(x) for x in claims) == 1
        leased = next(x for x in claims if x)
        assert leased['id'] == first
        db.query("UPDATE notifications SET lease_until=now()-interval '1 second' WHERE id=%s",
                 (first,))
        recovered = n.claim()
        assert recovered['id'] == first and recovered['lease_token'] != leased['lease_token']
        assert not n.accepted(leased, 'stale-worker')
        assert n.accepted(recovered, 'ses-first')
        assert row(first)['status'] == 'accepted'
        assert n.claim() is None
        assert n.feedback('ses-first', 'Delivery')
        assert row(first)['status'] == 'delivered'
        with patch.dict(os.environ, {'NOTIFICATION_TOPIC_ARN': 'arn:test:feedback'}):
            event = {'Records': [{'EventSource': 'aws:sns', 'Sns': {'TopicArn': 'arn:test:feedback',
                     'Message': '{"eventType":"Delivery","mail":{"messageId":"ses-first"}}'}}]}
            assert worker.handler(event, None) == {'feedback': 1}
            event['Records'][0]['Sns']['TopicArn'] = 'arn:wrong:topic'
            try:
                worker.handler(event, None)
                raise AssertionError('wrong feedback topic accepted')
            except ValueError:
                pass
        assert not n.feedback('ses-first', 'Send')
        assert n.feedback('ses-first', 'Complaint')
        assert row(first)['status'] == 'complained'
        suppressed = enqueue('suppressed')
        assert row(suppressed)['status'] == 'suppressed'
        early = enqueue('early-feedback', 'early@example.com')
        early_claim = n.claim()
        assert early_claim['id'] == early
        assert not n.feedback('ses-early', 'Delivery')
        assert n.accepted(early_claim, 'ses-early')
        assert row(early)['status'] == 'delivered'
        assert not n.feedback('ses-early', 'Delivery')
        transient = enqueue('transient-bounce', 'transient@example.com')
        assert n.accepted(n.claim(), 'ses-transient')
        assert n.feedback('ses-transient', 'Bounce', permanent=False)
        assert row(transient)['status'] == 'bounced'
        assert db.query("SELECT recipient FROM notification_suppressions "
                        "WHERE recipient='transient@example.com'") == []
        assert not n.feedback('ses-transient', 'Bounce', permanent=True)
        assert db.query("SELECT recipient FROM notification_suppressions "
                        "WHERE recipient='transient@example.com'")

        second = enqueue('retry', 'retry@example.com')
        errors = ClientError({'Error': {'Code': 'TooManyRequestsException',
                                      'Message': 'sensitive response not stored'}}, 'SendEmail')
        class SES:
            failure = errors
            sent = []
            def send_email(self, **request):
                if self.failure:
                    raise self.failure
                self.sent.append(request)
                return {'MessageId': 'ses-retry'}
        ses = SES()
        env = {'NOTIFICATIONS_ENABLED': '1', 'NOTIFICATION_FROM': 'notifications@voxdonna.com',
               'NOTIFICATION_REPLY_TO': 'suyash@voxdonna.com',
               'NOTIFICATION_CONFIGURATION_SET': 'photoshoot'}
        with patch.dict(os.environ, env), patch('notification_worker.ses_client', return_value=ses):
            assert worker.dispatch(limit=1)['retry'] == 1
            assert row(second)['status'] == 'queued'
            assert row(second)['last_error'] == 'TooManyRequestsException'
            due(second)
            ses.failure = None
            assert worker.dispatch(limit=1)['accepted'] == 1
            assert ses.sent[0]['EmailTags'][0]['Value'] == str(second)
            assert row(second)['status'] == 'accepted'
            assert worker.dispatch(limit=1)['accepted'] == 0
            third = enqueue('permanent', 'reject@example.com')
            ses.failure = ClientError({'Error': {'Code': 'MessageRejected'}}, 'SendEmail')
            assert worker.dispatch(limit=1)['failed'] == 1
            assert row(third)['status'] == 'failed'
            fourth = enqueue('exhausted', 'exhausted@example.com')
            db.query('UPDATE notifications SET attempts=%s WHERE id=%s', (n.MAX_ATTEMPTS-1, fourth))
            ses.failure = errors
            assert worker.dispatch(limit=1)['failed'] == 1
            assert row(fourth)['status'] == 'failed'
            enqueue('disabled', 'disabled@example.com')
            with patch.dict(os.environ, {'NOTIFICATIONS_ENABLED': '0'}):
                assert worker.dispatch()['disabled']
            assert db.query("SELECT attempts FROM notifications WHERE event_key='disabled'",
                            one=True)['attempts'] == 0
        print('notifications ok: real PostgreSQL migration/queue/concurrency/recovery checks; '
              'stubbed SES retry/failure; no email sent')
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
