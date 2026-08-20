-- The credit ledger. Append-only: rows are written, never updated or deleted.
--
-- A mutable balance column cannot answer "where did my 40 credits go", which a customer
-- will eventually ask, and an admin goodwill grant, a partial refund and a Razorpay
-- chargeback all have to be explainable afterwards.

CREATE TABLE credit_ledger (
    id              bigserial PRIMARY KEY,
    workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    -- Per-workspace and monotonic. Computed as max+1 inside the reserve transaction, so
    -- two writers that both skipped the lock collide on the unique index and the second
    -- gets a loud error instead of quietly double-spending.
    seq             bigint NOT NULL,
    delta           integer NOT NULL,
    -- The running balance. Reading the newest row is O(1) and cannot drift from the
    -- ledger, because it IS the ledger — unlike a cached column on workspaces.
    balance_after   integer NOT NULL,

    kind            text NOT NULL,
    job_id          uuid REFERENCES jobs(id) ON DELETE RESTRICT,

    -- Derived from the thing that happened ('reserve:<job>', 'razorpay:<payment>'),
    -- never from the request. One index defeats a double-clicked Generate, a settle
    -- that runs twice on retry, and Razorpay delivering the same webhook again.
    idempotency_key text NOT NULL,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ledger_seq_unique  UNIQUE (workspace_id, seq),
    CONSTRAINT ledger_idem_unique UNIQUE (workspace_id, idempotency_key),
    CONSTRAINT ledger_kind_check  CHECK (kind IN
        ('purchase', 'reserve', 'settle', 'refund', 'grant', 'chargeback')),
    CONSTRAINT ledger_delta_check CHECK (delta <> 0)
);

-- The balance read: newest row for a workspace, index-only.
CREATE INDEX ledger_ws_seq ON credit_ledger (workspace_id, seq DESC)
    INCLUDE (delta, balance_after);

-- Deliberately NO CHECK (balance_after >= 0). A chargeback has to be recordable even
-- after the credits are spent; a ledger that cannot represent "you owe me 4" forces
-- someone to falsify it. Non-negativity is enforced in the spend path instead.
