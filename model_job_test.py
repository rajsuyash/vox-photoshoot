"""Real PostgreSQL / HTTP check that a model job actually closes.

_make_talent() never claimed the job it created, so jobs.finish()'s fencing update
(WHERE claimed_by=%s AND status='running') matched zero rows and the job stayed
'queued' forever with no heartbeat — which is exactly what the 1-minute StalledJobs
monitor in notification_worker.py alarms on. See migrations/023_model_jobs_finished.sql
for the production repair.

MODEL_JOB_TEST_DSN=postgresql://postgres@localhost:55432/postgres \
    .venv/bin/python model_job_test.py
"""
import os
import pathlib
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import db

# The exact predicate notification_worker.py alarms on. A regression that leaves a model
# job at 'queued'/'running' with a stale heartbeat must trip this, or the monitor itself
# is untested.
STALLED_PREDICATE = (
    "status IN ('queued','running') "
    "AND COALESCE(heartbeat_at,created_at) < now() - interval '15 minutes'"
)


def main():
    base = os.environ.get('MODEL_JOB_TEST_DSN', '')
    url = urlsplit(base)
    assert url.hostname in {'localhost', '127.0.0.1', '::1'}, 'local test DSN required'
    name = 'vox_model_job_test_' + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ['DATABASE_URL'] = urlunsplit(url._replace(path='/' + name))
    os.environ.pop('S3_BUCKET', None)
    try:
        db.migrate()
        assert db.migrate() == []

        import auth
        import credits
        import talent
        from app import app

        uid = str(auth.create_user('model-job-test@example.com', 'long password')['id'])
        wid = str(db.query("INSERT INTO workspaces(name) VALUES('model job test') "
                           'RETURNING id', one=True)['id'])
        db.query("INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,'owner')",
                 (uid, wid))
        credits.grant(wid, 10, note='test credits', key='model-job-test:grant')
        token = auth.start_session(uid, wid)
        client = TestClient(app)
        client.cookies.set(auth.COOKIE, token)

        def job_row(key):
            return db.query(
                'SELECT * FROM jobs WHERE workspace_id=%s AND idempotency_key=%s',
                (wid, key), one=True)

        def stalled_count(job_id):
            return db.query(
                f'SELECT count(*) AS n FROM jobs WHERE id=%s AND {STALLED_PREDICATE}',
                (job_id,), one=True)['n']

        # --- success path: the job must end 'succeeded', claimed and with a heartbeat ---
        fake_row = {
            'id': 'tlnt00000001', 'name': 'Test Model', 'description': 'a model',
            's3_key': 'talent/tlnt00000001.png', 'source': 'generated',
            'created_at': None, 'last_used_at': None,
        }
        success_key = 'model-job-test:success:' + uuid.uuid4().hex[:8]
        with patch('talent.portrait', return_value=pathlib.Path('/dev/null')), \
             patch('talent.create', return_value=fake_row):
            response = client.post('/api/talent', data={
                'name': 'Test Model', 'skin': 'fair', 'origin': 'Punjabi',
                'idempotency_key': success_key,
            })
        assert response.status_code == 200, response.text

        job = job_row(success_key)
        assert job is not None, 'job row was never created'
        assert job['status'] == 'succeeded', f"expected succeeded, got {job['status']!r}"
        assert job['claimed_by'] is not None, 'job was never claimed'
        assert job['heartbeat_at'] is not None, 'heartbeat was never set'
        assert job['settled_credits'] == 1
        assert stalled_count(job['id']) == 0, 'a succeeded job must never read as stalled'

        # --- failure path: refunded, closed 'failed', still claimed with a heartbeat ---
        failure_key = 'model-job-test:failure:' + uuid.uuid4().hex[:8]
        with patch('talent.portrait', side_effect=RuntimeError('synthetic failure')):
            response = client.post('/api/talent', data={
                'name': 'Test Model 2', 'skin': 'fair', 'origin': 'Punjabi',
                'idempotency_key': failure_key,
            })
        assert response.status_code == 502, response.text

        job = job_row(failure_key)
        assert job is not None, 'job row was never created'
        assert job['status'] == 'failed', f"expected failed, got {job['status']!r}"
        assert job['claimed_by'] is not None, 'job was never claimed'
        assert job['heartbeat_at'] is not None, 'heartbeat was never set'
        assert job['settled_credits'] is None, 'finish() is never told settled_credits on failure'
        assert stalled_count(job['id']) == 0, 'a refunded failed job must never read as stalled'

        refund = db.query(
            "SELECT * FROM credit_ledger WHERE job_id=%s AND kind='refund'",
            (str(job['id']),), one=True)
        assert refund is not None, 'the reserved credit was never refunded'
        assert refund['delta'] == 1

        print('model job lifecycle ok: claimed, closed, no longer reads as stalled')
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
