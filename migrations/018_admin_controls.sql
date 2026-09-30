ALTER TABLE users ADD COLUMN suspended_at timestamptz;
ALTER TABLE workspaces ADD COLUMN suspended_at timestamptz;

CREATE TABLE admin_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key uuid NOT NULL UNIQUE,
    actor_id uuid REFERENCES users(id) ON DELETE SET NULL,
    action text NOT NULL,
    outcome text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success', 'failed')),
    target_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
    request jsonb NOT NULL,
    result jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_actions_target ON admin_actions(target_id, created_at DESC);
