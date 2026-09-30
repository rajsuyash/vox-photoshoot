"""Monthly Razorpay mandates. Credits come from captured, paid cycle invoices.

DATABASE_URL=postgresql://postgres@127.0.0.1:55439/donna_campaign_test?sslmode=disable \
    .venv/bin/python subscriptions.py  # real database, fake gateway; no payments
"""
import hashlib
import hmac
import os
import time

from psycopg.rows import dict_row

import billing
import credits
import db
import notifications

TERMINAL = {'cancelled', 'completed', 'expired'}
EVENTS = {f'subscription.{name}' for name in (
    'authenticated', 'activated', 'charged', 'pending', 'halted', 'paused',
    'resumed', 'cancelled', 'completed', 'updated')}


def _latest(conn, workspace_id):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM subscriptions WHERE workspace_id=%s '
                           'ORDER BY created_at DESC LIMIT 1', (workspace_id,)).fetchone()


def _sync(conn, row, remote):
    if remote['id'] != row['id'] or remote['plan_id'] != row['plan_id']:
        raise RuntimeError('Razorpay subscription does not match the stored mandate')
    conn.execute('UPDATE subscriptions SET status=%s, charge_at=%s, updated_at=now() '
                 'WHERE id=%s', (remote['status'], remote.get('charge_at'), row['id']))
    row.update(status=remote['status'], charge_at=remote.get('charge_at'))


def current(workspace_id, refresh=False):
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (workspace_id,))
        row = _latest(conn, workspace_id)
        if row is None:
            return None
        if refresh and row['status'] not in TERMINAL:
            _sync(conn, row, billing._client().subscription.fetch(row['id']))
        paid = conn.execute("SELECT count(*) FROM invoices WHERE razorpay_subscription_id=%s "
                            "AND status='paid'", (row['id'],)).fetchone()[0]
        return {key: row[key] for key in ('id', 'pack', 'credits', 'amount_paise',
                                         'status', 'charge_at')} | {'credited_payments': paid}


def checkout(workspace_id, pack):
    if pack not in billing.PACKS:
        raise ValueError('no such plan')
    client = billing._client()
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (workspace_id,))
        row = _latest(conn, workspace_id)
        if row and row['status'] not in TERMINAL:
            _sync(conn, row, client.subscription.fetch(row['id']))
            if row['status'] not in TERMINAL:
                if row['pack'] != pack:
                    raise ValueError('cancel the current subscription before choosing another plan')
                if row['status'] != 'created':
                    raise ValueError('this workspace already has a subscription')
                return {'subscription_id': row['id'], 'credits': row['credits']}

        # PostgreSQL coordinates plan creation across workers and workspaces.
        key_id = os.environ['RAZORPAY_KEY_ID']
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                     (f'razorpay-plan:{key_id}:{pack}',))
        plan = conn.execute('SELECT plan_id, credits, amount_paise FROM subscription_plans '
                            'WHERE key_id=%s AND pack=%s', (key_id, pack)).fetchone()
        if not plan:
            count = billing.PACKS[pack]
            amount = billing.price_paise(count)
            remote = client.plan.create({
                'period': 'monthly', 'interval': 1,
                'item': {'name': f'Donna Photoshoot {pack.title()} — {count} credits/month',
                         'description': 'Monthly image credits; unused credits roll over',
                         'amount': amount, 'currency': 'INR',
                         'tax_inclusive': False, 'tax_rate': billing.GST_BASIS_POINTS,
                         'sac_code': billing.SAC_CODE},
                'notes': {'app': 'vox-photoshoot', 'pack': pack}})
            if remote['item']['amount'] != amount or remote['item']['currency'] != 'INR':
                raise RuntimeError('Razorpay returned a different plan price')
            plan = (remote['id'], count, amount)
            conn.execute('INSERT INTO subscription_plans VALUES(%s,%s,%s,%s,%s)',
                         (key_id, pack, *plan))
        remote = client.subscription.create({
            'plan_id': plan[0], 'quantity': 1, 'total_count': 1200,
            'expire_by': int(time.time()) + 86400, 'customer_notify': True,
            'notes': {'app': 'vox-photoshoot', 'workspace_id': str(workspace_id), 'pack': pack}})
        conn.execute('INSERT INTO subscriptions(id,workspace_id,plan_id,pack,credits,'
                     'amount_paise,status,charge_at,short_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (remote['id'], workspace_id, plan[0], pack, plan[1], plan[2],
                      remote['status'], remote.get('charge_at'), remote.get('short_url')))
        return {'subscription_id': remote['id'], 'credits': plan[1]}


def cancel(workspace_id, subscription_id):
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (workspace_id,))
        row = _latest(conn, workspace_id)
        if not row or row['id'] != subscription_id:
            raise ValueError('no such subscription in this workspace')
        if row['status'] not in TERMINAL:
            client = billing._client()
            _sync(conn, row, client.subscription.fetch(row['id']))
            if row['status'] not in TERMINAL:
                _sync(conn, row, client.subscription.cancel(
                    row['id'], {'cancel_at_cycle_end': False}))
    return current(workspace_id)


def _credit(conn, row, payment, invoice):
    """Workspace is locked; ledger, invoice and mandate update commit together."""
    amount = payment.get('amount')
    gross = row['amount_paise'] * (10000 + billing.GST_BASIS_POINTS) // 10000
    if (payment.get('status') not in {'captured', 'refunded'}
            or (payment.get('status') == 'refunded' and payment.get('amount_refunded') != amount)
            or payment.get('currency') != 'INR'
            or amount not in {row['amount_paise'], gross}
            or invoice.get('status') != 'paid' or invoice.get('subscription_id') != row['id']
            or invoice.get('payment_id') != payment.get('id')
            or invoice.get('id') != payment.get('invoice_id')
            or invoice.get('gross_amount') != amount):
        raise ValueError('payment is not a paid billing cycle for this subscription')
    existing = conn.execute('SELECT 1 FROM credit_ledger WHERE workspace_id=%s '
                            'AND idempotency_key=%s',
                            (row['workspace_id'], 'razorpay:' + payment['id'])).fetchone()
    if not existing:
        credits._append(conn, str(row['workspace_id']), row['credits'], 'purchase',
                                  'razorpay:' + payment['id'],
                                  note=f"monthly {row['pack']} subscription {row['id']}")
        conn.execute("INSERT INTO invoices(workspace_id,razorpay_invoice_id,razorpay_payment_id,"
                     "razorpay_subscription_id,credits,amount_paise,status,short_url,paid_at) "
                     "VALUES(%s,%s,%s,%s,%s,%s,'paid',%s,now()) "
                     "ON CONFLICT(razorpay_invoice_id) DO NOTHING",
                     (row['workspace_id'], invoice['id'], payment['id'], row['id'],
                      row['credits'], amount, invoice.get('short_url')))
    if payment.get('amount_refunded'):
        billing.reverse_refund(conn, row, payment)
    balance = conn.execute('SELECT balance_after FROM credit_ledger WHERE workspace_id=%s '
                           'ORDER BY seq DESC LIMIT 1', (row['workspace_id'],)).fetchone()[0]
    refunded = int(payment.get('amount_refunded') or 0)
    if not existing and refunded < amount:
        notifications.financial(conn, 'receipt:' + payment['id'], 'receipt', row['workspace_id'],
                                {'credits': row['credits'] - row['credits'] * refunded // amount,
                                 'balance': balance, 'amount_paise': amount, 'currency': 'INR',
                                 'payment_id': payment['id'], 'plan': row['pack'].title(),
                                 'refunded_paise': refunded, 'invoice_url': invoice.get('short_url')})
    return {'credited': 0 if existing else row['credits'], 'balance': balance}


def handle(event):
    payload = event.get('payload') or {}
    remote = payload.get('subscription', {}).get('entity', {})
    invoice = payload.get('invoice', {}).get('entity', {})
    sub_id = remote.get('id') or invoice.get('subscription_id')
    row = db.query('SELECT * FROM subscriptions WHERE id=%s', (sub_id,), one=True)
    if row is None:
        return {'ignored': 'subscription belongs to another application'}
    client = billing._client()
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (row['workspace_id'],))
        # Fetch inside the lock: delayed events must not overwrite a newer cancellation.
        _sync(conn, row, client.subscription.fetch(row['id']))
        if event['event'] not in {'subscription.charged', 'invoice.paid'}:
            return {'subscription': row['id'], 'status': row['status']}
        payment = payload.get('payment', {}).get('entity', {})
        payment = client.payment.fetch(payment.get('id') or invoice['payment_id'])
        invoice = client.invoice.fetch(payment['invoice_id'])
        return _credit(conn, row, payment, invoice)


def confirm(workspace_id, subscription_id, payment_id, signature):
    row = db.query('SELECT * FROM subscriptions WHERE workspace_id=%s AND id=%s',
                   (workspace_id, subscription_id), one=True)
    if not row:
        raise PermissionError('no such subscription in this workspace')
    message = f"{payment_id}|{row['id']}".encode()
    expected = hmac.new(os.environ['RAZORPAY_KEY_SECRET'].encode(), message,
                        hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PermissionError('invalid payment signature')
    client = billing._client()
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (workspace_id,))
        payment = client.payment.fetch(payment_id)
        _sync(conn, row, client.subscription.fetch(row['id']))
        # Mandate authorisation alone may be a small payment, not a monthly invoice.
        if payment.get('status') in {'captured', 'refunded'} and payment.get('invoice_id'):
            invoice = client.invoice.fetch(payment['invoice_id'])
            if invoice.get('status') == 'paid' and invoice.get('subscription_id') == row['id']:
                _credit(conn, row, payment, invoice)
    return {'subscription': current(workspace_id), 'balance': credits.balance(workspace_id)}


def demo():
    """One integration check: real transactions and HTTP, only Razorpay is stubbed."""
    import json
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from urllib.parse import urlparse
    from fastapi.testclient import TestClient
    import app
    import auth

    dsn = urlparse(os.environ.get('DATABASE_URL', ''))
    if dsn.hostname not in {'127.0.0.1', 'localhost'} or dsn.path != '/donna_campaign_test':
        raise RuntimeError('use the throwaway local donna_campaign_test database')
    os.environ.update(RAZORPAY_KEY_ID='test-' + uuid.uuid4().hex,
                      RAZORPAY_KEY_SECRET='test-key-secret', RAZORPAY_WEBHOOK_SECRET='test-hook')
    os.environ.pop('S3_BUCKET', None)
    db.migrate()
    remote, invoices, payments, workspaces = {}, {}, {}, []
    gateway = SimpleNamespace()
    gateway.plan = SimpleNamespace(create=Mock(side_effect=lambda data:
        {'id': 'plan_' + uuid.uuid4().hex, 'item': data['item']}))

    def create(data):
        sid = 'sub_' + uuid.uuid4().hex
        remote[sid] = dict(id=sid, plan_id=data['plan_id'], status='created', charge_at=None)
        return dict(remote[sid])

    def stop(sid, data):
        assert data == {'cancel_at_cycle_end': False}
        remote[sid]['status'] = 'cancelled'
        return dict(remote[sid])

    gateway.subscription = SimpleNamespace(create=Mock(side_effect=create),
        fetch=Mock(side_effect=lambda sid: dict(remote[sid])), cancel=Mock(side_effect=stop))
    gateway.invoice = SimpleNamespace(fetch=Mock(side_effect=lambda iid: invoices[iid]))
    gateway.payment = SimpleNamespace(fetch=Mock(side_effect=lambda pid: payments[pid]))
    uid = str(db.query('INSERT INTO users(email,password_hash) VALUES(%s,%s) RETURNING id',
                       (uuid.uuid4().hex + '@subscription.test', 'disabled'), one=True)['id'])

    def workspace():
        wid = str(db.query("INSERT INTO workspaces(name) VALUES('Subscription check') RETURNING id",
                           one=True)['id'])
        workspaces.append(wid)
        db.query("INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,'owner')", (uid, wid))
        return wid

    def charge(sid, amount=105000, **overrides):
        pid, iid = 'pay_' + uuid.uuid4().hex, 'inv_' + uuid.uuid4().hex
        payments[pid] = {'id': pid, 'invoice_id': iid, 'amount': amount, 'currency': 'INR',
                         'status': 'captured', **overrides}
        invoices[iid] = dict(id=iid, subscription_id=sid, payment_id=pid,
                             status='paid', gross_amount=amount)
        remote[sid].update(status='active', charge_at=1800000000)
        return {'event': 'subscription.charged', 'payload': {
            'subscription': {'entity': dict(remote[sid], notes={'credits': '999999'})},
            'payment': {'entity': payments[pid]}}}

    try:
        with patch.object(billing, '_client', return_value=gateway), TestClient(app.app, raise_server_exceptions=False) as client:
            assert client.post('/api/checkout', data={'pack': 'starter'}).status_code == 401
            wid, other = workspace(), workspace()
            client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))
            assert client.get('/api/subscription').json() == {'subscription': None}
            assert client.post('/api/checkout', data={'pack': 'bad'}).status_code == 400
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: client.post('/api/checkout', data={'pack': 'starter'}), range(2)))
            assert all(r.status_code == 200 for r in results)
            sid = results[0].json()['subscription_id']
            assert results[1].json()['subscription_id'] == sid and gateway.subscription.create.call_count == 1
            assert gateway.subscription.create.call_args.args[0]['total_count'] == 1200
            assert client.post('/api/checkout', data={'pack': 'house'}).status_code == 400
            assert credits.balance(wid) == 0

            def deliver(event, signature=None):
                raw = json.dumps(event).encode()
                sig = signature or hmac.new(b'test-hook', raw, hashlib.sha256).hexdigest()
                return client.post('/api/webhooks/razorpay', content=raw,
                                   headers={'X-Razorpay-Signature': sig})

            # Refunds can arrive before the delayed first charge notification.
            early = workspace()
            client.cookies.set(auth.COOKIE, auth.start_session(uid, early))
            early_sid = client.post('/api/checkout', data={'pack': 'starter'}).json()['subscription_id']
            early_charge = charge(early_sid)
            early_pid = early_charge['payload']['payment']['entity']['id']
            early_charge['payload']['payment']['entity'] = dict(payments[early_pid])
            payments[early_pid]['amount_refunded'] = 52500
            early_refund = {'event': 'refund.processed', 'payload': {'refund': {'entity': {'payment_id': early_pid}}}}
            assert deliver(early_refund).status_code == 200
            assert deliver(early_charge).status_code == 200 and credits.balance(early) == 15
            assert deliver(early_charge).status_code == 200 and credits.balance(early) == 15
            payments[early_pid].update(amount_refunded=105000, status='refunded')
            assert deliver(early_charge).status_code == 200 and credits.balance(early) == 0

            # Refund arrives while confirmation holds the lock, before its invoice insert.
            from threading import Event
            racing = workspace()
            client.cookies.set(auth.COOKIE, auth.start_session(uid, racing))
            racing_sid = client.post('/api/checkout', data={'pack': 'starter'}).json()['subscription_id']
            racing_charge = charge(racing_sid)
            racing_pid = racing_charge['payload']['payment']['entity']['id']
            racing_refund = {'event': 'refund.processed', 'payload': {'refund': {'entity': {'payment_id': racing_pid}}}}
            refund_fetched = Event()
            first_fetch = True
            with ThreadPoolExecutor(1) as pool:
                def fetch_during_confirmation(pid):
                    nonlocal first_fetch
                    if pid == racing_pid and first_fetch:
                        first_fetch = False
                        snapshot = dict(payments[pid])
                        payments[pid]['amount_refunded'] = 52500
                        pending_refund.append(pool.submit(deliver, racing_refund))
                        assert refund_fetched.wait(5), 'refund did not reach the pre-insert lookup'
                        return snapshot
                    if pid == racing_pid:
                        refund_fetched.set()
                    return dict(payments[pid])
                pending_refund = []
                with patch.object(gateway.payment, 'fetch', side_effect=fetch_during_confirmation):
                    racing_callback = dict(razorpay_subscription_id=racing_sid, razorpay_payment_id=racing_pid,
                        razorpay_signature=hmac.new(b'test-key-secret', f'{racing_pid}|{racing_sid}'.encode(), hashlib.sha256).hexdigest())
                    assert client.post('/api/subscription/verify', data=racing_callback).status_code == 200
                    assert pending_refund[0].result(timeout=10).status_code == 200
            assert credits.balance(racing) == 15
            assert db.query("SELECT count(*) AS n FROM credit_ledger WHERE workspace_id=%s AND kind='purchase'",
                            (racing,), one=True)['n'] == 1
            client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))

            auth_pid = 'pay_authorisation'
            payments[auth_pid] = dict(id=auth_pid, status='captured', amount=500, currency='INR', invoice_id=None)
            remote[sid]['status'] = 'authenticated'
            callback = dict(razorpay_subscription_id=sid, razorpay_payment_id=auth_pid,
                            razorpay_signature=hmac.new(b'test-key-secret', f'{auth_pid}|{sid}'.encode(), hashlib.sha256).hexdigest())
            assert client.post('/api/subscription/verify', data={**callback, 'razorpay_signature': 'bad'}).status_code == 400
            assert client.post('/api/subscription/verify', data=callback).json()['balance'] == 0
            first = charge(sid)
            assert deliver(first, 'bad').status_code == 400 and credits.balance(wid) == 0
            assert deliver(first).json()['credited'] == 30 and credits.balance(wid) == 30
            assert deliver(first).json()['credited'] == 0 and credits.balance(wid) == 30
            invoice = invoices[first['payload']['payment']['entity']['invoice_id']]
            assert deliver({'event': 'invoice.paid', 'payload': {'invoice': {'entity': invoice}}}).json()['credited'] == 0
            pid = first['payload']['payment']['entity']['id']
            verified = dict(razorpay_subscription_id=sid, razorpay_payment_id=pid,
                            razorpay_signature=hmac.new(b'test-key-secret', f'{pid}|{sid}'.encode(), hashlib.sha256).hexdigest())
            assert client.post('/api/subscription/verify', data=verified).json()['balance'] == 30
            second = charge(sid, amount=123900)
            assert deliver(second).json()['credited'] == 30 and credits.balance(wid) == 60
            assert client.get('/api/subscription').json()['subscription']['credited_payments'] == 2
            assert client.get('/api/invoices').json()[0]['amount'] == 1239
            invalid = charge(sid, amount=500)
            assert deliver(invalid).status_code == 503 and credits.balance(wid) == 60
            retry = charge(sid)
            original = app.subscriptions._credit
            def crash(*args):
                original(*args)
                raise RuntimeError('intentional crash before commit')
            with patch.object(app.subscriptions, '_credit', side_effect=crash):
                assert deliver(retry).status_code == 503
            assert credits.balance(wid) == 60
            assert deliver(retry).json()['credited'] == 30 and credits.balance(wid) == 90
            assert client.post('/api/checkout', data={'pack': 'starter'}).status_code == 400
            unknown = {'event': 'subscription.charged', 'payload': {'subscription': {'entity': {'id': 'sub_other_app'}}}}
            assert deliver(unknown).status_code == 200 and credits.balance(wid) == 90
            payments[pid]['amount_refunded'] = 52500
            refund = {'event': 'refund.processed', 'payload': {'refund': {'entity': {
                'id': 'rfnd_partial', 'payment_id': pid, 'amount': 52500, 'status': 'processed'}}}}
            assert deliver(refund).json()['reversed'] == 15 and credits.balance(wid) == 75
            assert deliver(refund).json()['reversed'] == 0 and credits.balance(wid) == 75
            payments[pid]['amount_refunded'] = 105000
            refund['payload']['refund']['entity']['id'] = 'rfnd_remainder'
            assert deliver(refund).status_code == 200 and credits.balance(wid) == 60
            assert deliver(refund).status_code == 200 and credits.balance(wid) == 60
            assert deliver(first).json()['credited'] == 0 and credits.balance(wid) == 60
            client.cookies.set(auth.COOKIE, auth.start_session(uid, other))
            assert client.get('/api/subscription').json()['subscription'] is None
            assert client.post('/api/subscription/cancel', data={'subscription_id': sid}).status_code == 400
            assert client.post('/api/subscription/verify', data=verified).status_code == 400
            db.query("UPDATE memberships SET role='member' WHERE user_id=%s AND workspace_id=%s", (uid, other))
            assert not client.get('/api/billing').json()['can_manage']
            assert client.post('/api/checkout', data={'pack': 'starter'}).status_code == 403
            client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))
            assert client.post('/api/subscription/cancel', data={'subscription_id': sid}).json()['subscription']['status'] == 'cancelled'
            assert credits.balance(wid) == 60
            assert deliver({'event': 'subscription.activated', 'payload': first['payload']}).json()['status'] == 'cancelled'
            for pack in ('studio', 'house'):
                result = client.post('/api/checkout', data={'pack': pack})
                assert result.status_code == 200 and result.json()['credits'] == billing.PACKS[pack]
                assert client.post('/api/subscription/cancel', data={'subscription_id': result.json()['subscription_id']}).status_code == 200
        print('subscriptions passed: owner scope, checkout races, signatures, authorisation, renewals, rollover, duplicate events/callbacks, rollback/retry, partial/full refunds, cancellation, all plans')
    finally:
        for wid in workspaces:
            db.query('DELETE FROM credit_ledger WHERE workspace_id=%s', (wid,))
            db.query('DELETE FROM workspaces WHERE id=%s', (wid,))
        db.query('DELETE FROM subscription_plans WHERE key_id=%s', (os.environ['RAZORPAY_KEY_ID'],))
        db.query('DELETE FROM users WHERE id=%s', (uid,))
        db.close()


if __name__ == '__main__':
    demo()
