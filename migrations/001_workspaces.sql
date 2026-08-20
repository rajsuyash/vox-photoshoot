-- Who logs in, and whose workspace they are in.
--
-- Sales-led: there is no public signup. An admin creates the workspace and the owner
-- account. A user may belong to more than one workspace (a group with several brands),
-- which is why membership is its own table rather than a column on users.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE TABLE workspaces (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    -- Both needed on a GST invoice. Nullable so a workspace can exist before the
    -- paperwork does; billing refuses to raise an invoice without them.
    gstin         text,
    billing_email text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    archived_at   timestamptz
);

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL,
    password_hash text NOT NULL,
    name          text,
    is_admin      boolean NOT NULL DEFAULT false,   -- you, not the client
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- Case-insensitive: nobody should be able to register Owner@brand.com alongside
-- owner@brand.com, and everyone types their address differently.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));

CREATE TABLE memberships (
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role         text NOT NULL DEFAULT 'member',
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, workspace_id),
    CONSTRAINT memberships_role_check CHECK (role IN ('owner', 'member'))
);

CREATE INDEX memberships_by_workspace ON memberships (workspace_id);

CREATE TABLE sessions (
    -- The sha256 of the cookie value, never the value itself: a database dump must not
    -- hand out live sessions.
    token_hash   text PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Which workspace this session is looking at. A user with two workspaces switches
    -- by updating this, so the choice survives a page load.
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sessions_expiry ON sessions (expires_at);
