"""Jobs, the images they produce, and recovering the ones a dead container left behind.

Replaces the in-memory JOBS dict. That dict lost every running shoot on a restart while
the images sat in S3 perfectly intact, and returned 404 for them afterwards because the
only thing that knew they existed had died with the process.

Three rules hold everything together:

  fencing      every write says `AND claimed_by = me`. A container that stalled and then
               resumed must not overwrite a job that was already given up on and
               refunded, or the customer gets images they were refunded for.
  per-image    images are rows written as each one lands, not a list written at the end.
               Two reshoots seconds apart both used to read the old list and the second
               overwrote the first, losing a paid image.
  attempts     a reshoot is a new attempt with its own seed and its own key, so it
               cannot return or overwrite the image it is replacing.

    .venv/bin/python jobs.py          # self-check against DATABASE_URL
"""

import json
import os
import socket
import uuid

import db

# Identifies this container in claimed_by. Hostname alone is not enough — App Runner can
# reuse one — so a per-process uuid is appended.
INSTANCE = f'{socket.gethostname()}:{uuid.uuid4().hex[:8]}'

# How long a job may go without a heartbeat before it is treated as abandoned.
# Deliberately longer than the worst case for ONE framing, not the average whole shoot:
# hf's retry and backoff can reach ~9 minutes on a single pathological framing, and
# reaping a job that is merely slow refunds a customer for work they are about to get.
STALE_MINUTES = 10


def create(workspace_id: str, user_id: str, kind: str, idempotency_key: str,
           params: dict, piece_id: str | None = None, reserved_credits: int = 0,
           parent_job_id: str | None = None, conn=None) -> dict:
    """Insert a job, or return the existing one for a repeated idempotency key.

    The repeat is not an error: it is a double-clicked Generate, a retried request, or a
    second tab. Returning the original job means the customer sees their shoot and is
    charged once. Pass conn to enrol this in a caller's transaction — the credit reserve
    must commit with the job or not at all.
    """
    sql = """
        INSERT INTO jobs (workspace_id, user_id, kind, parent_job_id, params,
                          piece_id, reserved_credits, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
        RETURNING id, kind, status, reserved_credits
    """
    args = (workspace_id, user_id, kind, parent_job_id, json.dumps(params),
            piece_id, reserved_credits, idempotency_key)

    def run(cursor):
        cursor.execute(sql, args)
        row = cursor.fetchone()
        if row is None:  # the key already existed
            cursor.execute(
                """SELECT id, kind, status, reserved_credits FROM jobs
                    WHERE workspace_id = %s AND idempotency_key = %s""",
                (workspace_id, idempotency_key))
            return {**cursor.fetchone(), 'created': False}
        return {**row, 'created': True}

    from psycopg.rows import dict_row
    if conn is not None:
        with conn.cursor(row_factory=dict_row) as cursor:
            return run(cursor)
    with db.connect() as owned:
        with owned.cursor(row_factory=dict_row) as cursor:
            return run(cursor)


def claim(job_id: str) -> bool:
    """Take ownership. False means someone else already has it — do nothing."""
    row = db.query(
        """UPDATE jobs
              SET status = 'running', claimed_by = %s, claimed_at = now(),
                  heartbeat_at = now(), attempts = attempts + 1,
                  started_at = COALESCE(started_at, now())
            WHERE id = %s AND status = 'queued'
        RETURNING id""",
        (INSTANCE, job_id), one=True)
    return row is not None


def heartbeat(job_id: str) -> None:
    db.query('UPDATE jobs SET heartbeat_at = now() WHERE id = %s AND claimed_by = %s',
             (job_id, INSTANCE))


def next_attempt(shoot_id: str, framing: str) -> int:
    """Which go this is at a framing. Drives both the seed and the storage key."""
    row = db.query(
        'SELECT COALESCE(max(attempt), 0) + 1 AS n FROM job_images '
        'WHERE shoot_id = %s AND framing = %s', (shoot_id, framing), one=True)
    return int(row['n'])


def add_image(job_id: str, shoot_id: str, framing: str, attempt: int,
              s3_key: str, seed: int | None) -> None:
    """Record one delivered image. Called the moment it lands, not at the end."""
    db.query(
        """INSERT INTO job_images (job_id, shoot_id, framing, attempt, s3_key, seed)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (shoot_id, framing, attempt) DO NOTHING""",
        (job_id, shoot_id, framing, attempt, s3_key, seed))


def image_count(job_id: str) -> int:
    row = db.query('SELECT count(*) AS n FROM job_images WHERE job_id = %s',
                   (job_id,), one=True)
    return int(row['n'])


def finish(job_id: str, status: str, failures=None, error: str | None = None,
           settled_credits: int | None = None) -> bool:
    """Close a job we still own. False means we were reaped; discard the result."""
    row = db.query(
        """UPDATE jobs
              SET status = %s, failures = %s, error = %s, settled_credits = %s,
                  finished_at = now()
            WHERE id = %s AND claimed_by = %s AND status = 'running'
        RETURNING id""",
        (status, json.dumps(failures or []), error, settled_credits, job_id, INSTANCE),
        one=True)
    return row is not None


def get(job_id: str, workspace_id: str) -> dict | None:
    """One job, scoped to its workspace. The scope is the authorisation — without it,
    knowing a job id is enough to read, and to reshoot, someone else's paid work."""
    job = db.query(
        """SELECT id, kind, status, params, piece_id, parent_job_id, failures, error,
                  reserved_credits, settled_credits, created_at, finished_at
             FROM jobs WHERE id = %s AND workspace_id = %s""",
        (job_id, workspace_id), one=True)
    if job is None:
        return None
    job['images'] = images_for(str(job['parent_job_id'] or job['id']))
    return job


def images_for(shoot_id: str) -> list[dict]:
    """The current gallery: the newest attempt at each framing, in framing order.

    Older attempts stay in the table — the customer paid for them and may prefer one —
    but the gallery shows the latest.
    """
    return db.query(
        """SELECT DISTINCT ON (framing) framing, attempt, s3_key, seed
             FROM job_images WHERE shoot_id = %s
         ORDER BY framing, attempt DESC""", (shoot_id,))


def history(workspace_id: str, limit: int = 50) -> list[dict]:
    """Shoots and retouches for the workspace, newest first. Reshoots are folded into
    the shoot they belong to rather than listed as separate work."""
    return db.query(
        """SELECT j.id, j.kind, j.status, j.params, j.created_at, j.finished_at,
                  (SELECT count(*) FROM job_images i WHERE i.shoot_id = j.id) AS images
             FROM jobs j
            WHERE j.workspace_id = %s AND j.parent_job_id IS NULL
         ORDER BY j.created_at DESC LIMIT %s""",
        (workspace_id, limit))


def sweep() -> list[dict]:
    """Fail every job whose container died, and report what to refund.

    Runs from a real request rather than a timer: App Runner throttles CPU between
    requests, so a background loop is exactly the thing that does not run when needed.
    The frontend polls every 3 seconds, so there is always a heartbeat when it matters.

    Deliberately does NOT retry. The dead container may already have spent money at fal
    and there is no way to ask fal what it did, so re-running risks charging twice.
    """
    with db.connect() as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(8675309)').fetchone()[0]:
            return []            # another instance is already sweeping
        try:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """UPDATE jobs
                          SET status = 'failed', finished_at = now(),
                              error = 'interrupted — the server restarted mid-job'
                        WHERE status = 'running'
                          AND heartbeat_at < now() - make_interval(mins => %s)
                    RETURNING id, workspace_id, reserved_credits,
                              (SELECT count(*) FROM job_images i WHERE i.job_id = jobs.id)
                                  AS delivered""",
                    (STALE_MINUTES,))
                return cursor.fetchall()
        finally:
            conn.execute('SELECT pg_advisory_unlock(8675309)')


def demo() -> None:
    if not os.environ.get('DATABASE_URL'):
        print('jobs: DATABASE_URL not set, skipping')
        return
    db.migrate()

    ws = db.query("INSERT INTO workspaces (name) VALUES ('jobs-selfcheck') RETURNING id",
                  one=True)['id']
    user = db.query(
        "INSERT INTO users (email, password_hash) VALUES (%s,'x') RETURNING id",
        (f'jobs-{uuid.uuid4().hex[:8]}@test',), one=True)['id']

    job = create(ws, user, 'shoot', 'idem-1', {'a': 1}, reserved_credits=3)
    assert job['created'] is True

    # A repeated key is a double-click, not a second job. This is the whole defence
    # against being charged twice for one Generate.
    again = create(ws, user, 'shoot', 'idem-1', {'a': 1}, reserved_credits=3)
    assert again['created'] is False and again['id'] == job['id']

    jid = str(job['id'])
    assert claim(jid) is True
    assert claim(jid) is False, 'a claimed job must not be claimable twice'

    # Attempts advance per framing, which is what makes a reshoot a new photograph.
    assert next_attempt(jid, 'hero') == 1
    add_image(jid, jid, 'hero', 1, 'shoots/x/hero-1.png', 101)
    assert next_attempt(jid, 'hero') == 2
    assert next_attempt(jid, 'profile') == 1, 'framings must count independently'

    # A second attempt does not replace the first: both were paid for.
    add_image(jid, jid, 'hero', 2, 'shoots/x/hero-2.png', 1101)
    gallery = images_for(jid)
    assert len(gallery) == 1 and gallery[0]['attempt'] == 2, gallery
    assert db.query('SELECT count(*) AS n FROM job_images WHERE shoot_id = %s',
                    (jid,), one=True)['n'] == 2, 'the earlier attempt was destroyed'

    assert finish(jid, 'succeeded', settled_credits=2) is True
    assert finish(jid, 'succeeded') is False, 'a finished job must not finish twice'

    # Reading is scoped to the workspace: a job id alone must not be enough.
    assert get(jid, str(ws)) is not None
    other = db.query("INSERT INTO workspaces (name) VALUES ('other') RETURNING id",
                     one=True)['id']
    assert get(jid, str(other)) is None, 'a job leaked across workspaces'

    # Fencing: a job claimed by someone else cannot be closed by us.
    stolen = create(ws, user, 'shoot', 'idem-2', {})
    sid = str(stolen['id'])
    db.query("UPDATE jobs SET status='running', claimed_by='someone-else' WHERE id=%s",
             (sid,))
    assert finish(sid, 'succeeded') is False, 'fencing token ignored'

    # The sweeper only takes jobs that have actually gone quiet.
    db.query("UPDATE jobs SET claimed_by=%s, heartbeat_at = now() WHERE id=%s",
             (INSTANCE, sid))
    assert not any(str(r['id']) == sid for r in sweep()), 'reaped a live job'
    db.query("UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id=%s",
             (sid,))
    assert any(str(r['id']) == sid for r in sweep()), 'did not reap a dead job'

    db.query('DELETE FROM workspaces WHERE id IN (%s, %s)', (ws, other))
    db.query('DELETE FROM users WHERE id = %s', (user,))
    db.close()
    print('jobs ok')


if __name__ == '__main__':
    demo()
