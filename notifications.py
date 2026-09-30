"""Transactional outbox. Never send an email inside a money/account transaction.

SES acceptance is not delivery. Leases fence database writes, not remote sends:
a crash after SES accepts but before we record it may cause a duplicate on retry.
"""
import html
import logging
import os
import re
import uuid
from urllib.parse import urlsplit

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import db

MAX_ATTEMPTS = 8
LEASE_SECONDS = 120


def address(value):
    if not isinstance(value, str) or len(value) > 254:
        raise ValueError('invalid email address')
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value):
        raise ValueError('invalid email address')
    local, domain = value.rsplit('@', 1)
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        raise ValueError('invalid email address')
    return local + '@' + domain.lower()


def _url(value, hosts):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or any(ord(c) < 32 for c in value)):
        raise ValueError('invalid email link')
    return value


def _integer(payload, field, minimum=None):
    value = payload[field]
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError('invalid ' + field)
    return value


def render(kind, payload):
    """Only known templates and trusted link destinations; always plain text + HTML."""
    origin = _url(os.environ.get('PUBLIC_ORIGIN', 'https://photo.voxdonna.com'),
                  {'photo.voxdonna.com'}).rstrip('/')
    if kind == 'welcome':
        count = _integer(payload, 'credits', 0)
        subject, title = 'Welcome to Donna Photoshoot', 'Your next beautiful photo starts here'
        name = str(payload.get('name') or 'there')[:120]
        paragraphs = [f'Hi {name}, welcome to Donna Photoshoot.',
                      f'Your account is ready with {count} credits. '
                      'Create a product photoshoot or a marketing campaign from your product photo.']
        link, label = origin + '/index.html', 'Start a photoshoot'
    elif kind in {'invite', 'reset'}:
        import account_actions
        subject = 'Your Donna Photoshoot ' + ('invitation' if kind == 'invite' else 'password reset')
        title = 'You’re invited' if kind == 'invite' else 'Reset your password'
        paragraphs = [('Accept your workspace invitation. Existing accounts must sign in with the invited email.'
                       if kind == 'invite' else 'You requested a password reset. Ignore this email if it wasn’t you.'),
                      'This link works once and expires in ' + ('24 hours.' if kind == 'invite' else '30 minutes.')]
        link = origin + '/account.html#token=' + account_actions.delivery(payload['action_id'])
        label = 'Accept invitation' if kind == 'invite' else 'Reset password'
    elif kind in {'receipt', 'refund'}:
        count = _integer(payload, 'credits', 0)
        balance = _integer(payload, 'balance')
        amount = _integer(payload, 'amount_paise', 0)
        if payload.get('currency', 'INR') != 'INR':
            raise ValueError('unsupported currency')
        money = f'INR {amount // 100:,}.{amount % 100:02d}'
        reference = str(payload.get('payment_id', ''))[:100]
        if kind == 'receipt':
            subject, title = 'Your Donna Photoshoot credit receipt', 'Your credits are ready'
            paragraphs = [f'Payment received: {money} (total paid, including applicable tax).',
                          f'{count} credits added. Balance after this purchase: {balance} credits.',
                          f'Payment reference: {reference}']
            paragraphs.insert(0, 'Plan: ' + str(payload.get('plan') or 'Photo credits')[:120])
            if payload.get('refunded_paise'):
                paragraphs.append('This payment already includes a refund; the credit amount above '
                                  'is the net amount retained.')
        else:
            subject, title = 'Your Donna Photoshoot refund confirmation', 'Refund confirmed'
            paragraphs = [f'Total refunds recorded for this payment: {money}.',
                          f'Total credits reversed for this payment: {count}. Balance after this adjustment: {balance} credits.',
                          f'Payment reference: {reference}']
        invoice = payload.get('invoice_url')
        link = (_url(invoice, {'rzp.io', 'rzp.in', 'razorpay.com', 'api.razorpay.com'})
                if invoice else origin + '/billing.html')
        label = 'View invoice' if invoice else 'View billing'
    else:
        raise ValueError('unsupported notification kind')
    footer = 'Questions? Reply to this email for help. Donna Photoshoot.'
    text = '\n\n'.join([title, *paragraphs, label + ': ' + link, footer])
    body = ''.join('<p style="line-height:1.7;color:#51453d">' + html.escape(p) + '</p>'
                   for p in paragraphs)
    markup = ('<!doctype html><html><body style="margin:0;background:#f6f2ed;'
              'font-family:Arial,sans-serif"><div style="max-width:560px;margin:32px auto;'
              'padding:36px;background:#fff;border-radius:16px">'
              '<p style="letter-spacing:2px;color:#a77b57;font-size:12px">DONNA PHOTOSHOOT</p>'
              '<h1 style="font-family:Georgia,serif;font-weight:normal;color:#735237">'
              + html.escape(title) + '</h1>' + body
              + '<p style="margin:28px 0"><a style="display:inline-block;padding:14px 22px;'
              'background:#b9825e;color:#fff;border-radius:8px;text-decoration:none" href="'
              + html.escape(link, quote=True) + '">' + label + '</a></p>'
              '<p style="font-size:12px;color:#817269">' + footer + '</p></div></body></html>')
    return {'subject': subject, 'text': text, 'html': markup}


def enqueue(conn, event_key, kind, recipient, payload, *, workspace_id=None, user_id=None):
    """Caller owns the transaction. Business event key is stable across all retries."""
    recipient = address(recipient)
    if not isinstance(event_key, str) or not 1 <= len(event_key) <= 240:
        raise ValueError('invalid event key')
    if kind in {'invite', 'reset'}:
        ident = str(uuid.UUID(payload['action_id']))
        if not conn.execute('SELECT id FROM account_actions WHERE id=%s AND kind=%s '
                            'AND user_id=%s AND workspace_id IS NOT DISTINCT FROM %s::uuid',
                            (ident, kind, user_id, workspace_id)).fetchone():
            raise ValueError('account action does not match notification')
    else:
        render(kind, payload)
    conn.execute('INSERT INTO notifications(event_key,kind,recipient,payload,workspace_id,user_id,status) '
                 "VALUES(%s,%s,%s,%s,%s,%s,CASE WHEN EXISTS(SELECT 1 FROM notification_suppressions "
                 "WHERE recipient=lower(%s)) THEN 'suppressed' ELSE 'queued' END) "
                 'ON CONFLICT(event_key) DO NOTHING',
                 (event_key, kind, recipient, Jsonb(payload), workspace_id, user_id, recipient))
    return conn.execute('SELECT id FROM notifications WHERE event_key=%s', (event_key,)).fetchone()[0]


def financial(conn, key, kind, workspace_id, payload):
    """Billing contact first, then a valid owner email. Bad legacy data cannot lose money."""
    contacts = conn.execute('SELECT billing_email FROM workspaces WHERE id=%s',
                            (workspace_id,)).fetchall()
    contacts += conn.execute("SELECT u.email FROM users u JOIN memberships m ON m.user_id=u.id "
                             "WHERE m.workspace_id=%s AND m.role='owner' ORDER BY m.created_at,u.id",
                             (workspace_id,)).fetchall()
    for candidate in contacts:
        try:
            recipient = address(candidate[0])
        except ValueError:
            continue
        return enqueue(conn, key, kind, recipient, payload, workspace_id=workspace_id)
    logging.getLogger('donna').warning('notification missing valid billing contact workspace=%s', workspace_id)
    return None


def claim():
    with db.tx() as conn:
        if os.environ.get('NOTIFICATIONS_SINCE'):
            conn.execute("UPDATE notifications SET status='suppressed',last_error='before_activation',updated_at=now() "
                         "WHERE status='queued' AND created_at<%s::timestamptz", (os.environ['NOTIFICATIONS_SINCE'],))
        conn.execute("UPDATE notifications SET status='failed',last_error='attempts_exhausted',"
                     'lease_token=NULL,lease_until=NULL,updated_at=now() '
                     "WHERE status IN ('queued','sending') AND attempts>=%s "
                     'AND (lease_until IS NULL OR lease_until<now())', (MAX_ATTEMPTS,))
        conn.execute("UPDATE notifications n SET status='suppressed',lease_token=NULL,"
                     'lease_until=NULL,updated_at=now() FROM notification_suppressions s '
                     "WHERE lower(n.recipient)=s.recipient AND (n.status='queued' OR "
                     "(n.status='sending' AND n.lease_until<now()))")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("WITH next AS (SELECT id FROM notifications WHERE attempts<%s "
                        "AND ((status='queued' AND next_attempt_at<=now()) OR "
                        "(status='sending' AND lease_until<now())) ORDER BY created_at "
                        'FOR UPDATE SKIP LOCKED LIMIT 1) '
                        "UPDATE notifications n SET status='sending',attempts=attempts+1,"
                        "lease_until=now()+%s * interval '1 second',lease_token=%s,updated_at=now() "
                        'FROM next WHERE n.id=next.id RETURNING n.*',
                        (MAX_ATTEMPTS, LEASE_SECONDS, uuid.uuid4()))
            return cur.fetchone()


def accepted(row, message_id):
    with db.tx() as conn:
        updated = conn.execute("UPDATE notifications SET status='accepted',provider_message_id=%s,"
                               'accepted_at=now(),lease_until=NULL,lease_token=NULL,last_error=NULL,updated_at=now() '
                               "WHERE id=%s AND lease_token=%s AND status='sending' RETURNING id",
                               (message_id, row['id'], row['lease_token'])).fetchone()
        if updated:
            _apply_feedback(conn, message_id)
        return bool(updated)


def failed(row, code, *, permanent=False):
    status = 'failed' if permanent or row['attempts'] >= MAX_ATTEMPTS else 'queued'
    delay = min(3600, 30 * 2 ** min(row['attempts'], 8))
    safe_code = code if re.fullmatch(r'[A-Za-z0-9_]{1,64}', code) else 'provider_error'
    updated = db.query('UPDATE notifications SET status=%s,last_error=%s,lease_until=NULL,'
                       "lease_token=NULL,next_attempt_at=now()+%s * interval '1 second',updated_at=now() "
                       "WHERE id=%s AND lease_token=%s AND status='sending' RETURNING id",
                       (status, safe_code, delay, row['id'], row['lease_token']), one=True)
    return status if updated else 'stale'


def feedback(message_id, event, *, permanent=True):
    """Authenticated consumer only. Keep early feedback; don't suppress transient bounces."""
    status = {'Delivery': 'delivered', 'Bounce': 'bounced', 'Complaint': 'complained'}.get(event)
    if not status:
        return False
    with db.tx() as conn:
        conn.execute('INSERT INTO notification_feedback(provider_message_id,status,suppress) '
                     'VALUES(%s,%s,%s) ON CONFLICT(provider_message_id,status) DO UPDATE '
                     'SET suppress=notification_feedback.suppress OR EXCLUDED.suppress',
                     (message_id, status, status == 'complained' or (status == 'bounced' and permanent)))
        return _apply_feedback(conn, message_id)


def _apply_feedback(conn, message_id):
    row = conn.execute('SELECT recipient,status FROM notifications WHERE provider_message_id=%s '
                       'FOR UPDATE', (message_id,)).fetchone()
    if not row:
        return False
    event = conn.execute('SELECT status,suppress FROM notification_feedback '
                         'WHERE provider_message_id=%s ORDER BY CASE status '
                         "WHEN 'complained' THEN 3 WHEN 'bounced' THEN 2 ELSE 1 END DESC LIMIT 1",
                         (message_id,)).fetchone()
    rank = {'accepted': 0, 'delivered': 1, 'bounced': 2, 'complained': 3}
    if not event:
        return False
    status, suppress = event
    if suppress:
        conn.execute('INSERT INTO notification_suppressions(recipient,reason) VALUES(lower(%s),%s) '
                     'ON CONFLICT(recipient) DO UPDATE SET reason=EXCLUDED.reason', (row[0], status))
    if rank.get(row[1], 4) >= rank[status]:
        return False
    conn.execute('UPDATE notifications SET status=%s,updated_at=now(),'
                 "delivered_at=CASE WHEN %s='delivered' THEN now() ELSE delivered_at END "
                 'WHERE provider_message_id=%s', (status, status, message_id))
    return True
