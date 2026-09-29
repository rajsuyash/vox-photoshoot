-- Storyboard-driven video ads: campaign → concepts → storyboard → versions → shots →
-- generated assets → final render. Replaces "one still, one clip" with an agency-style
-- multi-shot ad, while the existing job_videos flow (010) stays untouched alongside it.
--
-- Everything here traces back to a shot or a version, so a refresh recovers by asking
-- jobs "what's running for this version" instead of re-deriving state from S3.

CREATE TABLE campaigns (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name          text NOT NULL,
    brief         jsonb NOT NULL DEFAULT '{}'::jsonb,
    brand_style   text NOT NULL DEFAULT '',
    campaign_style text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'draft',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX campaigns_by_workspace ON campaigns (workspace_id, created_at DESC);

CREATE TABLE campaign_products (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id           uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    piece_id              text NOT NULL REFERENCES pieces(id),
    fidelity_instructions text NOT NULL DEFAULT ''
);

CREATE INDEX campaign_products_by_campaign ON campaign_products (campaign_id);

-- A character is either a workspace's own talent, a built-in cast key (cast.json — text,
-- not a foreign key, since the built-in cast lives in code not a table), or neither yet
-- (the director describes one from scratch and nothing has been cast/generated for it).
-- master_asset_id is filled in later, once a master reference portrait exists for a
-- from-scratch character — added AFTER generated_assets below (deferred FK).
CREATE TABLE campaign_characters (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id  uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         text NOT NULL,
    role         text NOT NULL DEFAULT '',
    appearance   jsonb NOT NULL DEFAULT '{}'::jsonb,
    talent_id    text REFERENCES talent(id),
    cast_key     text,
    master_asset_id uuid,

    -- talent and cast_key both name a concrete identity, so at most one may be set — a
    -- character may also be neither (fully described, no reference chosen yet).
    CONSTRAINT campaign_characters_identity_check
        CHECK (NOT (talent_id IS NOT NULL AND cast_key IS NOT NULL))
);

CREATE INDEX campaign_characters_by_campaign ON campaign_characters (campaign_id);

CREATE TABLE concepts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name             text NOT NULL,
    core_idea        text NOT NULL DEFAULT '',
    emotional_hook   text NOT NULL DEFAULT '',
    visual_world     text NOT NULL DEFAULT '',
    story_arc        text NOT NULL DEFAULT '',
    tagline          text NOT NULL DEFAULT '',
    chosen           boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX concepts_by_campaign ON concepts (campaign_id);
-- At most one chosen concept per campaign.
CREATE UNIQUE INDEX concepts_one_chosen ON concepts (campaign_id) WHERE chosen;

CREATE TABLE storyboards (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id        uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    concept_id         uuid REFERENCES concepts(id),
    title              text NOT NULL DEFAULT '',
    aspect_ratio       text NOT NULL DEFAULT '9:16',
    target_duration    numeric NOT NULL,
    platform           text NOT NULL DEFAULT '',
    visual_style       text NOT NULL DEFAULT '',
    emotional_arc      text NOT NULL DEFAULT '',
    music_direction    text NOT NULL DEFAULT '',
    voice_config       jsonb NOT NULL DEFAULT '{}'::jsonb,
    palette            text NOT NULL DEFAULT '',
    -- Filled in once version 1 exists — added as a deferred FK below so the two tables
    -- can be created in either order without a chicken-and-egg CREATE TABLE.
    active_version_id uuid,
    -- 9:16/1:1 adaptations of an existing storyboard fork the parent rather than starting
    -- from a brief again (not built in Phase 1 — see the plan's "Later").
    parent_storyboard_id uuid REFERENCES storyboards(id),
    status             text NOT NULL DEFAULT 'draft',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT storyboards_status_check CHECK (status IN
        ('draft', 'generating', 'awaiting_approval', 'approved', 'in_production',
         'completed', 'archived'))
);

CREATE INDEX storyboards_by_campaign ON storyboards (campaign_id);

CREATE TABLE storyboard_versions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    storyboard_id          uuid NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    version_number         integer NOT NULL,
    creative_summary       text NOT NULL DEFAULT '',
    status                 text NOT NULL DEFAULT 'draft',
    created_from_version_id uuid REFERENCES storyboard_versions(id),
    approved_at            timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT storyboard_versions_status_check
        CHECK (status IN ('draft', 'approved', 'superseded')),
    CONSTRAINT storyboard_versions_unique UNIQUE (storyboard_id, version_number)
);

CREATE INDEX storyboard_versions_by_storyboard ON storyboard_versions (storyboard_id);

ALTER TABLE storyboards ADD CONSTRAINT storyboards_active_version_fk
    FOREIGN KEY (active_version_id) REFERENCES storyboard_versions(id) DEFERRABLE INITIALLY DEFERRED;

-- Shot timing is derived, never stored: start_time = SUM(duration) OVER (ORDER BY
-- position) computed in storyboard.get_version(). Changing one shot's duration moves
-- every later shot with no second timing system to keep in sync.
CREATE TABLE storyboard_shots (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id              uuid NOT NULL REFERENCES storyboard_versions(id) ON DELETE CASCADE,
    -- Stable across a version fork, so "Shot 04: Image V1-V3, Video V1-V2" survives a new
    -- version — a shot's full generation history is every asset with this shot_key.
    shot_key                uuid NOT NULL,
    position                integer NOT NULL,
    kind                    text NOT NULL DEFAULT 'shot',
    duration                numeric NOT NULL,
    spec                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    character_ids           uuid[] NOT NULL DEFAULT '{}',
    product_ids             uuid[] NOT NULL DEFAULT '{}',
    image_prompt            text NOT NULL DEFAULT '',
    motion_prompt           text NOT NULL DEFAULT '',
    negative_prompt         text NOT NULL DEFAULT '',
    state                   text NOT NULL DEFAULT 'draft',
    -- FKs to generated_assets added below, once that table exists.
    selected_frame_asset_id uuid,
    selected_video_asset_id uuid,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT storyboard_shots_kind_check CHECK (kind IN ('shot', 'end_card')),
    CONSTRAINT storyboard_shots_duration_check CHECK (duration > 0),
    CONSTRAINT storyboard_shots_state_check CHECK (state IN
        ('draft', 'ready_for_frame', 'frame_generating', 'frame_review', 'frame_approved',
         'frame_failed', 'video_generating', 'video_review', 'video_approved',
         'video_failed')),
    -- Positions are contiguous 0..n-1 within a version (enforced in storyboard.py, not
    -- SQL — an in-flight reorder briefly holds duplicate positions). DEFERRABLE lets a
    -- reorder swap positions within one transaction without violating the constraint
    -- mid-statement.
    CONSTRAINT storyboard_shots_position_unique
        UNIQUE (version_id, position) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT storyboard_shots_key_unique UNIQUE (version_id, shot_key)
);

CREATE INDEX storyboard_shots_by_version ON storyboard_shots (version_id, position);

CREATE TABLE generated_assets (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    campaign_id    uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    storyboard_id  uuid NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    version_id     uuid NOT NULL REFERENCES storyboard_versions(id) ON DELETE CASCADE,
    -- Nullable: music and final_render attach at version level. Everything else must
    -- carry a shot — enforced below rather than left to the application, since an asset
    -- with no owner is unreachable from any shot and unrecoverable on a refresh.
    shot_id        uuid REFERENCES storyboard_shots(id) ON DELETE CASCADE,
    shot_key       uuid,
    type           text NOT NULL,
    provider       text NOT NULL DEFAULT '',
    provider_model text NOT NULL DEFAULT '',
    prompt         text NOT NULL DEFAULT '',
    settings       jsonb NOT NULL DEFAULT '{}'::jsonb,
    key            text NOT NULL,        -- S3 key, never a URL — see storage.py
    thumb_key      text,
    job_id         uuid REFERENCES jobs(id),
    variant        integer NOT NULL DEFAULT 1,
    approval_status text NOT NULL DEFAULT 'pending',
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT generated_assets_type_check CHECK (type IN
        ('storyboard_image', 'reference_image', 'video_clip', 'voiceover', 'music',
         'sound_effect', 'logo', 'product_reference', 'character_reference',
         'final_render')),
    CONSTRAINT generated_assets_approval_check
        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    -- Shot-level types must carry both shot_id and shot_key (shot_key is what survives a
    -- version fork — see storyboard_shots.shot_key above); music/final_render may float
    -- at version level with neither.
    CONSTRAINT generated_assets_shot_scope_check CHECK (
        (type IN ('storyboard_image', 'reference_image', 'video_clip', 'voiceover',
                  'sound_effect') AND shot_id IS NOT NULL AND shot_key IS NOT NULL)
        OR (type IN ('music', 'final_render', 'logo', 'product_reference',
                      'character_reference'))
    ),
    CONSTRAINT generated_assets_variant_unique UNIQUE (shot_key, type, variant)
);

CREATE INDEX generated_assets_by_shot_key ON generated_assets (shot_key, type);
CREATE INDEX generated_assets_by_version ON generated_assets (version_id);

ALTER TABLE storyboard_shots ADD CONSTRAINT storyboard_shots_frame_fk
    FOREIGN KEY (selected_frame_asset_id) REFERENCES generated_assets(id) ON DELETE SET NULL;
ALTER TABLE storyboard_shots ADD CONSTRAINT storyboard_shots_video_fk
    FOREIGN KEY (selected_video_asset_id) REFERENCES generated_assets(id) ON DELETE SET NULL;

ALTER TABLE campaign_characters ADD CONSTRAINT campaign_characters_master_asset_fk
    FOREIGN KEY (master_asset_id) REFERENCES generated_assets(id) ON DELETE SET NULL;

-- Append-only history of every approve/reject/reset decision. Never updated or deleted —
-- it is the audit trail a customer dispute or a "who approved this" question needs.
CREATE TABLE approvals (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    version_id   uuid NOT NULL REFERENCES storyboard_versions(id) ON DELETE CASCADE,
    shot_id      uuid REFERENCES storyboard_shots(id) ON DELETE CASCADE,
    stage        text NOT NULL,
    decision     text NOT NULL,
    asset_id     uuid REFERENCES generated_assets(id),
    user_id      uuid REFERENCES users(id),
    note         text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT approvals_decision_check CHECK (decision IN ('approved', 'rejected', 'reset'))
);

CREATE INDEX approvals_by_version ON approvals (version_id, created_at DESC);

CREATE TABLE final_renders (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id   uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    storyboard_id uuid NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    version_id    uuid NOT NULL REFERENCES storyboard_versions(id) ON DELETE CASCADE,
    aspect_ratio  text NOT NULL,
    resolution    text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'queued',
    asset_id      uuid REFERENCES generated_assets(id),
    job_id        uuid REFERENCES jobs(id),
    -- Every asset id used to build this render, so any past ad can be rebuilt exactly.
    manifest      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX final_renders_by_version ON final_renders (version_id);

-- jobs gets pointers to what it's working on, same shape as job_videos' source pointers,
-- plus the six new ad_* kinds. Fencing, sweep, idempotency and refunds all come free.
ALTER TABLE jobs ADD COLUMN storyboard_version_id uuid REFERENCES storyboard_versions(id);
ALTER TABLE jobs ADD COLUMN storyboard_shot_id uuid REFERENCES storyboard_shots(id);

CREATE INDEX jobs_by_storyboard_version ON jobs (storyboard_version_id)
    WHERE status IN ('queued', 'running');

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('shoot', 'reshoot', 'retouch', 'model', 'video',
                     'ad_concepts', 'ad_board', 'ad_frame', 'ad_video', 'ad_music',
                     'ad_render'));
