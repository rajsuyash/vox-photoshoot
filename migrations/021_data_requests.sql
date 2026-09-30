CREATE TABLE data_requests (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
    status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','reviewing','rejected')),
    reviewer_id uuid REFERENCES users(id) ON DELETE SET NULL,
    review_note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);
CREATE UNIQUE INDEX data_requests_open ON data_requests(workspace_id) WHERE status IN ('requested','reviewing');
