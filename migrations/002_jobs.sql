-- Jobs and the images they produced.
--
-- This replaces the in-memory JOBS dict, which lost every running shoot on a restart
-- and returned 404 for a job whose images were sitting in S3 perfectly intact.
--
-- job_images is a table rather than a list on the job for a specific reason: two
-- reshoots started seconds apart both read the old list, and the second to finish
-- overwrote the first. One paid image vanished silently. Rows do not race.

CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed');

CREATE TABLE jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES users(id),
    kind             text NOT NULL,
    -- A reshoot is its own job pointing at the shoot it belongs to. Previously it wrote
    -- status onto the parent, so one rejected framing made a customer's three good
    -- images read "generation failed".
    parent_job_id    uuid REFERENCES jobs(id) ON DELETE CASCADE,
    status           job_status NOT NULL DEFAULT 'queued',

    params           jsonb NOT NULL DEFAULT '{}'::jsonb,
    piece_id         text,          -- the upload this was built from
    reserved_credits integer NOT NULL DEFAULT 0,
    settled_credits  integer,       -- NULL until the job stops

    attempts         integer NOT NULL DEFAULT 0,
    claimed_by       text,          -- instance id; the fencing token
    claimed_at       timestamptz,
    heartbeat_at     timestamptz,

    failures         jsonb NOT NULL DEFAULT '[]'::jsonb,
    error            text,
    -- Client-minted, so a double-clicked Generate is one job rather than two charges.
    idempotency_key  text NOT NULL,

    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,

    CONSTRAINT jobs_kind_check CHECK (kind IN ('shoot', 'reshoot', 'retouch')),
    CONSTRAINT jobs_idem_unique UNIQUE (workspace_id, idempotency_key)
);

-- Partial: the table is overwhelmingly finished jobs, and only live ones are swept.
CREATE INDEX jobs_claimable ON jobs (created_at) WHERE status IN ('queued', 'running');
CREATE INDEX jobs_by_workspace ON jobs (workspace_id, created_at DESC);
CREATE INDEX jobs_by_parent ON jobs (parent_job_id) WHERE parent_job_id IS NOT NULL;

CREATE TABLE job_images (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id     uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    -- The shoot this image belongs to: a reshoot's image belongs to the parent gallery.
    shoot_id   uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    framing    text NOT NULL,
    -- Which go at this framing. Attempt 2 is a genuinely different photograph, because
    -- the seed is derived from it — reshoot used to reuse the fixed per-framing seed
    -- and hand back a byte-identical image the customer had just paid for again.
    attempt    integer NOT NULL DEFAULT 1,
    -- The S3 key, never a presigned URL: the URL outlives its signing credentials by
    -- hours, the key by forever.
    s3_key     text NOT NULL,
    seed       integer,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT job_image_unique UNIQUE (shoot_id, framing, attempt)
);

CREATE INDEX job_images_by_shoot ON job_images (shoot_id, framing, attempt DESC);
