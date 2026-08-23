-- A workspace's own models, alongside the thirty built into the app.
--
-- Called talent rather than models because this codebase already uses "model" for the
-- image model behind a provider, and cast.py already owns the built-in cast. Talent is
-- what the industry calls the people in front of the camera, and it leaves both other
-- meanings alone.
--
-- Two ways one gets here, and the column records which:
--
--   generated  the customer described an age, a complexion, a region, and the app made
--              a portrait through the same brief the built-in cast was made with.
--
--   uploaded   the customer supplied a photograph of a real person and the app
--              normalised it through that same brief — jewellery off, plain top, bare
--              ears and neck. The raw upload is never the reference: a model wearing
--              her own earrings puts them into a shot of the customer's ring.
--
-- Private to the workspace. There is no sharing flag on purpose: an uploaded face is a
-- real person who consented to one brand using their likeness, not to a marketplace.

CREATE TABLE talent (
    -- Same 12-char hex slug as pieces, minted by the app and used in the S3 key.
    id           text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id),

    -- What the customer calls her. Shown on the card, never sent to the provider.
    name         text NOT NULL,
    -- What the shoot conditions on, in the same shape as cast.json's description:
    -- "a 26 year old Kashmiri model, very fair skin with ...". locations.compose drops
    -- it straight into the prompt, so it carries its own opening clause.
    description  text NOT NULL,

    -- The normalised portrait. Always present: an uploaded face is stored only after
    -- the normalising pass has produced something usable as a reference.
    s3_key       text NOT NULL,
    source       text NOT NULL,

    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    archived_at  timestamptz,

    CONSTRAINT talent_source_check CHECK (source IN ('generated', 'uploaded'))
);

CREATE INDEX talent_by_workspace
    ON talent (workspace_id, COALESCE(last_used_at, created_at) DESC)
    WHERE archived_at IS NULL;

-- Making a model is a job like any other: it reserves a credit, generates one image and
-- settles. jobs.kind has enumerated its three kinds since 002 and would have rejected a
-- fourth — which it did, on the first call, exactly as a check constraint should.
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('shoot', 'reshoot', 'retouch', 'model'));
