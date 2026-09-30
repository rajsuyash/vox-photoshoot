"""Razorpay signatures, legacy invoices and refunds.

Self-serve monthly mandates live in subscriptions.py. Both payment paths deduplicate
on the payment id; webhook signatures cover raw bytes before JSON parsing.
"""

import hashlib
import hmac
import json
import os
import time

import credits
import db

# --- what a credit costs, and what it sells for -----------------------------------
#
# One credit is one generated image. Set here rather than per-invoice so a price can
# never be taken from the browser.
#
# Derived rather than guessed, from measured numbers (2026-08-20, USD/INR 95.75):
#
#   fal nano-banana-pro/edit @2K          $0.150000   published rate
#   Anthropic detection, 1/3 of a shoot   $0.000821   measured 2299 in + 33 out, haiku
#   S3 storage 30 days + PUT              $0.000127   ~5MB PNG
#   App Runner active CPU                 $0.000567   ~1 min at 0.5 vCPU
#   forex markup on USD billing 3%        $0.004545
#                                         ---------
#   marginal                              $0.156061 = Rs 14.94
#
# Plus Rs 2,283/month of fixed cost (App Runner memory, RDS, Secrets Manager) which is
# pure volume arithmetic. At the planning figure of 100 shoots a month — 300 credits —
# that is Rs 7.84 a credit, so all-in cost is Rs 22.78. Priced at cost + 50%, grossed up
# for the Razorpay fee, that is Rs 35.
#
# NOTE: AWS is currently billing $0 because account credits absorb it. The Rs 2,283
# appears when those run out, so it is costed in deliberately rather than ignored.
MARGINAL_COST_RUPEES = 14.94        # per credit, excludes fixed monthly costs
RUPEES_PER_CREDIT = 35              # ex-GST. 18% GST is added on the invoice.

# GST. Razorpay computes the tax itself and prints it as its own line, which is what
# makes the document a tax invoice a B2B customer can claim input credit against — the
# entire reason for using Razorpay Invoices rather than a payment link.
#
# Sending these is necessary but NOT sufficient: Razorpay only computes tax once GST is
# configured on the account (Dashboard > Settings > Tax/GST, GSTIN-gated). Until then it
# accepts and stores both fields and returns tax_amount = 0, i.e. it silently issues an
# invoice with no tax line. price_paise() is therefore the ex-GST figure in both cases,
# and the customer's total is that plus whatever Razorpay adds.
GST_BASIS_POINTS = 1800             # 18%, as Razorpay wants it: hundredths of a percent

# SAC 998386 is "photographic and videographic processing services". An AI image
# generation service is arguably 998434 (on-line software) instead, and the answer
# changes the rate a customer can reclaim. Confirm with the CA before the first live
# invoice; it is one constant.
SAC_CODE = '998386'

# What a customer can buy without talking to anyone. Keyed by name, never by amount:
# /api/checkout takes a pack key and resolves the size here, so a client that posts
# credits=99999 gets a KeyError rather than a discount.
PACKS = {
    'starter': 30,      # 10 shoots
    'studio': 100,      # 33 shoots
    'house': 300,       # 100 shoots — the volume the price was derived at
}

# How long a self-serve invoice stays payable. Short on purpose: an invoice-first
# checkout is what buys the GST document, and the cost of that shape is an abandoned
# cart leaving an issued invoice open. Expiry closes them without anyone doing it by
# hand. Long enough that someone can go and find their UPI PIN.
INVOICE_TTL_MINUTES = 30

# The only event that may add credits. See the module docstring.
CREDITING_EVENT = 'invoice.paid'


def configured() -> bool:
    return bool(os.environ.get('RAZORPAY_KEY_ID')
                and os.environ.get('RAZORPAY_KEY_SECRET'))


def _client():
    import razorpay

    if not configured():
        raise RuntimeError('RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set')
    return razorpay.Client(auth=(os.environ['RAZORPAY_KEY_ID'],
                                 os.environ['RAZORPAY_KEY_SECRET']))


def price_paise(credits_count: int) -> int:
    """Integers only. Money in floats is how a rounding error becomes a rounding bug."""
    return credits_count * RUPEES_PER_CREDIT * 100


def raise_invoice(workspace_id: str, credits_count: int,
                  expire_by: int | None = None) -> dict:
    """Create a GST invoice in Razorpay and email it to the workspace's billing address.

    An ISSUED invoice carries an order_id, which is what Checkout opens against — that
    is what lets self-serve have both a modal and a tax invoice without hand-rolling
    the document. Drafts have order_id NULL, so this must never create one.
    """
    if credits_count <= 0:
        raise ValueError('an invoice needs at least one credit')

    workspace = db.query(
        'SELECT name, gstin, billing_email FROM workspaces WHERE id = %s',
        (workspace_id,), one=True)
    if workspace is None:
        raise ValueError('no such workspace')
    if not workspace['billing_email']:
        raise ValueError('this workspace has no billing email — add one first')

    amount = price_paise(credits_count)
    invoice = _client().invoice.create({
        'type': 'invoice',
        'description': f'{credits_count} image credits — Donna Photoshoot',
        'customer': {
            'name': workspace['name'],
            'email': workspace['billing_email'],
            **({'gstin': workspace['gstin']} if workspace['gstin'] else {}),
        },
        'line_items': [{
            'name': 'Donna Photoshoot image credits',
            'description': f'{credits_count} credits, one credit is one generated image',
            'amount': price_paise(1),
            'currency': 'INR',
            'quantity': credits_count,
            # The amount above is ex-GST, so the tax is added on top rather than
            # carved out of it. Getting this backwards silently cuts the margin by 18%.
            'tax_inclusive': False,
            'tax_rate': GST_BASIS_POINTS,
            'sac_code': SAC_CODE,
        }],
        # Carried back on the webhook, so the credits land in the right workspace
        # without trusting anything the payer could influence.
        'notes': {'workspace_id': str(workspace_id), 'credits': str(credits_count)},
        'sms_notify': 1,
        'email_notify': 1,
        # Unix seconds. Passed in rather than computed from a clock inside the payload
        # so the caller decides how long the cart lives.
        'expire_by': expire_by or int(time.time() + INVOICE_TTL_MINUTES * 60),
    })

    db.query(
        """INSERT INTO invoices (workspace_id, razorpay_invoice_id, razorpay_order_id,
                                 credits, amount_paise, status, short_url)
           VALUES (%s, %s, %s, %s, %s, 'issued', %s)""",
        (workspace_id, invoice['id'], invoice.get('order_id'), credits_count, amount,
         invoice.get('short_url')))
    # Surfaced rather than assumed. Razorpay returns tax_amount = 0 when GST is not
    # configured on the account, and an invoice with no tax line looks entirely normal
    # until a customer's CA rejects it — so the caller gets told, every time.
    tax_paise = int(invoice.get('tax_amount') or 0)
    return {'invoice_id': invoice['id'], 'short_url': invoice.get('short_url'),
            'order_id': invoice.get('order_id'),
            'credits': credits_count, 'amount_paise': amount,
            'tax_paise': tax_paise,
            'gross_paise': int(invoice.get('gross_amount') or amount),
            'gst_applied': tax_paise > 0}


def verify(raw_body: bytes, signature: str) -> bool:
    """HMAC over the exact bytes received, compared in constant time."""
    secret = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle(raw_body: bytes, signature: str) -> dict:
    """Process a signed delivery; failures propagate so Razorpay can retry."""
    if not verify(raw_body, signature):
        raise PermissionError('bad signature')

    event = json.loads(raw_body)
    kind = event.get('event')
    import subscriptions
    if kind in subscriptions.EVENTS:
        return subscriptions.handle(event)
    if kind == 'refund.processed':
        return _refund(event)
    if kind != CREDITING_EVENT:
        return {'ignored': kind}

    payload = event.get('payload', {})
    invoice = payload.get('invoice', {}).get('entity', {})
    payment = payload.get('payment', {}).get('entity', {})
    if invoice.get('subscription_id'):
        return subscriptions.handle(event)

    row = db.query('SELECT workspace_id, credits FROM invoices WHERE razorpay_invoice_id=%s',
                   (invoice.get('id'),), one=True)
    if not row:
        return {'ignored': 'invoice belongs to another application'}
    payment_id = payment.get('id') or invoice.get('payment_id')
    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (row['workspace_id'],))
        payment = _client().payment.fetch(payment_id)
        if (payment.get('status') not in {'captured', 'refunded'}
                or payment.get('invoice_id') != invoice.get('id')
                or (payment.get('status') == 'refunded'
                    and payment.get('amount_refunded') != payment.get('amount'))):
            raise ValueError('invoice payment has not been captured')
        credits._append(conn, str(row['workspace_id']), row['credits'], 'purchase',
                        f'razorpay:{payment_id}', note=f'invoice {invoice["id"]}')
        conn.execute("UPDATE invoices SET status='paid', paid_at=COALESCE(paid_at,now()), "
                     "razorpay_payment_id=%s WHERE razorpay_invoice_id=%s AND status<>'refunded'",
                     (payment_id, invoice['id']))
        if payment.get('amount_refunded'):
            reverse_refund(conn, row, payment)
        balance = conn.execute('SELECT balance_after FROM credit_ledger WHERE workspace_id=%s '
                               'ORDER BY seq DESC LIMIT 1', (row['workspace_id'],)).fetchone()[0]
    return {'credited': row['credits'], 'workspace_id': str(row['workspace_id']), 'balance': balance}


def _refund(event: dict) -> dict:
    """Money went back, so the credits do too — even into a negative balance.

    This is exactly why the ledger has no non-negative constraint: a ledger that cannot
    record "you owe me four credits" forces somebody to falsify it.
    """
    refund = event.get('payload', {}).get('refund', {}).get('entity', {})
    payment_id = refund.get('payment_id')
    if not payment_id:
        return {'ignored': 'refund carried no payment id'}

    client = _client()
    payment = client.payment.fetch(payment_id)
    invoice_id = payment.get('invoice_id')
    if not invoice_id:
        return {'ignored': 'refund has no billing invoice'}
    row = db.query('SELECT * FROM invoices WHERE razorpay_invoice_id=%s', (invoice_id,), one=True)
    if row is None:
        # A refund may beat the first charge event. Resolve its real invoice rather
        # than acknowledging it permanently while the local payment row is absent.
        invoice = client.invoice.fetch(invoice_id)
        import subscriptions
        subscription = db.query('SELECT * FROM subscriptions WHERE id=%s',
                                (invoice.get('subscription_id'),), one=True)
        if subscription is None:
            return {'ignored': 'refund belongs to another application'}
        gross = subscription['amount_paise'] * (10000 + GST_BASIS_POINTS) // 10000
        if payment['amount'] not in {subscription['amount_paise'], gross}:
            return {'ignored': 'refund of mandate authorisation, not monthly credits'}
        with db.tx() as conn:
            conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (subscription['workspace_id'],))
            payment = client.payment.fetch(payment_id)
            subscriptions._credit(conn, subscription, payment, client.invoice.fetch(invoice_id))
        return {'reconciled_refund': payment_id}

    with db.tx() as conn:
        conn.execute('SELECT 1 FROM workspaces WHERE id=%s FOR UPDATE', (row['workspace_id'],))
        payment = client.payment.fetch(payment_id)
        if payment.get('status') not in {'captured', 'refunded'} or payment.get('invoice_id') != invoice_id:
            raise ValueError('refund payment has not been captured')
        # Also handles an outstanding legacy invoice refunded before its paid event.
        credits._append(conn, str(row['workspace_id']), row['credits'], 'purchase',
                        f'razorpay:{payment_id}', note=f'refund reconciliation: {invoice_id}')
        conn.execute("UPDATE invoices SET status=CASE WHEN status='refunded' THEN status ELSE 'paid' END, "
                     'razorpay_payment_id=%s, paid_at=COALESCE(paid_at,now()) '
                     'WHERE razorpay_invoice_id=%s', (payment_id, invoice_id))
        delta = reverse_refund(conn, row, payment)
    return {'reversed': delta, 'workspace_id': str(row['workspace_id'])}


def reverse_refund(conn, row, payment):
    """Reconcile refunds inside a workspace lock, including refund-before-charge."""
    payment_id = payment['id']
    total, refunded = int(payment['amount']), int(payment.get('amount_refunded') or 0)
    if total <= 0 or refunded <= 0 or refunded > total:
        raise ValueError('refund is not reflected in the Razorpay payment yet')
    # Cumulative provider totals handle out-of-order partial refunds and rounding.
    owed = int(row['credits']) * refunded // total
    prefix = f'refund:{payment_id}'
    reversed_so_far = conn.execute(
        "SELECT COALESCE(-sum(delta),0) FROM credit_ledger WHERE workspace_id=%s "
        "AND kind='chargeback' AND (idempotency_key=%s OR starts_with(idempotency_key,%s))",
        (row['workspace_id'], prefix, prefix + ':')).fetchone()[0]
    delta = max(0, owed - reversed_so_far)
    if delta:
        credits._append(conn, str(row['workspace_id']), -delta, 'chargeback',
                        prefix if refunded == total else f'{prefix}:{refunded}',
                        note=f'refund of payment {payment_id}: {refunded}/{total} paise')
    if refunded == total:
        conn.execute("UPDATE invoices SET status='refunded' WHERE razorpay_payment_id=%s", (payment_id,))
    return delta


def invoices_for(workspace_id: str, limit: int = 50) -> list[dict]:
    return db.query(
        """SELECT razorpay_invoice_id, credits, amount_paise, status, short_url,
                  issued_at, paid_at
             FROM invoices WHERE workspace_id = %s
         ORDER BY issued_at DESC LIMIT %s""", (workspace_id, limit))


def demo() -> None:
    """Self-check: signature handling and idempotency. No network, no Razorpay account."""
    os.environ['RAZORPAY_WEBHOOK_SECRET'] = 'test-secret'
    body = b'{"event":"invoice.paid"}'
    good = hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()

    assert verify(body, good)
    assert not verify(body, 'deadbeef'), 'a wrong signature was accepted'
    assert not verify(body + b' ', good), 'signature must cover the exact bytes'
    assert not verify(body, ''), 'a missing signature was accepted'

    # Re-serialising parsed JSON is the classic way this breaks: it passes in dev and
    # fails in prod on key ordering, so the check must be over the raw bytes.
    reserialised = json.dumps(json.loads(body), separators=(', ', ': ')).encode()
    assert reserialised != body and not verify(reserialised, good)

    try:
        handle(body, 'wrong')
    except PermissionError:
        pass
    else:
        raise AssertionError('an unsigned webhook was processed')

    # Only one event type may credit. Anything else is acknowledged and ignored.
    for other in ('payment.captured', 'order.paid', 'payment.authorized'):
        payload = json.dumps({'event': other}).encode()
        signature = hmac.new(b'test-secret', payload, hashlib.sha256).hexdigest()
        assert handle(payload, signature) == {'ignored': other}, other

    assert price_paise(10) == 10 * RUPEES_PER_CREDIT * 100
    assert isinstance(price_paise(3), int)

    # Packs are what a stranger can buy unattended, so the table gets checked rather
    # than trusted. A pack that is not a whole number of shoots leaves credits stranded
    # that the customer paid for and cannot spend on the thing they came for.
    assert PACKS, 'no packs means no self-serve checkout'
    for name, size in PACKS.items():
        assert isinstance(size, int) and size > 0, name
        # Deliberately NOT a whole number of shoots. Credits are fungible — a reshoot
        # and a retouch cost one each — so the remainder on a 100-credit pack is spendable
        # rather than stranded, and 100 is a better number to put on a card than 99.
        assert size >= credits.COST['shoot'], (
            f'pack {name!r} cannot buy even one shoot')
    # No volume discount is encoded yet; if one is added, this is where it gets checked
    # against MARGINAL_COST_RUPEES rather than discovered in a bank statement.
    assert len(set(PACKS.values())) == len(PACKS), 'two packs are the same size'
    assert 0 < INVOICE_TTL_MINUTES <= 24 * 60, 'a cart that lives a day is not a cart'

    # The price has to clear the marginal cost with room to spare. This is the check
    # that fires if fal raises its rate or the rupee moves and nobody re-does the maths
    # — otherwise the first anyone hears of selling below cost is the bank balance.
    assert RUPEES_PER_CREDIT > MARGINAL_COST_RUPEES * 1.5, (
        f'Rs {RUPEES_PER_CREDIT}/credit does not clear Rs {MARGINAL_COST_RUPEES} '
        f'marginal cost by 50% — re-derive the price')
    # And it must cover the fixed base at the volume it was priced for.
    FIXED_RUPEES_MONTH, PLANNED_CREDITS_MONTH = 2283, 300
    all_in = MARGINAL_COST_RUPEES + FIXED_RUPEES_MONTH / PLANNED_CREDITS_MONTH
    assert RUPEES_PER_CREDIT >= all_in * 1.5 * 0.98, (
        f'Rs {RUPEES_PER_CREDIT} is under cost+50% (Rs {all_in * 1.5:.2f}) at '
        f'{PLANNED_CREDITS_MONTH} credits/month')
    # Break-even volume, for the record: below this the fixed base eats the margin.
    breakeven = FIXED_RUPEES_MONTH / (RUPEES_PER_CREDIT / 1.5 - MARGINAL_COST_RUPEES)
    assert breakeven < PLANNED_CREDITS_MONTH, breakeven

    # A video credit must clear the same per-credit cost basis an image credit does —
    # otherwise a video costs the platform more than what a customer's credit buys.
    # Pure and network-free: video.REGISTRY and video.credits_for are plain data/maths.
    import video

    def _worst_usd_per_credit() -> float:
        return max(
            (provider.usd_per_second * duration) / video.credits_for(duration, provider)
            for provider in video.REGISTRY.values()
            for duration in sorted(provider.durations)
        )

    assert _worst_usd_per_credit() <= credits.USD_PER_CREDIT, (
        f'a video provider charges more per credit (${_worst_usd_per_credit():.4f}) '
        f'than the ${credits.USD_PER_CREDIT} basis a credit is sold against')

    # Red-before-green: prove the check can actually fail, not just pass vacuously.
    # Bumping usd_per_second alone cannot do it — video.credits_for's ceil() is
    # self-correcting by construction (ceil(x/c)*c >= x always), so any price rise
    # just charges more credits and the ratio stays under the basis. A believable real
    # bug instead: someone "rounds more kindly" and floors instead of ceils, silently
    # undercharging a fractional credit — that DOES decouple credits from spend.
    import math

    original_credits_for = video.credits_for
    video.credits_for = lambda seconds, provider: (
        math.floor(provider.usd_per_second * seconds / credits.USD_PER_CREDIT) or 1)
    try:
        try:
            assert _worst_usd_per_credit() <= credits.USD_PER_CREDIT
        except AssertionError:
            print(f'RED (expected): floor()-ing the credit count fails the margin '
                 f'check -> ${_worst_usd_per_credit():.4f}/credit')
        else:
            raise AssertionError('a floored credit count should have failed the '
                                 'margin check')
    finally:
        video.credits_for = original_credits_for

    print(f'GREEN: real video pricing (ceil) clears the basis -> '
         f'${_worst_usd_per_credit():.4f}/credit <= ${credits.USD_PER_CREDIT}/credit')

    if os.environ.get('DATABASE_URL'):
        import uuid

        db.migrate()
        ws = str(db.query("INSERT INTO workspaces (name, billing_email) "
                          "VALUES ('billing-check','b@test') RETURNING id",
                          one=True)['id'])
        credits.grant(ws, 0 + 5, 'seed')

        paid = json.dumps({
            'event': 'invoice.paid',
            'payload': {
                'invoice': {'entity': {'id': 'inv_1',
                                       'notes': {'workspace_id': ws, 'credits': '100'}}},
                'payment': {'entity': {'id': 'pay_1', 'status': 'captured'}},
            }}).encode()
        signature = hmac.new(b'test-secret', paid, hashlib.sha256).hexdigest()

        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        db.query("INSERT INTO invoices(workspace_id,razorpay_invoice_id,credits,amount_paise) "
                 "VALUES(%s,'inv_1',100,350000)", (ws,))
        gateway = SimpleNamespace(payment=SimpleNamespace(fetch=Mock(return_value={
            'id': 'pay_1', 'invoice_id': 'inv_1', 'status': 'captured',
            'amount': 350000, 'amount_refunded': 35000})))
        with patch(__name__ + '._client', return_value=gateway):
            first = handle(paid, signature)
            assert first['balance'] == 95, first  # 5 opening +100 paid -10 refunded
            second = handle(paid, signature)
            assert second['balance'] == first['balance'], 'a retried webhook double-credited'

        total, tail = credits.reconcile(ws)
        assert total == tail, (total, tail)

        db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws,))
        db.query('DELETE FROM invoices WHERE workspace_id = %s', (ws,))
        db.query('DELETE FROM workspaces WHERE id = %s', (ws,))
        db.close()

    print('billing ok')


if __name__ == '__main__':
    demo()
