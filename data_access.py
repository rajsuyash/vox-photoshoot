"""Owner-scoped JSON export and deletion requests. Purge requires an approved retention policy."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import auth
import db

router = APIRouter()


def owner(session):
    workspace = auth.current_workspace(session)
    if session['is_admin']:
        auth.require_admin(session)
    else:
        row = db.query('SELECT role FROM memberships WHERE user_id=%s AND workspace_id=%s',
                       (session['user_id'],workspace), one=True)
        if not row or row['role'] != 'owner':
            raise HTTPException(403,'workspace owner required')
    return workspace


def export(workspace, offset=0):
    """Each bounded page is a consistent snapshot. Tokens, passwords and internal prompts are excluded."""
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        with conn.cursor(row_factory=dict_row) as cur:
            brand = cur.execute('SELECT id,name,gstin,billing_email,created_at FROM workspaces WHERE id=%s',
                                (workspace,)).fetchone()
            if not brand:
                raise HTTPException(404,'workspace not found')
            result = {'workspace':brand, 'page_offset':offset, 'page_size':100,
                      'format':'Donna Photoshoot workspace metadata export; media stays available in History',
                      'snapshot':'Each page is a separate snapshot; request during a quiet period for multi-page consistency.'}
            queries = {
                'members': 'SELECT u.email,u.name,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=%s ORDER BY u.id LIMIT 100 OFFSET %s',
                'credits': 'SELECT seq,delta,balance_after,kind,note,created_at FROM credit_ledger WHERE workspace_id=%s ORDER BY seq LIMIT 100 OFFSET %s',
                'invoices': 'SELECT razorpay_invoice_id,razorpay_payment_id,credits,amount_paise,status,issued_at,paid_at FROM invoices WHERE workspace_id=%s ORDER BY issued_at,id LIMIT 100 OFFSET %s',
                'jobs': 'SELECT id,kind,status,reserved_credits,settled_credits,created_at,finished_at FROM jobs WHERE workspace_id=%s ORDER BY created_at,id LIMIT 100 OFFSET %s',
                'images': 'SELECT i.job_id,i.framing,i.attempt,i.s3_key,i.created_at FROM job_images i JOIN jobs j ON j.id=i.job_id WHERE j.workspace_id=%s ORDER BY i.created_at,i.id LIMIT 100 OFFSET %s',
                'products': 'SELECT id AS piece_id,sku,description,created_at FROM pieces WHERE workspace_id=%s ORDER BY created_at,id LIMIT 100 OFFSET %s',
            }
            for key, query in queries.items():
                result[key] = cur.execute(query,(workspace,offset)).fetchall()
            result['next_offset'] = offset+100 if any(len(result[k])==100 for k in queries) else None
            return result


@router.get('/api/account/export')
def my_export(offset: int = Query(0,ge=0),session: dict = Depends(auth.current_session)):
    return export(owner(session),offset)


@router.get('/api/admin/export/{workspace_id}')
def admin_export(workspace_id: uuid.UUID, offset: int = Query(0,ge=0),
                 session: dict = Depends(auth.current_session)):
    auth.require_admin(session)
    result = export(str(workspace_id),offset)
    # The support read is attributed without recording exported customer content.
    with db.tx() as conn:
        conn.execute('INSERT INTO admin_actions(request_key,actor_id,action,target_id,reason,request,result) '
                     'VALUES(%s,%s,\'export\',%s,\'support workspace export\',%s,%s)',
                     (uuid.uuid4(),session['user_id'],workspace_id,Jsonb({'offset':offset}),Jsonb({'page_size':100})))
    return result


@router.post('/api/account/deletion')
def request_deletion(request_key: uuid.UUID = Form(...), reason: str = Form(...,min_length=3,max_length=500),
                     confirm_name: str = Form(...,max_length=160), session: dict = Depends(auth.current_session)):
    workspace = owner(session)
    reason = reason.strip()
    if len(reason)<3:
        raise HTTPException(400,'give a reason')
    with db.tx() as conn:
        row = conn.execute('SELECT name FROM workspaces WHERE id=%s FOR UPDATE',(workspace,)).fetchone()
        if row[0] != confirm_name:
            raise HTTPException(400,'type the workspace name to confirm')
        replay = conn.execute('SELECT workspace_id,reason,status FROM data_requests WHERE id=%s', (request_key,)).fetchone()
        if replay:
            if str(replay[0]) != workspace or replay[1] != reason:
                raise HTTPException(400,'request key already used')
            return {'id':str(request_key),'status': 'pending review' if replay[2] != 'rejected' else 'rejected',
                    'purge_enabled':False}
        prior = conn.execute('SELECT id,reason FROM data_requests WHERE workspace_id=%s '
                             "AND status IN ('requested','reviewing')",(workspace,)).fetchone()
        if prior:
            return {'id':str(prior[0]),'status':'pending review','purge_enabled':False}
        conn.execute('INSERT INTO data_requests(id,workspace_id,user_id,reason) VALUES(%s,%s,%s,%s)',
                     (request_key,workspace,session['user_id'],reason))
        return {'id':str(request_key),'status':'pending review','purge_enabled':False}


@router.get('/api/admin/data-requests')
def requests(offset: int = Query(0,ge=0),session: dict = Depends(auth.current_session)):
    auth.require_admin(session)
    return db.query('SELECT r.id,r.workspace_id,w.name,r.user_id,r.reason,r.status,r.review_note,r.created_at '
                    'FROM data_requests r JOIN workspaces w ON w.id=r.workspace_id '
                    'ORDER BY r.created_at DESC,r.id LIMIT 50 OFFSET %s',(offset,))
