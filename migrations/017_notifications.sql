-- Durable transaction emails; enqueue commits with the originating business event.
CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('welcome', 'receipt', 'refund')),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE SET NULL,
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    recipient text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued', 'sending', 'accepted', 'delivered', 'bounced', 'complained',
         'failed', 'suppressed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token uuid,
    provider_message_id text UNIQUE,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz,
    delivered_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_due ON notifications(next_attempt_at, created_at)
    WHERE status IN ('queued', 'sending');
CREATE INDEX notifications_workspace ON notifications(workspace_id, created_at DESC);
-- Feedback may precede the sender's acceptance write. Keep it until correlation exists.
CREATE TABLE notification_feedback (
    provider_message_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('delivered', 'bounced', 'complained')),
    suppress boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider_message_id, status)
);
CREATE TABLE notification_suppressions (
    recipient text PRIMARY KEY CHECK (recipient = lower(recipient)),
    reason text NOT NULL CHECK (reason IN ('bounced', 'complained')),
    created_at timestamptz NOT NULL DEFAULT now()
);
