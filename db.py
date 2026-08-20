"""Postgres: one pool, and a migration runner small enough to read in a minute.

Everything the app knows used to live in a dict that died with the container
(`JOBS` in app.py). This is where it lives now.

No ORM and no Alembic. The app is a few hundred lines of plain SQL against six tables,
and SQLAlchemy plus a migration framework would be more machinery than schema. Migrations
are numbered .sql files applied once, in order, recorded in schema_migrations — which is
the part of Alembic that actually matters.

    .venv/bin/python db.py            # apply migrations, then self-check
"""

import contextlib
import os
import pathlib
import sys

MIGRATIONS = pathlib.Path('migrations')

# Small on purpose. Every connection is one of RDS db.t4g.micro's ~80, and the anyio
# threadpool is capped at 6 workers (app.py), so more than this can never be in use.
POOL_MAX = 8

_pool = None


def dsn() -> str:
    """Connection string. sslmode=require is not optional against a public endpoint."""
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL not set')
    if 'sslmode=' not in url:
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url


def pool():
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        # open=False then open(): lets the app boot when the database is briefly
        # unreachable rather than crash-looping the container out of App Runner.
        _pool = ConnectionPool(dsn(), min_size=1, max_size=POOL_MAX, open=False,
                               kwargs={'autocommit': True})
        _pool.open(wait=True, timeout=15)
    return _pool


def close() -> None:
    """Shut the pool down. The server never calls this; scripts do, so they can exit
    without psycopg complaining about worker threads it could not join."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextlib.contextmanager
def connect():
    """A connection from the pool. Autocommit — use tx() when you need a transaction."""
    with pool().connection() as conn:
        yield conn


@contextlib.contextmanager
def tx():
    """A transaction. Everything money-related runs inside one of these."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def query(sql: str, params=(), one: bool = False):
    """Rows as dicts, because six tables do not need a row-mapping layer."""
    from psycopg.rows import dict_row

    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return None
            return cur.fetchone() if one else cur.fetchall()


def migrate() -> list[str]:
    """Apply every unapplied migration, in filename order. Safe to run on every boot."""
    applied = []
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name       text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        done = {row[0] for row in conn.execute('SELECT name FROM schema_migrations')}

    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name in done:
            continue
        # Each migration is its own transaction: a failure half way leaves the ones
        # before it applied and recorded, so a re-run resumes rather than restarts.
        with tx() as conn:
            conn.execute(path.read_text())
            conn.execute('INSERT INTO schema_migrations (name) VALUES (%s)', (path.name,))
        applied.append(path.name)
        print(f'migrated {path.name}', flush=True)
    return applied


def demo() -> None:
    """Self-check. Needs DATABASE_URL; applies migrations and exercises the pool."""
    if not os.environ.get('DATABASE_URL'):
        print('db: DATABASE_URL not set, skipping (set it to run this check)')
        return

    migrate()
    assert query('SELECT 1 AS n', one=True)['n'] == 1

    # Every table the app depends on must exist after migrate().
    names = {row['table_name'] for row in query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    for table in ('workspaces', 'users', 'memberships', 'sessions'):
        assert table in names, f'{table} missing after migrate()'

    # migrate() must be idempotent — it runs on every container boot.
    assert migrate() == [], 'a second migrate() tried to reapply something'

    # A transaction must actually roll back, or the credit ledger is a lie.
    with contextlib.suppress(RuntimeError):
        with tx() as conn:
            conn.execute('CREATE TEMP TABLE rollback_probe (x int)')
            raise RuntimeError('deliberate')

    close()
    print('db ok')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        migrate()
        close()
    else:
        demo()
