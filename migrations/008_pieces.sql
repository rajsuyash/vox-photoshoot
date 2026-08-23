-- The product library.
--
-- An upload was already durable — /api/pieces puts it in S3 before anything reads it —
-- but nothing remembered whose it was or what it turned out to be. The id came back to
-- one browser, was posted to /api/shoots, and was then reachable only through the job
-- rows that happened to reference it. So a customer with forty SKUs re-uploaded the same
-- photograph every time they wanted another shoot of the same ring.
--
-- It also closes an authorization hole. piece_id arrived on /api/shoots as free text and
-- was resolved straight to a path, with no check that the workspace posting it was the
-- workspace that uploaded it. Twelve hex characters is not an access control.

CREATE TABLE pieces (
    -- The id /api/pieces already mints. text, not uuid: it is a 12-char hex slug and it
    -- is already baked into S3 keys and into every existing jobs.piece_id.
    id           text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id),

    -- Where the original lives. NULL only for rows recovered from job history below,
    -- where the file extension is not knowable from SQL; the API resolves those once
    -- against S3 and writes the answer back.
    s3_key       text,

    category     text NOT NULL,
    type         text NOT NULL DEFAULT '',
    description  text NOT NULL DEFAULT '',
    sku          text,

    created_at   timestamptz NOT NULL DEFAULT now(),
    -- Sorting a library by upload date puts the piece you shot this morning below the
    -- one you uploaded a year ago and never used again.
    last_used_at timestamptz,
    archived_at  timestamptz
);

CREATE INDEX pieces_by_workspace
    ON pieces (workspace_id, COALESCE(last_used_at, created_at) DESC)
    WHERE archived_at IS NULL;

-- Recover what the job table already knows, so the library is not empty for everyone who
-- was using this before it existed. DISTINCT ON keeps the most recent job per piece, and
-- its params carry the category and description the customer confirmed at the time.
INSERT INTO pieces (id, workspace_id, user_id, category, type, description, sku,
                    created_at, last_used_at)
SELECT DISTINCT ON (j.piece_id)
       j.piece_id,
       j.workspace_id,
       j.user_id,
       COALESCE(j.params ->> 'category', 'ring'),
       COALESCE(j.params -> 'options' ->> 'type', ''),
       COALESCE(j.params ->> 'description', ''),
       j.sku,
       j.created_at,
       j.created_at
  FROM jobs j
 WHERE j.piece_id IS NOT NULL
   AND j.kind = 'shoot'
 ORDER BY j.piece_id, j.created_at DESC
    ON CONFLICT (id) DO NOTHING;
