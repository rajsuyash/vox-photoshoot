-- Turning an already-delivered shoot still into a short video ad.
--
-- A video is a job like any other kind (jobs.kind gets a fourth-then-fifth member here,
-- 'video'), so it gets fencing, heartbeats, idempotency and the orphan sweep for free.
-- What it needs beyond that is its own result table: a shoot's job_images row is one
-- image at one credit; a video's price varies with duration and provider, and it is
-- not "one row per credit" the way an image is (see jobs.sweep()'s CASE for kind =
-- 'video', which prices a video's delivery off this table's existence, not a row count).

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('shoot', 'reshoot', 'retouch', 'model', 'video'));

CREATE TABLE job_videos (
    job_id         uuid PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    -- The shoot this was animated from, and which delivered image in it — so a video
    -- can be found from the gallery it belongs to, and reproduced if ever disputed.
    source_job_id    uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_framing   text NOT NULL,
    source_attempt   integer NOT NULL,

    -- The still actually fed to the video model: the original when its aspect already
    -- matched, or the reframed one (also kept as its own job_images row on source_job_id,
    -- framing 'reframe-<aspect>') when it did not.
    still_key      text NOT NULL,
    key            text NOT NULL,        -- the rendered mp4, S3 key not a URL — see storage.py

    duration       numeric,
    width          integer,
    height         integer,
    aspect         text NOT NULL,
    provider       text,

    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX job_videos_by_source ON job_videos (source_job_id);
