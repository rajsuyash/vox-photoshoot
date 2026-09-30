CREATE TABLE account_actions (
    id uuid PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('invite','reset')),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    role text CHECK (role IN ('owner','member')),
    allow_password boolean NOT NULL DEFAULT false,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_actions_user ON account_actions(user_id, created_at DESC);
CREATE TABLE recovery_attempts (
    email_hash text NOT NULL,
    ip_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recovery_attempts_email ON recovery_attempts(email_hash,created_at);
CREATE INDEX recovery_attempts_ip ON recovery_attempts(ip_hash,created_at);
ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('welcome','receipt','refund','invite','reset'));
