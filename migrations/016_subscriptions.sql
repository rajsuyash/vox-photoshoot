CREATE TABLE subscription_plans (
    key_id text NOT NULL,
    pack text NOT NULL,
    plan_id text NOT NULL,
    credits integer NOT NULL CHECK (credits > 0),
    amount_paise bigint NOT NULL CHECK (amount_paise > 0),
    PRIMARY KEY (key_id, pack)
);

CREATE TABLE subscriptions (
    id text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    plan_id text NOT NULL,
    pack text NOT NULL,
    credits integer NOT NULL CHECK (credits > 0),
    amount_paise bigint NOT NULL CHECK (amount_paise > 0),
    status text NOT NULL CHECK (status IN ('created', 'authenticated', 'active',
        'pending', 'halted', 'paused', 'cancelled', 'completed', 'expired')),
    charge_at bigint,
    short_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One mandate per workspace, including an unfinished checkout.
CREATE UNIQUE INDEX subscriptions_one_open ON subscriptions(workspace_id)
    WHERE status NOT IN ('cancelled', 'completed', 'expired');
CREATE INDEX subscriptions_workspace ON subscriptions(workspace_id, created_at DESC);

ALTER TABLE invoices ADD COLUMN razorpay_subscription_id text
    REFERENCES subscriptions(id) ON DELETE CASCADE;
CREATE INDEX invoices_subscription ON invoices(razorpay_subscription_id);
