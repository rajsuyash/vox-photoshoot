"""Prepaid credits: reserve before the work, settle against what was delivered.

One credit is one generated image. Not a bundle — a shoot is three generations, and
pricing it at five means a partial result (two of three framings, which is normal) needs
integer arithmetic on money and the answer is always slightly wrong. At 1:1 settlement is
`len(delivered)` and the refund is exact, and the sentence a customer hears is
"you pay for images you receive", which needs no policy to defend.

Charge BEFORE the provider call, always. Generate-then-debit hands free work to anyone
who kills the connection, and makes a container death unbillable. The reserve/refund
machinery exists so that charging first is safe.

    .venv/bin/python credits.py       # self-check against DATABASE_URL
"""

import os
import uuid

import db
import locations

# What each action costs, in images. Computed server-side and pinned onto the job at
# reserve time — never re-derived at settle, or a price change silently re-prices work
# that is already in flight, and never taken from the client.
COST = {
    'shoot': len(locations.FRAMINGS),
    'reshoot': 1,
    'retouch': 1,
}


class Insufficient(Exception):
    """Not enough credits. The caller turns this into a 402."""


def balance(workspace_id: str) -> int:
    """Current balance: the newest ledger row. One tuple, index-only, O(1) forever."""
    row = db.query(
        'SELECT balance_after FROM credit_ledger WHERE workspace_id = %s '
        'ORDER BY seq DESC LIMIT 1', (workspace_id,), one=True)
    return int(row['balance_after']) if row else 0


def _append(conn, workspace_id: str, delta: int, kind: str, idempotency_key: str,
            job_id: str | None = None, note: str | None = None,
            require_funds: bool = False) -> int:
    """Write one ledger row inside the caller's transaction. Returns the new balance.

    Assumes the workspace row is already locked when require_funds is set — see
    reserve(). Returns the current balance unchanged if this key was already written,
    which is what makes every caller safely repeatable.
    """
    existing = conn.execute(
        'SELECT balance_after FROM credit_ledger '
        'WHERE workspace_id = %s AND idempotency_key = %s',
        (workspace_id, idempotency_key)).fetchone()
    if existing:
        return int(existing[0])

    row = conn.execute(
        'SELECT seq, balance_after FROM credit_ledger WHERE workspace_id = %s '
        'ORDER BY seq DESC LIMIT 1', (workspace_id,)).fetchone()
    seq, current = (row[0], row[1]) if row else (0, 0)

    if require_funds and current + delta < 0:
        raise Insufficient(f'needs {-delta} credits, has {current}')

    conn.execute(
        """INSERT INTO credit_ledger (workspace_id, seq, delta, balance_after, kind,
                                      job_id, idempotency_key, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (workspace_id, seq + 1, delta, current + delta, kind, job_id,
         idempotency_key, note))
    return current + delta


def reserve(conn, workspace_id: str, job_id: str, cost: int) -> int:
    """Take the credits for a job. Must run in the transaction that creates the job.

    Job-then-crash gives away free work; reserve-then-crash debits a customer for a job
    that does not exist and that nothing will ever sweep, so the credits vanish with no
    trace. One transaction, both rows.

    The lock is taken on the workspace row because an append-only table has no row to
    lock. Without it, two concurrent shoots both read balance=3, both insert -3, and
    both commit: the ledger faithfully records a double-spend.
    """
    conn.execute('SELECT 1 FROM workspaces WHERE id = %s FOR UPDATE', (workspace_id,))
    return _append(conn, workspace_id, -cost, 'reserve', f'reserve:{job_id}',
                   job_id=job_id, require_funds=True)


def settle(job_id: str, delivered: int) -> None:
    """Give back the difference between what was reserved and what was delivered.

    Called once when a job stops, for any reason. Writing nothing when the whole reserve
    was used is deliberate: a delta of zero is not an event.
    """
    job = db.query(
        'SELECT workspace_id, reserved_credits FROM jobs WHERE id = %s',
        (job_id,), one=True)
    if job is None:
        return
    owed = int(job['reserved_credits']) - int(delivered)
    if owed <= 0:
        return
    kind = 'refund' if delivered == 0 else 'settle'
    note = f'{delivered} of {job["reserved_credits"]} images delivered'
    with db.tx() as conn:
        _append(conn, str(job['workspace_id']), owed, kind, f'settle:{job_id}',
                job_id=job_id, note=note)


def grant(workspace_id: str, amount: int, note: str = 'manual grant') -> int:
    """Admin goodwill, or the free credits that come with an account.

    This is also the answer to "was that bad image the customer's fault" — a human
    decides and it is recorded, rather than becoming a discount rule in code.
    """
    with db.tx() as conn:
        return _append(conn, workspace_id, amount, 'grant',
                       f'grant:{uuid.uuid4()}', note=note)


def purchase(workspace_id: str, amount: int, payment_id: str, note: str = '') -> int:
    """Credits bought. Keyed on the PAYMENT id, not the webhook event id.

    A single Razorpay payment emits several events with different ids; keying on the
    event would let two of them credit the same money twice while the dedupe sat there
    looking correct. The payment id is the identity of the money.
    """
    with db.tx() as conn:
        return _append(conn, workspace_id, amount, 'purchase',
                       f'razorpay:{payment_id}', note=note or payment_id)


def ledger(workspace_id: str, limit: int = 100) -> list[dict]:
    return db.query(
        """SELECT seq, delta, balance_after, kind, job_id, note, created_at
             FROM credit_ledger WHERE workspace_id = %s
         ORDER BY seq DESC LIMIT %s""", (workspace_id, limit))


def reconcile(workspace_id: str) -> tuple[int, int]:
    """(sum of the ledger, balance on the newest row). They must be equal.

    Cheap, and the one check that catches a bug in seq or balance_after on the day it
    happens rather than at an audit.
    """
    row = db.query(
        """SELECT COALESCE(sum(delta), 0) AS total,
                  COALESCE((SELECT balance_after FROM credit_ledger
                             WHERE workspace_id = %s ORDER BY seq DESC LIMIT 1), 0) AS tail
             FROM credit_ledger WHERE workspace_id = %s""",
        (workspace_id, workspace_id), one=True)
    return int(row['total']), int(row['tail'])


def demo() -> None:
    if not os.environ.get('DATABASE_URL'):
        print('credits: DATABASE_URL not set, skipping')
        return
    import jobs

    db.migrate()
    assert COST['shoot'] == len(locations.FRAMINGS), 'a shoot must cost one per image'

    ws = str(db.query("INSERT INTO workspaces (name) VALUES ('credits-check') "
                      'RETURNING id', one=True)['id'])
    user = str(db.query(
        "INSERT INTO users (email, password_hash) VALUES (%s,'x') RETURNING id",
        (f'cr-{uuid.uuid4().hex[:8]}@test',), one=True)['id'])

    assert balance(ws) == 0
    assert grant(ws, 10, 'welcome') == 10

    # Reserve and job creation are one transaction.
    with db.tx() as conn:
        job = jobs.create(ws, user, 'shoot', 'k1', {}, reserved_credits=3, conn=conn)
        assert reserve(conn, ws, str(job['id']), 3) == 7
    assert balance(ws) == 7, 'reserve did not commit with the job'

    # Two of three framings delivered: one credit comes back, not two, not zero.
    settle(str(job['id']), delivered=2)
    assert balance(ws) == 8, balance(ws)
    settle(str(job['id']), delivered=2)          # a retry must not pay twice
    assert balance(ws) == 8, 'settle was not idempotent'

    # A job that delivered nothing is fully refunded.
    with db.tx() as conn:
        dead = jobs.create(ws, user, 'shoot', 'k2', {}, reserved_credits=3, conn=conn)
        reserve(conn, ws, str(dead['id']), 3)
    assert balance(ws) == 5
    settle(str(dead['id']), delivered=0)
    assert balance(ws) == 8, 'a failed job must cost nothing'

    # Spending more than the balance must fail, and must leave nothing behind.
    before = balance(ws)
    try:
        with db.tx() as conn:
            broke = jobs.create(ws, user, 'shoot', 'k3', {}, reserved_credits=999,
                                conn=conn)
            reserve(conn, ws, str(broke['id']), 999)
    except Insufficient:
        pass
    else:
        raise AssertionError('overdraft allowed')
    assert balance(ws) == before, 'a refused reserve moved the balance'
    assert db.query("SELECT count(*) AS n FROM jobs WHERE workspace_id=%s "
                    "AND idempotency_key='k3'", (ws,), one=True)['n'] == 0, \
        'the job survived a rolled-back reserve'

    # A repeated payment id credits once, however many times the webhook arrives.
    assert purchase(ws, 500, 'pay_abc') == before + 500
    assert purchase(ws, 500, 'pay_abc') == before + 500, 'double-credited a payment'

    total, tail = reconcile(ws)
    assert total == tail == balance(ws), (total, tail)

    db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws,))
    db.query('DELETE FROM jobs WHERE workspace_id = %s', (ws,))
    db.query('DELETE FROM workspaces WHERE id = %s', (ws,))
    db.query('DELETE FROM users WHERE id = %s', (user,))
    db.close()
    print('credits ok')


if __name__ == '__main__':
    demo()
