"""Bounded account recovery and support routes, using existing roles and SQL."""
import os
import uuid
from psycopg.types.json import Jsonb

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from pydantic import BaseModel, Field

import account_actions
import auth
import credits
import db
import support
import billing
import admin_security
import storage

router = APIRouter()


def administrator(session: dict = Depends(auth.current_session)):
    return auth.require_admin(session)


def admin_identity(session: dict = Depends(auth.current_session)):
    if not session['is_admin']:
        raise HTTPException(403, 'admins only')
    return session


@router.get('/api/admin/security')
def security_status(session: dict = Depends(admin_identity)):
    return admin_security.status(session)


@router.post('/api/admin/security/enroll')
def enroll(session: dict = Depends(admin_identity)):
    try:
        return admin_security.enroll(session)
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.post('/api/admin/security/verify')
def step_up(code: str = Form(..., max_length=80), enrollment: bool = Form(False),
            session: dict = Depends(admin_identity)):
    try:
        return admin_security.verify(session, code, enrollment=enrollment)
    except ValueError as error:
        raise HTTPException(400, str(error))


class Action(BaseModel):
    request_key: uuid.UUID
    action: str = Field(max_length=40)
    target_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)
    data: dict = Field(default_factory=dict)


@router.post('/api/admin/action')
def change(body: Action, session: dict = Depends(administrator)):
    if body.action in {'invite', 'provision', 'resend'} and os.environ.get('NOTIFICATIONS_ENABLED') != '1':
        raise HTTPException(503, 'customer email delivery is awaiting sender approval; this action is unavailable')
    try:
        return support.change(str(session['user_id']), str(body.request_key), body.action,
                              str(body.target_id), body.reason, **body.data)
    except (PermissionError, ValueError, TypeError, KeyError) as error:
        db.query("INSERT INTO admin_actions(request_key,actor_id,action,outcome,target_id,reason,request,result) "
                 "VALUES(%s,%s,%s,'failed',%s,%s,%s,%s)",
                 (uuid.uuid4(), session['user_id'], body.action, body.target_id, body.reason,
                  Jsonb({'correlation_key':str(body.request_key)}), Jsonb({'error':type(error).__name__})))
        raise HTTPException(403 if isinstance(error, PermissionError) else 400, str(error))


@router.get('/api/admin/customers')
def customers(q: str = Query('', max_length=160), offset: int = Query(0, ge=0),
              session: dict = Depends(administrator)):
    pattern = '%' + q + '%'
    return db.query('SELECT u.id,u.email,u.name,u.is_admin,u.suspended_at,u.created_at,u.last_login_at '
                    'FROM users u WHERE u.email ILIKE %s OR u.name ILIKE %s OR EXISTS('
                    'SELECT 1 FROM memberships m JOIN workspaces w ON w.id=m.workspace_id '
                    'WHERE m.user_id=u.id AND w.name ILIKE %s) '
                    'ORDER BY u.created_at DESC,u.id LIMIT 50 OFFSET %s', (pattern,pattern,pattern,offset))


@router.get('/api/admin/directory')
def directory(q: str = Query('', max_length=160), offset: int = Query(0, ge=0),
              session: dict = Depends(administrator)):
    return db.query('SELECT w.id,w.name,w.gstin,w.billing_email,w.suspended_at,'
                    '(SELECT count(*) FROM memberships m WHERE m.workspace_id=w.id) AS members,'
                    'COALESCE((SELECT balance_after FROM credit_ledger c WHERE c.workspace_id=w.id '
                    'ORDER BY c.seq DESC LIMIT 1),0) AS balance FROM workspaces w '
                    'WHERE w.archived_at IS NULL AND (w.name ILIKE %s OR w.billing_email ILIKE %s) '
                    'ORDER BY w.created_at DESC,w.id LIMIT 50 OFFSET %s', ('%'+q+'%','%'+q+'%',offset))


@router.get('/api/admin/notifications')
def emails(status: str = Query('', max_length=20), q: str = Query('', max_length=160),
           offset: int = Query(0, ge=0), session: dict = Depends(administrator)):
    return db.query('SELECT id,workspace_id,kind,recipient,status,attempts,last_error,created_at,delivered_at '
                    'FROM notifications WHERE (%s=\'\' OR status=%s) AND recipient ILIKE %s '
                    'ORDER BY created_at DESC,id LIMIT 50 OFFSET %s', (status,status,'%'+q+'%',offset))


@router.get('/api/admin/jobs')
def failed_jobs(offset: int = Query(0, ge=0), session: dict = Depends(administrator)):
    return db.query("SELECT j.id,j.workspace_id,w.name,j.kind,j.status,j.reserved_credits,j.settled_credits,"
                    "j.attempts,j.heartbeat_at,j.created_at,(j.error IS NOT NULL) AS has_error,"
                    "(SELECT count(*) FROM job_images i WHERE i.job_id=j.id) AS images,"
                    "(SELECT COALESCE(sum(delta),0) FROM credit_ledger c WHERE c.job_id=j.id "
                    "AND c.kind IN ('refund','settle')) AS returned_credits "
                    "FROM jobs j JOIN workspaces w ON w.id=j.workspace_id WHERE j.status='failed' OR "
                    "(j.status IN ('queued','running') AND COALESCE(j.heartbeat_at,j.created_at)<now()-interval '10 minutes') "
                    "ORDER BY j.created_at DESC LIMIT 50 OFFSET %s", (offset,))


@router.get('/api/admin/job/{job_id}')
def job_detail(job_id: uuid.UUID, session: dict = Depends(administrator)):
    row = db.query('SELECT id,workspace_id,kind,status,reserved_credits,settled_credits,'
                   'attempts,heartbeat_at,created_at,(error IS NOT NULL) AS has_error '
                   'FROM jobs WHERE id=%s', (job_id,), one=True)
    if not row:
        raise HTTPException(404, 'job not found')
    outputs = db.query("SELECT 'image' AS type,s3_key AS key,'' AS provider FROM job_images WHERE job_id=%s "
                       "UNION ALL SELECT 'video',key,provider FROM job_videos WHERE job_id=%s "
                       "UNION ALL SELECT type,key,provider FROM generated_assets WHERE job_id=%s",
                       (job_id, job_id, job_id))
    for output in outputs:
        output['url'] = storage.presign(output['key'])
    return {'job': row, 'outputs': outputs,
            'provider_reference': None, 'provider_outcome': 'requires provider reconciliation',
            'error': 'Generation failed; consult application logs for the diagnostic.' if row['has_error'] else None,
            'ledger': db.query('SELECT delta,kind,note,created_at FROM credit_ledger '
                               'WHERE job_id=%s ORDER BY seq', (job_id,))}


@router.get('/api/admin/summary')
def summary(session: dict = Depends(administrator)):
    return db.query("SELECT (SELECT count(*) FROM users) AS accounts,"
                    "(SELECT count(*) FROM users WHERE created_at>now()-interval '30 days') AS new_accounts_30d,"
                    "(SELECT count(DISTINCT workspace_id) FROM invoices WHERE paid_at IS NOT NULL) AS paying_workspaces,"
                    "(SELECT COALESCE(sum(amount_paise),0) FROM invoices WHERE paid_at IS NOT NULL) AS payments_paise,"
                    "(SELECT COALESCE(-sum(delta),0) FROM credit_ledger WHERE kind='chargeback') AS reversed_credits,"
                    "(SELECT COALESCE(-sum(delta),0) FROM credit_ledger WHERE kind='reserve')-"
                    "(SELECT COALESCE(sum(delta),0) FROM credit_ledger WHERE kind IN ('refund','settle')) AS credits_consumed,"
                    "(SELECT count(*) FROM jobs WHERE status='failed') AS failed_jobs,"
                    "(SELECT count(*) FROM notifications WHERE status='failed') AS failed_emails", one=True)


@router.get('/api/admin/reconcile/{workspace_id}')
def reconcile(workspace_id: uuid.UUID, session: dict = Depends(administrator)):
    """Read provider evidence; never recreate credits or request a charge/refund."""
    results = []
    for invoice in db.query('SELECT razorpay_payment_id,amount_paise,status FROM invoices '
                            'WHERE workspace_id=%s AND razorpay_payment_id IS NOT NULL '
                            'ORDER BY issued_at DESC LIMIT 50', (workspace_id,)):
        try:
            payment = billing._client().payment.fetch(invoice['razorpay_payment_id'])
            results.append({'payment_id': invoice['razorpay_payment_id'], 'local_status': invoice['status'],
                            'provider_status': payment['status'], 'amount_matches': payment['amount'] == invoice['amount_paise'],
                            'provider_refunded_paise': payment.get('amount_refunded', 0),
                            'currency': payment['currency']})
        except Exception:
            results.append({'payment_id': invoice['razorpay_payment_id'], 'provider_status': 'unavailable'})
    ledger = credits.reconcile(str(workspace_id))
    return {'payments': results, 'ledger_matches': ledger[0] == ledger[1],
            'ledger_total': ledger[0], 'balance': ledger[1], 'limit': 50}


@router.get('/api/admin/customer/{user_id}')
def customer(user_id: uuid.UUID, session: dict = Depends(administrator)):
    user = db.query('SELECT id,email,name,is_admin,suspended_at,created_at,last_login_at '
                   'FROM users WHERE id=%s', (user_id,), one=True)
    if not user:
        raise HTTPException(404, 'customer not found')
    return {'user': user, 'memberships': db.query('SELECT w.id,w.name,w.billing_email,w.gstin,w.suspended_at,m.role '
                'FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=%s', (user_id,)),
            'audit': db.query('SELECT actor_id,action,outcome,reason,created_at FROM admin_actions '
                              'WHERE target_id=%s ORDER BY created_at DESC LIMIT 50', (user_id,))}


@router.get('/api/admin/workspace/{workspace_id}')
def workspace(workspace_id: uuid.UUID, offset: int = Query(0, ge=0),
              session: dict = Depends(administrator)):
    wid = str(workspace_id)
    row = db.query('SELECT id,name,billing_email,gstin,suspended_at,created_at FROM workspaces WHERE id=%s',
                   (wid,), one=True)
    if not row:
        raise HTTPException(404, 'workspace not found')
    return {'workspace': row, 'balance': credits.balance(wid),
            'members': db.query('SELECT u.id,u.email,u.name,m.role,u.suspended_at FROM users u '
                                 'JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=%s', (wid,)),
            'ledger': db.query('SELECT seq,delta,balance_after,kind,job_id,note,created_at FROM credit_ledger '
                               'WHERE workspace_id=%s ORDER BY seq DESC LIMIT 50 OFFSET %s', (wid,offset)),
            'invoices': db.query('SELECT razorpay_invoice_id,razorpay_payment_id,razorpay_subscription_id,'
                                  'credits,amount_paise,status,issued_at,paid_at FROM invoices WHERE workspace_id=%s '
                                  'ORDER BY issued_at DESC LIMIT 50 OFFSET %s', (wid,offset)),
            'subscriptions': db.query('SELECT id,pack,status,charge_at,created_at FROM subscriptions '
                                       'WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 50', (wid,)),
            'audit': db.query('SELECT actor_id,action,outcome,reason,created_at FROM admin_actions '
                              'WHERE target_id=%s ORDER BY created_at DESC LIMIT 50', (wid,))}


@router.post('/api/account/recovery')
def recovery(request: Request, email: str = Form(..., max_length=254)):
    if os.environ.get('NOTIFICATIONS_ENABLED') != '1':
        raise HTTPException(503, 'email recovery is being configured; use Google sign-in or contact support')
    account_actions.request_reset(email, request.client.host if request.client else 'unknown')
    return {'ok': True, 'message': 'If this is an eligible password account, a reset link will arrive shortly.'}


@router.post('/api/account/link')
def inspect_link(request: Request, token: str = Form(..., max_length=110)):
    session = auth.lookup(request.cookies.get(auth.COOKIE))
    row = db.query('SELECT a.kind,a.allow_password,u.password_hash,u.google_sub,a.user_id '
                   'FROM account_actions a JOIN users u ON u.id=a.user_id WHERE a.token_hash=%s '
                   'AND a.consumed_at IS NULL AND a.expires_at>now() AND u.suspended_at IS NULL',
                   (auth.token_hash(token),), one=True)
    if not row:
        raise HTTPException(400, 'invalid or expired link')
    password = row['kind'] == 'reset' or (row['allow_password'] and not row['password_hash'] and not row['google_sub'])
    return {'kind': row['kind'], 'password_required': password,
            'login_required': not password and (not session or str(session['user_id']) != str(row['user_id']))}


@router.post('/api/account/accept')
def accept(request: Request, token: str = Form(..., max_length=110),
           password: str = Form('', max_length=256)):
    try:
        return account_actions.accept(token, password, auth.lookup(request.cookies.get(auth.COOKIE)))
    except PermissionError as error:
        raise HTTPException(403, str(error))
    except ValueError as error:
        raise HTTPException(400, str(error))
