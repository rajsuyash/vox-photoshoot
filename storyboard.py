"""Campaigns, storyboards, versions, shots and their generated assets.

The domain layer for the storyboard-driven ad flow: persistence, structural ops
(add/duplicate/delete/reorder/split a shot), versioning (fork-on-edit-when-approved) and
the shot state machine's bookkeeping. No provider imports — orchestrator.py calls the
providers and then calls in here to record what happened.

Every read and write is scoped to a workspace_id, because a campaign/storyboard/version/
shot id alone must not be enough to reach another workspace's paid work — the same rule
jobs.get() already enforces for jobs. Only campaigns and generated_assets carry
workspace_id directly; everything between them is scoped by joining up to campaigns.

Two ideas hold the versioning model together:

  shot_key     a stable uuid that survives a version fork. A shot's full generation
               history = the generated_assets rows sharing its shot_key, which is why
               asset lookups key on shot_key and not on a shot's row id.
  ensure_editable   every STRUCTURAL or CREATIVE-PLAN op (add/duplicate/delete/reorder/
               split, update_spec) calls this first. A draft version is edited in place;
               an approved one is frozen, so editing it forks a new draft version (same
               shot_keys, same selections, same states) and moves the storyboard's
               active_version_id to the fork.

The version freeze protects the CREATIVE PLAN only (spec fields, prompts, durations,
structure, order) — not PRODUCTION STATE (shot.state, selected_frame/video_asset_id,
approvals rows, generated assets). Production state is mutable in place on both draft and
approved versions: apply_event() and select_asset() never fork, so a background job
finishing frame_done/video_done against an approved version updates it directly instead
of spawning a new version underneath the customer's approved plan. A SUPERSEDED version's
shots are read-only for production events (apply_event/select_asset raise ValueError) —
nothing points at a superseded version as "the" version to generate against any more.
complete_generation() is how a job started against one version routes its result to the
matching shot in whatever version is active by the time it finishes.

    .venv/bin/python storyboard.py     # self-check against DATABASE_URL
"""

import json
import os
import uuid

import db
import jobs
import shot_state


class NotFound(Exception):
    """A campaign/storyboard/version/shot/asset does not exist in this workspace.

    Covers cross-workspace access too: a row that exists but belongs to a different
    workspace looks exactly like a row that does not exist, which is the point.
    """


class Conflict(Exception):
    """A product/character cannot be removed — some shot, in some version, still
    references it by id."""


# Which generated_assets.type feeds which "selected" column on a shot. Only these two
# types are ever selectable — everything else (music, final_render, references) has no
# single-choice slot on a shot.
FRAME_ASSET_TYPES = {'storyboard_image'}
VIDEO_ASSET_TYPES = {'video_clip'}

# Shot fields that are real columns rather than entries inside the `spec` jsonb blob.
# update_spec() routes a patch key through here to decide which side of that split to
# write to; shot_state.FIELD_GROUPS is the authority on what each field INVALIDATES,
# this constant is only about WHERE it is stored.
COLUMN_FIELDS = {'image_prompt', 'motion_prompt', 'negative_prompt', 'duration',
                 'character_ids', 'product_ids'}

# Which apply_event() events record an approvals row, and under what stage name.
APPROVE_STAGE = {'approve_frame': 'frame', 'approve_video': 'video'}

# A shot demoted OUT of one of these states lost that approval — _record_reset logs it.
# frame_approved's only FORWARD event is start_video, and video_approved has none, so a
# transition landing anywhere else from one of these two states is always a rollback,
# never ordinary progress (see _record_reset's callers for how each guards against
# firing on the forward path too).
_RESET_STAGE_FOR_STATE = {'frame_approved': 'frame', 'video_approved': 'video'}

# event -> the *_generating state a shot must be sitting in for that event to apply.
# complete_generation() uses this to decide whether a finishing job still matches what
# the active version's shot is waiting on.
_GENERATING_STATE_FOR_EVENT = {
    'frame_done': 'frame_generating', 'frame_failed': 'frame_generating',
    'video_done': 'video_generating', 'video_failed': 'video_generating',
}


def _row_or_404(row, what: str):
    if row is None:
        raise NotFound(what)
    return row


def _campaign_scope(workspace_id: str, campaign_id: str) -> dict:
    return _row_or_404(db.query(
        'SELECT id, workspace_id FROM campaigns WHERE id = %s AND workspace_id = %s',
        (campaign_id, workspace_id), one=True), 'campaign not found')


def _version_scope(workspace_id: str, version_id: str) -> dict:
    """version + its storyboard + its campaign, scoped to the workspace."""
    return _row_or_404(db.query(
        """SELECT sv.id AS version_id, sv.storyboard_id, sv.status AS version_status,
                  sv.version_number, s.campaign_id, s.active_version_id, c.workspace_id
             FROM storyboard_versions sv
             JOIN storyboards s ON s.id = sv.storyboard_id
             JOIN campaigns c ON c.id = s.campaign_id
            WHERE sv.id = %s AND c.workspace_id = %s""",
        (version_id, workspace_id), one=True), 'version not found')


def _shot_scope(workspace_id: str, shot_id: str) -> dict:
    """A shot plus enough of its version/storyboard/campaign to scope and fork it."""
    return _row_or_404(db.query(
        """SELECT sh.*, sv.storyboard_id, sv.status AS version_status, c.workspace_id
             FROM storyboard_shots sh
             JOIN storyboard_versions sv ON sv.id = sh.version_id
             JOIN storyboards s ON s.id = sv.storyboard_id
             JOIN campaigns c ON c.id = s.campaign_id
            WHERE sh.id = %s AND c.workspace_id = %s""",
        (shot_id, workspace_id), one=True), 'shot not found')


# --- Campaigns / products / characters / concepts -----------------------------------

def create_campaign(workspace_id: str, name: str, brief: dict | None = None,
                     brand_style: str = '', campaign_style: str = '') -> dict:
    return db.query(
        """INSERT INTO campaigns (workspace_id, name, brief, brand_style, campaign_style)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id, workspace_id, name, brief, brand_style, campaign_style, status,
                     created_at""",
        (workspace_id, name, json.dumps(brief or {}), brand_style, campaign_style),
        one=True)


def get_campaign(workspace_id: str, campaign_id: str) -> dict | None:
    """None for both "doesn't exist" and "belongs to another workspace" — the caller
    cannot tell the difference, which is the access control."""
    return db.query('SELECT * FROM campaigns WHERE id = %s AND workspace_id = %s',
                    (campaign_id, workspace_id), one=True)


def list_campaigns(workspace_id: str) -> list[dict]:
    return db.query('SELECT * FROM campaigns WHERE workspace_id = %s '
                    'ORDER BY created_at DESC', (workspace_id,))


def add_product(workspace_id: str, campaign_id: str, piece_id: str,
                 fidelity_instructions: str = '') -> dict:
    """Adding the same piece twice is a no-op that returns the existing row — a double
    click must not put two chips of the same product in the campaign."""
    _campaign_scope(workspace_id, campaign_id)
    existing = db.query(
        """SELECT id, campaign_id, piece_id, fidelity_instructions FROM campaign_products
            WHERE campaign_id = %s AND piece_id = %s""", (campaign_id, piece_id), one=True)
    if existing:
        return existing
    return db.query(
        """INSERT INTO campaign_products (campaign_id, piece_id, fidelity_instructions)
           VALUES (%s, %s, %s)
           RETURNING id, campaign_id, piece_id, fidelity_instructions""",
        (campaign_id, piece_id, fidelity_instructions), one=True)


def add_character(workspace_id: str, campaign_id: str, name: str, role: str = '',
                   appearance: dict | None = None, talent_id: str | None = None,
                   cast_key: str | None = None) -> dict:
    """Adding the same talent/cast identity twice is a no-op that returns the existing
    row (see add_product) — a from-scratch character (neither talent_id nor cast_key)
    has no identity to dedupe on, so two of those are two real characters."""
    _campaign_scope(workspace_id, campaign_id)
    if talent_id or cast_key:
        existing = db.query(
            """SELECT id, campaign_id, name, role, appearance, talent_id, cast_key
                 FROM campaign_characters
                WHERE campaign_id = %s AND (talent_id = %s OR cast_key = %s)""",
            (campaign_id, talent_id, cast_key), one=True)
        if existing:
            return existing
    return db.query(
        """INSERT INTO campaign_characters (campaign_id, name, role, appearance,
                talent_id, cast_key)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id, campaign_id, name, role, appearance, talent_id, cast_key""",
        (campaign_id, name, role, json.dumps(appearance or {}), talent_id, cast_key),
        one=True)


def _product_scope(workspace_id: str, campaign_id: str, product_id: str) -> dict:
    return _row_or_404(db.query(
        """SELECT cp.id FROM campaign_products cp JOIN campaigns c ON c.id = cp.campaign_id
            WHERE cp.id = %s AND cp.campaign_id = %s AND c.workspace_id = %s""",
        (product_id, campaign_id, workspace_id), one=True), 'product not found')


def remove_product(workspace_id: str, campaign_id: str, product_id: str) -> None:
    """Refuses if any shot, in any version of this campaign, still names this product —
    deleting the row out from under a shot that references it would leave a dangling id
    no UI could resolve back to a product."""
    _product_scope(workspace_id, campaign_id, product_id)
    referenced = db.query(
        """SELECT 1 FROM storyboard_shots sh
             JOIN storyboard_versions sv ON sv.id = sh.version_id
             JOIN storyboards sb ON sb.id = sv.storyboard_id
            WHERE sb.campaign_id = %s AND %s = ANY(sh.product_ids) LIMIT 1""",
        (campaign_id, product_id), one=True)
    if referenced:
        raise Conflict('a shot still uses this product')
    db.query('DELETE FROM campaign_products WHERE id = %s', (product_id,))


def _character_scope(workspace_id: str, campaign_id: str, character_id: str) -> dict:
    return _row_or_404(db.query(
        """SELECT cc.id FROM campaign_characters cc
             JOIN campaigns c ON c.id = cc.campaign_id
            WHERE cc.id = %s AND cc.campaign_id = %s AND c.workspace_id = %s""",
        (character_id, campaign_id, workspace_id), one=True), 'character not found')


def remove_character(workspace_id: str, campaign_id: str, character_id: str) -> None:
    """See remove_product — same rule, same reason."""
    _character_scope(workspace_id, campaign_id, character_id)
    referenced = db.query(
        """SELECT 1 FROM storyboard_shots sh
             JOIN storyboard_versions sv ON sv.id = sh.version_id
             JOIN storyboards sb ON sb.id = sv.storyboard_id
            WHERE sb.campaign_id = %s AND %s = ANY(sh.character_ids) LIMIT 1""",
        (campaign_id, character_id), one=True)
    if referenced:
        raise Conflict('a shot still uses this character')
    db.query('DELETE FROM campaign_characters WHERE id = %s', (character_id,))


def save_concepts(workspace_id: str, campaign_id: str, concepts: list[dict]) -> list[str]:
    """Persist the director's 3 concepts. Returns their new ids, in the given order.

    mode ('story'|'showcase', migrations/012_concept_mode.sql) is a real column now — it
    used to live in campaigns.brief->'concept_modes', a JSONB side-map ads_api.py kept in
    sync by hand because this migration wasn't in scope yet.
    """
    _campaign_scope(workspace_id, campaign_id)
    ids = []
    with db.tx() as conn:
        for c in concepts:
            row = conn.execute(
                """INSERT INTO concepts (campaign_id, name, core_idea, emotional_hook,
                        visual_world, story_arc, tagline, mode)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (campaign_id, c.get('name', ''), c.get('core_idea', ''),
                 c.get('emotional_hook', ''), c.get('visual_world', ''),
                 c.get('story_arc', ''), c.get('tagline', ''),
                 c.get('mode', 'story'))).fetchone()
            ids.append(str(row[0]))
    return ids


def choose_concept(workspace_id: str, campaign_id: str, concept_id: str) -> str:
    _campaign_scope(workspace_id, campaign_id)
    with db.tx() as conn:
        conn.execute('UPDATE concepts SET chosen = false WHERE campaign_id = %s',
                    (campaign_id,))
        row = conn.execute(
            'UPDATE concepts SET chosen = true WHERE id = %s AND campaign_id = %s '
            'RETURNING id', (concept_id, campaign_id)).fetchone()
    if row is None:
        raise NotFound('concept not found')
    return concept_id


# --- Storyboard + version 1 ----------------------------------------------------------

def create_storyboard(workspace_id: str, campaign_id: str, fields: dict,
                       shots: list[dict]) -> dict:
    """One transaction: storyboard row, version 1 (draft), every shot with a fresh
    shot_key, active_version_id set. Nothing here is visible half-built.

    `fields['warnings']`, if given, is director.py's SOFT-error list from
    generate_storyboard (a board with no hard errors, possibly some style warnings still
    open) — stored on the version so the editor can show them on reload, not just at the
    moment the job finishes.
    """
    _campaign_scope(workspace_id, campaign_id)
    with db.tx() as conn:
        sb = conn.execute(
            """INSERT INTO storyboards (campaign_id, concept_id, title, aspect_ratio,
                    target_duration, platform, visual_style, emotional_arc,
                    music_direction, voice_config, palette)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (campaign_id, fields.get('concept_id'), fields.get('title', ''),
             fields.get('aspect_ratio', '9:16'), fields['target_duration'],
             fields.get('platform', ''), fields.get('visual_style', ''),
             fields.get('emotional_arc', ''), fields.get('music_direction', ''),
             json.dumps(fields.get('voice_config', {})),
             fields.get('palette', ''))).fetchone()
        storyboard_id = sb[0]

        version = conn.execute(
            """INSERT INTO storyboard_versions (storyboard_id, version_number, status,
                    warnings)
               VALUES (%s, 1, 'draft', %s) RETURNING id""",
            (storyboard_id, json.dumps(fields.get('warnings', [])))).fetchone()
        version_id = version[0]

        for position, shot in enumerate(shots):
            conn.execute(
                """INSERT INTO storyboard_shots (version_id, shot_key, position, kind,
                        duration, spec, character_ids, product_ids, image_prompt,
                        motion_prompt, negative_prompt, state)
                   VALUES (%s, gen_random_uuid(), %s, %s, %s, %s, %s::uuid[], %s::uuid[],
                           %s, %s, %s, 'draft')""",
                (version_id, position, shot.get('kind', 'shot'), shot['duration'],
                 json.dumps(shot.get('spec', {})), shot.get('character_ids', []),
                 shot.get('product_ids', []), shot.get('image_prompt', ''),
                 shot.get('motion_prompt', ''), shot.get('negative_prompt', '')))

        conn.execute('UPDATE storyboards SET active_version_id = %s WHERE id = %s',
                    (version_id, storyboard_id))
    return {'storyboard_id': str(storyboard_id), 'version_id': str(version_id)}


def get_version(workspace_id: str, version_id: str) -> dict:
    """Full state for the editor: storyboard fields, version fields, and shots in
    position order each carrying a derived start_time (never stored — see module doc)."""
    scope = _version_scope(workspace_id, version_id)
    storyboard = db.query('SELECT * FROM storyboards WHERE id = %s',
                          (scope['storyboard_id'],), one=True)
    version = db.query('SELECT * FROM storyboard_versions WHERE id = %s',
                       (version_id,), one=True)
    shots = db.query(
        """SELECT *, COALESCE(SUM(duration) OVER (
                ORDER BY position ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ), 0) AS start_time
             FROM storyboard_shots WHERE version_id = %s ORDER BY position""",
        (version_id,))
    total_duration = sum(float(s['duration']) for s in shots)
    return {'storyboard': storyboard, 'version': version, 'shots': shots,
            'total_duration': total_duration}


# --- Versioning ------------------------------------------------------------------

def _fork_version(workspace_id: str, version_id: str) -> str:
    scope = _version_scope(workspace_id, version_id)
    with db.tx() as conn:
        next_number = conn.execute(
            'SELECT COALESCE(MAX(version_number), 0) + 1 FROM storyboard_versions '
            'WHERE storyboard_id = %s', (scope['storyboard_id'],)).fetchone()[0]
        new_version = conn.execute(
            """INSERT INTO storyboard_versions (storyboard_id, version_number,
                    creative_summary, status, created_from_version_id)
               SELECT storyboard_id, %s, creative_summary, 'draft', id
                 FROM storyboard_versions WHERE id = %s
               RETURNING id""", (next_number, version_id)).fetchone()
        new_version_id = new_version[0]
        # Same shot_key, same selections, same state — a fork changes WHICH version is
        # editable, never what has already been generated or approved.
        conn.execute(
            """INSERT INTO storyboard_shots (version_id, shot_key, position, kind,
                    duration, spec, character_ids, product_ids, image_prompt,
                    motion_prompt, negative_prompt, state, selected_frame_asset_id,
                    selected_video_asset_id)
               SELECT %s, shot_key, position, kind, duration, spec, character_ids,
                      product_ids, image_prompt, motion_prompt, negative_prompt, state,
                      selected_frame_asset_id, selected_video_asset_id
                 FROM storyboard_shots WHERE version_id = %s""",
            (new_version_id, version_id))
        conn.execute('UPDATE storyboards SET active_version_id = %s, updated_at = now() '
                    'WHERE id = %s', (new_version_id, scope['storyboard_id']))
    return str(new_version_id)


def ensure_editable(workspace_id: str, version_id: str) -> str:
    """The version id to actually write to: version_id itself if it's a draft, or a
    freshly forked draft otherwise. Every op below calls this first.

    Only 'draft' is edited in place. Both 'approved' and 'superseded' fork: a superseded
    version is an old direction the customer has since moved on from (or forked again
    themselves), and editing it must not resurrect it in place — it forks its own draft,
    same as editing the currently-approved version does, and active_version_id moves to
    that fork regardless of which status the edit started from.
    """
    scope = _version_scope(workspace_id, version_id)
    if scope['version_status'] == 'draft':
        return str(version_id)
    return _fork_version(workspace_id, version_id)


def _resolve_shot(workspace_id: str, shot_id: str) -> tuple[str, str]:
    """(effective_version_id, effective_shot_id) for a shot-level op. Forks the version
    if needed and maps shot_id to its same-shot_key counterpart in the fork — a fork
    gives every shot a new row id, so the caller's original shot_id no longer exists."""
    shot = _shot_scope(workspace_id, shot_id)
    effective_version_id = ensure_editable(workspace_id, shot['version_id'])
    if str(effective_version_id) == str(shot['version_id']):
        return effective_version_id, str(shot_id)
    new_shot = db.query(
        'SELECT id FROM storyboard_shots WHERE version_id = %s AND shot_key = %s',
        (effective_version_id, shot['shot_key']), one=True)
    return effective_version_id, str(new_shot['id'])


def approve_version(workspace_id: str, version_id: str) -> str:
    """Freeze this version. Any prior approved version of the same storyboard becomes
    superseded — a storyboard has at most one approved version at a time."""
    scope = _version_scope(workspace_id, version_id)
    with db.tx() as conn:
        conn.execute(
            """UPDATE storyboard_versions SET status = 'superseded'
                WHERE storyboard_id = %s AND status = 'approved' AND id <> %s""",
            (scope['storyboard_id'], version_id))
        conn.execute(
            "UPDATE storyboard_versions SET status = 'approved', approved_at = now() "
            'WHERE id = %s', (version_id,))
        conn.execute(
            'UPDATE storyboards SET active_version_id = %s, updated_at = now() '
            'WHERE id = %s', (version_id, scope['storyboard_id']))
    return str(version_id)


# --- Shot structural ops -------------------------------------------------------------

def _shift_positions(conn, version_id: str, from_position: int, delta: int) -> None:
    conn.execute(
        'UPDATE storyboard_shots SET position = position + %s '
        'WHERE version_id = %s AND position >= %s', (delta, version_id, from_position))


def add_shot(workspace_id: str, version_id: str, after_position: int,
             shot_fields: dict) -> tuple[str, str]:
    effective_version_id = ensure_editable(workspace_id, version_id)
    new_position = after_position + 1
    with db.tx() as conn:
        _shift_positions(conn, effective_version_id, new_position, 1)
        row = conn.execute(
            """INSERT INTO storyboard_shots (version_id, shot_key, position, kind,
                    duration, spec, character_ids, product_ids, image_prompt,
                    motion_prompt, negative_prompt, state)
               VALUES (%s, gen_random_uuid(), %s, %s, %s, %s, %s::uuid[], %s::uuid[],
                       %s, %s, %s, 'draft') RETURNING id""",
            (effective_version_id, new_position, shot_fields.get('kind', 'shot'),
             shot_fields['duration'], json.dumps(shot_fields.get('spec', {})),
             shot_fields.get('character_ids', []), shot_fields.get('product_ids', []),
             shot_fields.get('image_prompt', ''), shot_fields.get('motion_prompt', ''),
             shot_fields.get('negative_prompt', ''))).fetchone()
    return effective_version_id, str(row[0])


def duplicate_shot(workspace_id: str, shot_id: str) -> tuple[str, str]:
    """A copy right after the original. Gets its OWN shot_key: a duplicate is a new
    shot lineage that happens to start with the same spec, not the same shot in two
    places — so it starts at 'draft' with no generation history of its own."""
    effective_version_id, effective_shot_id = _resolve_shot(workspace_id, shot_id)
    shot = db.query('SELECT * FROM storyboard_shots WHERE id = %s',
                    (effective_shot_id,), one=True)
    with db.tx() as conn:
        _shift_positions(conn, effective_version_id, shot['position'] + 1, 1)
        row = conn.execute(
            """INSERT INTO storyboard_shots (version_id, shot_key, position, kind,
                    duration, spec, character_ids, product_ids, image_prompt,
                    motion_prompt, negative_prompt, state)
               VALUES (%s, gen_random_uuid(), %s, %s, %s, %s, %s::uuid[], %s::uuid[],
                       %s, %s, %s, 'draft') RETURNING id""",
            (effective_version_id, shot['position'] + 1, shot['kind'], shot['duration'],
             json.dumps(shot['spec']), shot['character_ids'], shot['product_ids'],
             shot['image_prompt'], shot['motion_prompt'],
             shot['negative_prompt'])).fetchone()
    return effective_version_id, str(row[0])


def delete_shot(workspace_id: str, shot_id: str) -> str:
    effective_version_id, effective_shot_id = _resolve_shot(workspace_id, shot_id)
    shot = db.query('SELECT position FROM storyboard_shots WHERE id = %s',
                    (effective_shot_id,), one=True)
    with db.tx() as conn:
        conn.execute('DELETE FROM storyboard_shots WHERE id = %s', (effective_shot_id,))
        _shift_positions(conn, effective_version_id, shot['position'] + 1, -1)
    return effective_version_id


def split_shot(workspace_id: str, shot_id: str) -> tuple[str, str, str]:
    """Two shots, each half the duration, spec cloned. The second gets a new shot_key —
    it is new footage, not a continuation of the first's generation history."""
    effective_version_id, effective_shot_id = _resolve_shot(workspace_id, shot_id)
    shot = db.query('SELECT * FROM storyboard_shots WHERE id = %s',
                    (effective_shot_id,), one=True)
    half = shot['duration'] / 2
    with db.tx() as conn:
        conn.execute('UPDATE storyboard_shots SET duration = %s, updated_at = now() '
                    'WHERE id = %s', (half, effective_shot_id))
        _shift_positions(conn, effective_version_id, shot['position'] + 1, 1)
        row = conn.execute(
            """INSERT INTO storyboard_shots (version_id, shot_key, position, kind,
                    duration, spec, character_ids, product_ids, image_prompt,
                    motion_prompt, negative_prompt, state)
               VALUES (%s, gen_random_uuid(), %s, %s, %s, %s, %s::uuid[], %s::uuid[],
                       %s, %s, %s, 'draft') RETURNING id""",
            (effective_version_id, shot['position'] + 1, shot['kind'], half,
             json.dumps(shot['spec']), shot['character_ids'], shot['product_ids'],
             shot['image_prompt'], shot['motion_prompt'],
             shot['negative_prompt'])).fetchone()
    return effective_version_id, effective_shot_id, str(row[0])


def reorder(workspace_id: str, version_id: str, shot_ids: list[str]) -> str:
    """shot_ids in their new order. Must name exactly the shots already in the version —
    partial reorders would leave the rest at undefined positions."""
    effective_version_id = ensure_editable(workspace_id, version_id)
    if str(effective_version_id) != str(version_id):
        # A fork gave every shot a new id. Map the caller's (now-stale) ids across by
        # shot_key, which is the one thing a fork always carries over unchanged.
        old_shots = db.query('SELECT id, shot_key FROM storyboard_shots '
                             'WHERE version_id = %s', (version_id,))
        key_by_old_id = {str(r['id']): r['shot_key'] for r in old_shots}
        new_shots = db.query('SELECT id, shot_key FROM storyboard_shots '
                             'WHERE version_id = %s', (effective_version_id,))
        new_id_by_key = {str(r['shot_key']): str(r['id']) for r in new_shots}
        shot_ids = [new_id_by_key[str(key_by_old_id[str(sid)])] for sid in shot_ids]

    existing = {str(r['id']) for r in db.query(
        'SELECT id FROM storyboard_shots WHERE version_id = %s', (effective_version_id,))}
    if existing != {str(s) for s in shot_ids}:
        raise ValueError('reorder must name exactly the shots already in this version')

    with db.tx() as conn:
        for position, shot_id in enumerate(shot_ids):
            conn.execute('UPDATE storyboard_shots SET position = %s, updated_at = now() '
                        'WHERE id = %s', (position, shot_id))
    return effective_version_id


def set_duration(workspace_id: str, shot_id: str, duration) -> tuple[str, str, str]:
    return update_spec(workspace_id, shot_id, {'duration': duration})


def update_spec(workspace_id: str, shot_id: str, patch: dict) -> tuple[str, str, str]:
    """Apply `patch` to a shot's fields, then apply shot_state.after_edit so the shot's
    state reflects what still matches what was actually generated. This is a
    CREATIVE-PLAN op, so it still forks an approved version like any other spec edit
    (via _resolve_shot) — unlike apply_event/select_asset, which never fork.

    after_edit only ever holds state or demotes it, so any state change here is by
    definition a rollback: if it drops the shot out of frame_approved or video_approved,
    that lost approval is logged as an approvals row (decision='reset'), on whichever
    shot row the edit actually landed on (the fork's, if this edit forked).
    """
    effective_version_id, effective_shot_id = _resolve_shot(workspace_id, shot_id)
    shot = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                    (effective_shot_id,), one=True)
    old_state = shot['state']
    new_state = shot_state.after_edit(old_state, list(patch.keys()))

    set_clauses = ['state = %s', 'updated_at = now()']
    args: list = [new_state]
    spec_patch = {}
    for field, value in patch.items():
        if field in ('character_ids', 'product_ids'):
            set_clauses.append(f'{field} = %s::uuid[]')
            args.append(value)
        elif field in COLUMN_FIELDS:
            set_clauses.append(f'{field} = %s')
            args.append(value)
        else:
            spec_patch[field] = value
    if spec_patch:
        set_clauses.append('spec = spec || %s::jsonb')
        args.append(json.dumps(spec_patch))
    args.append(effective_shot_id)

    with db.tx() as conn:
        conn.execute(f"UPDATE storyboard_shots SET {', '.join(set_clauses)} WHERE id = %s",
                    tuple(args))
        _record_reset(conn, workspace_id, effective_version_id, effective_shot_id,
                      old_state, new_state)
    return effective_version_id, effective_shot_id, new_state


# --- Shot state events -----------------------------------------------------------

def _reject_superseded(shot: dict) -> None:
    """Guard for production events (apply_event, select_asset): a superseded version's
    shots are read-only — nothing points at that version as the one to generate against
    any more, so a job or an approval landing on it is always stale."""
    if shot['version_status'] == 'superseded':
        raise ValueError('cannot apply a production event to a superseded version')


def _record_reset(conn, workspace_id: str, version_id: str, shot_id: str, old_state: str,
                   new_state: str) -> None:
    """Log a lost frame/video approval as an approvals row (decision='reset'). Callers
    must only invoke this on a transition that is actually a rollback — see each call
    site for how it excludes the one forward path (frame_approved -> video_generating)
    that also leaves one of the watched states without invalidating it."""
    if new_state == old_state:
        return
    stage = _RESET_STAGE_FOR_STATE.get(old_state)
    if stage is None:
        return
    conn.execute(
        """INSERT INTO approvals (workspace_id, version_id, shot_id, stage, decision)
           VALUES (%s, %s, %s, %s, 'reset')""",
        (workspace_id, version_id, shot_id, stage))


def apply_event(workspace_id: str, shot_id: str, event: str, asset_id: str | None = None,
                 user_id: str | None = None) -> tuple[str, str, str]:
    """Drive a shot through shot_state.transition IN PLACE and record its approvals
    history. This never forks, even on an approved version — production state (state,
    selections, approvals, assets) is mutable on both draft and approved versions; only
    the creative plan is frozen. It raises ValueError if the shot's version is
    superseded (see _reject_superseded).

    Regenerating a frame from a video_* state (start_frame after a clip already exists)
    keeps the shot's existing selected_frame/video_asset_id — generated_assets are
    history and are never cleared — only `state` drops. The render gate is
    `state == 'video_approved'`, not whether a selection happens to be set.

    Two events can roll a shot backward, and each only ever loses the ONE approval it
    actually invalidates:
      start_frame   regenerates the frame itself, which invalidates any clip built from
                    the old one — logged whenever it fires (it is never the forward path;
                    forward progress out of frame_approved goes through start_video).
      start_video   regenerates a clip from the SAME already-approved frame — logged only
                    when the shot was already at video_review/video_approved (a real
                    rollback of a video approval), never when it fires as ordinary forward
                    progress out of frame_approved (which does not touch the frame's own
                    approval at all, so nothing there was lost).
    """
    shot = _shot_scope(workspace_id, shot_id)
    _reject_superseded(shot)
    old_state = shot['state']
    new_state = shot_state.transition(old_state, event, kind=shot['kind'])
    version_id = str(shot['version_id'])

    with db.tx() as conn:
        conn.execute('UPDATE storyboard_shots SET state = %s, updated_at = now() '
                    'WHERE id = %s', (new_state, shot_id))
        if event in APPROVE_STAGE:
            conn.execute(
                """INSERT INTO approvals (workspace_id, version_id, shot_id, stage,
                        decision, asset_id, user_id)
                   VALUES (%s, %s, %s, %s, 'approved', %s, %s)""",
                (workspace_id, version_id, shot_id, APPROVE_STAGE[event], asset_id,
                 user_id))
        if event == 'start_frame' or (event == 'start_video'
                                       and old_state in ('video_review', 'video_approved')):
            _record_reset(conn, workspace_id, version_id, shot_id, old_state, new_state)
    return version_id, str(shot_id), new_state


# --- Generated assets --------------------------------------------------------------

def add_asset(workspace_id: str, campaign_id: str, storyboard_id: str, version_id: str,
              type_: str, key: str, shot_id: str | None = None,
              shot_key: str | None = None, provider: str = '', provider_model: str = '',
              prompt: str = '', settings: dict | None = None, thumb_key: str | None = None,
              job_id: str | None = None, metadata: dict | None = None) -> dict:
    """variant = max(existing variant for this shot_key+type) + 1, computed inside the
    transaction so two concurrent generations for one shot never collide on a number."""
    with db.tx() as conn:
        next_variant = conn.execute(
            'SELECT COALESCE(MAX(variant), 0) + 1 FROM generated_assets '
            'WHERE shot_key = %s AND type = %s', (shot_key, type_)).fetchone()[0]
        row = conn.execute(
            """INSERT INTO generated_assets (workspace_id, campaign_id, storyboard_id,
                    version_id, shot_id, shot_key, type, provider, provider_model,
                    prompt, settings, key, thumb_key, job_id, variant, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (workspace_id, campaign_id, storyboard_id, version_id, shot_id, shot_key,
             type_, provider, provider_model, prompt, json.dumps(settings or {}), key,
             thumb_key, job_id, next_variant, json.dumps(metadata or {}))).fetchone()
    return {'id': str(row[0]), 'variant': next_variant}


def select_asset(workspace_id: str, shot_id: str, asset_id: str) -> str:
    """Point the shot's frame/video slot at this asset. Validated by shot_key, not shot
    row id — the same asset (by shot_key) is valid for a shot in any version fork. Never
    forks (production state, like apply_event); raises ValueError on a superseded
    version's shot."""
    shot = _shot_scope(workspace_id, shot_id)
    _reject_superseded(shot)
    asset = _row_or_404(db.query(
        'SELECT id, shot_key, type FROM generated_assets WHERE id = %s '
        'AND workspace_id = %s', (asset_id, workspace_id), one=True), 'asset not found')
    if str(asset['shot_key']) != str(shot['shot_key']):
        raise ValueError('asset does not belong to this shot')
    if asset['type'] in FRAME_ASSET_TYPES:
        column = 'selected_frame_asset_id'
    elif asset['type'] in VIDEO_ASSET_TYPES:
        column = 'selected_video_asset_id'
    else:
        raise ValueError(f"asset type {asset['type']!r} is not selectable")
    db.query(f'UPDATE storyboard_shots SET {column} = %s, updated_at = now() '
             'WHERE id = %s', (asset_id, shot_id))
    return shot_id


def list_assets(workspace_id: str, shot_key: str, type_: str | None = None) -> list[dict]:
    """Every asset ever generated for this shot_key, across every version fork."""
    sql = 'SELECT * FROM generated_assets WHERE workspace_id = %s AND shot_key = %s'
    args: list = [workspace_id, shot_key]
    if type_:
        sql += ' AND type = %s'
        args.append(type_)
    sql += ' ORDER BY type, variant'
    return db.query(sql, tuple(args))


def active_jobs(workspace_id: str, version_id: str) -> list[dict]:
    """Running/queued jobs for this version — how the UI recovers after a refresh."""
    _version_scope(workspace_id, version_id)
    return jobs.active_for_version(version_id)


def shot_in_active_version(workspace_id: str, shot_key: str,
                            storyboard_id: str) -> dict | None:
    """The shot row carrying this shot_key in the storyboard's CURRENT active version, or
    None if that version has no such shot (it was deleted there after a fork)."""
    storyboard = _row_or_404(db.query(
        """SELECT sb.active_version_id FROM storyboards sb
             JOIN campaigns c ON c.id = sb.campaign_id
            WHERE sb.id = %s AND c.workspace_id = %s""",
        (storyboard_id, workspace_id), one=True), 'storyboard not found')
    return db.query(
        'SELECT * FROM storyboard_shots WHERE version_id = %s AND shot_key = %s',
        (storyboard['active_version_id'], shot_key), one=True)


def complete_generation(workspace_id: str, storyboard_id: str, shot_key: str,
                         event: str) -> str | None:
    """A background job started against whatever version was active when it launched —
    which may since have been forked (an edit against the approved version it was
    running on) or moved on by a fresh regenerate. Route the job's result to the
    shot_key's counterpart in the version that is active NOW: if that shot is still
    sitting in the *_generating state this event expects, apply the event there and
    return the shot id it transitioned. Otherwise the active shot has already moved past
    what this job was for, so this result is history only — the caller still records the
    asset via add_asset() (keyed by shot_key, so it attaches regardless of which version
    row is active), but no state transition happens and None is returned.
    """
    shot = shot_in_active_version(workspace_id, shot_key, storyboard_id)
    expected_state = _GENERATING_STATE_FOR_EVENT.get(event)
    if shot is None or expected_state is None or shot['state'] != expected_state:
        return None
    _, transitioned_shot_id, _ = apply_event(workspace_id, str(shot['id']), event)
    return transitioned_shot_id


def demo() -> None:
    if not os.environ.get('DATABASE_URL'):
        print('storyboard: DATABASE_URL not set, skipping')
        return
    import psycopg

    db.migrate()

    ws = str(db.query("INSERT INTO workspaces (name) VALUES ('storyboard-check') "
                      'RETURNING id', one=True)['id'])
    other_ws = str(db.query("INSERT INTO workspaces (name) VALUES ('storyboard-other') "
                            'RETURNING id', one=True)['id'])
    user = str(db.query(
        "INSERT INTO users (email, password_hash) VALUES (%s, 'x') RETURNING id",
        (f'sb-{uuid.uuid4().hex[:8]}@test',), one=True)['id'])
    piece = f'pc{uuid.uuid4().hex[:10]}'
    db.query("INSERT INTO pieces (id, workspace_id, user_id, category) "
             "VALUES (%s, %s, %s, 'ring')", (piece, ws, user))

    try:
        # --- campaign + storyboard + shots -----------------------------------------
        campaign = create_campaign(ws, 'Diwali 2026', brief={'goal': 'awareness'})
        campaign_id = str(campaign['id'])
        add_product(ws, campaign_id, piece, 'keep the stone-setting exact')

        shots = [{'duration': 3} for _ in range(4)] + [{'duration': 4, 'kind': 'end_card'}]
        created = create_storyboard(ws, campaign_id,
            {'target_duration': 16, 'title': 'Diwali hero'}, shots)
        version_id = created['version_id']
        storyboard_id = created['storyboard_id']

        state = get_version(ws, version_id)
        assert len(state['shots']) == 5
        assert state['total_duration'] == 16
        start_times = [float(s['start_time']) for s in state['shots']]
        assert start_times == [0, 3, 6, 9, 12], start_times
        positions = [s['position'] for s in state['shots']]
        assert positions == [0, 1, 2, 3, 4], positions
        shot2_id = str(state['shots'][1]['id'])

        # --- set_duration shifts every later start_time -----------------------------
        set_duration(ws, shot2_id, 6)
        state = get_version(ws, version_id)
        start_times = [float(s['start_time']) for s in state['shots']]
        assert start_times == [0, 3, 9, 12, 15], start_times
        assert state['total_duration'] == 19

        # --- duplicate / split / delete keep positions contiguous --------------------
        shot0_id = str(state['shots'][0]['id'])
        _, dup_id = duplicate_shot(ws, shot0_id)
        state = get_version(ws, version_id)
        positions = [s['position'] for s in state['shots']]
        assert positions == list(range(6)), positions
        assert len(state['shots']) == 6

        _, split_a, split_b = split_shot(ws, dup_id)
        state = get_version(ws, version_id)
        positions = [s['position'] for s in state['shots']]
        assert positions == list(range(7)), positions
        durations = {str(s['id']): float(s['duration']) for s in state['shots']}
        assert durations[split_a] == durations[split_b] == 1.5

        delete_shot(ws, split_b)
        state = get_version(ws, version_id)
        positions = [s['position'] for s in state['shots']]
        assert positions == list(range(6)), positions

        reorder(ws, version_id, [str(s['id']) for s in reversed(state['shots'])])
        state = get_version(ws, version_id)
        assert [s['position'] for s in state['shots']] == list(range(6))

        # --- update_spec + shot_state invalidation -----------------------------------
        # (pick an ordinary 'shot', not the end_card the reorder above may have moved
        # to position 0 — end_card allows no provider events at all)
        target_row = next(s for s in state['shots'] if s['kind'] == 'shot')
        target_id = str(target_row['id'])
        target_key = target_row['shot_key']
        apply_event(ws, target_id, 'approve_instructions')
        apply_event(ws, target_id, 'start_frame')
        apply_event(ws, target_id, 'frame_done')
        apply_event(ws, target_id, 'approve_frame')
        state = get_version(ws, version_id)
        target = next(s for s in state['shots'] if str(s['id']) == target_id)
        assert target['state'] == 'frame_approved'

        _, _, new_state = update_spec(ws, target_id, {'camera_angle': 'low angle'})
        assert new_state == 'ready_for_frame', new_state

        # bring it back up, then prove an audio field leaves state untouched
        apply_event(ws, target_id, 'start_frame')
        apply_event(ws, target_id, 'frame_done')
        apply_event(ws, target_id, 'approve_frame')
        _, _, new_state = update_spec(ws, target_id, {'music_cue': 'swell'})
        assert new_state == 'frame_approved', new_state

        # --- approve version, then edit -> fork ---------------------------------------
        approve_version(ws, version_id)
        sb_row = db.query('SELECT active_version_id FROM storyboards WHERE id = %s',
                          (storyboard_id,), one=True)
        assert str(sb_row['active_version_id']) == version_id

        forked_version_id, forked_shot_id = _resolve_shot(ws, target_id)
        assert forked_version_id != version_id, 'editing an approved version must fork'
        forked = get_version(ws, forked_version_id)
        forked_target = next(s for s in forked['shots'] if s['shot_key'] == target_key)
        assert str(forked_target['id']) == forked_shot_id
        assert forked_target['state'] == 'frame_approved', 'state carries into the fork'

        original_still = get_version(ws, version_id)
        assert original_still['version']['status'] == 'approved', \
            'the original version must be untouched by the fork'

        sb_row = db.query('SELECT active_version_id FROM storyboards WHERE id = %s',
                          (forked['storyboard']['id'],), one=True)
        assert str(sb_row['active_version_id']) == forked_version_id, \
            'active_version_id must move to the fork'

        # --- assets: variants, selection, and survival across a fork -----------------
        campaign_uuid = forked['storyboard']['campaign_id']
        asset_ids = []
        for _ in range(3):
            a = add_asset(ws, campaign_uuid, forked['storyboard']['id'],
                         forked_version_id, 'storyboard_image', f's3://fake/{uuid.uuid4()}',
                         shot_id=forked_shot_id, shot_key=target_key)
            asset_ids.append(a['id'])
        assert [a['variant'] for a in
                [db.query('SELECT variant FROM generated_assets WHERE id = %s',
                         (aid,), one=True) for aid in asset_ids]] == [1, 2, 3]
        # a 4th variant, to prove list_assets later sees every one ever made
        add_asset(ws, campaign_uuid, forked['storyboard']['id'], forked_version_id,
                 'storyboard_image', 's3://fake/x', shot_id=forked_shot_id,
                 shot_key=target_key)
        select_asset(ws, forked_shot_id, asset_ids[2])
        forked = get_version(ws, forked_version_id)
        forked_target = next(s for s in forked['shots'] if s['shot_key'] == target_key)
        assert str(forked_target['selected_frame_asset_id']) == asset_ids[2]

        # fork again (approve, then edit) and check the asset history + selection carry
        approve_version(ws, forked_version_id)
        second_fork_version, _second_fork_shot = _resolve_shot(ws, forked_shot_id)
        assert second_fork_version != forked_version_id
        listed = list_assets(ws, target_key, type_='storyboard_image')
        assert len(listed) == 4, len(listed)
        second_fork = get_version(ws, second_fork_version)
        second_target = next(s for s in second_fork['shots']
                             if s['shot_key'] == target_key)
        assert str(second_target['selected_frame_asset_id']) == asset_ids[2], \
            'selection must carry across a fork'

        # --- production events never fork an approved version -------------------------
        approve_version(ws, second_fork_version)
        count_versions = lambda: db.query(
            'SELECT COUNT(*) AS n FROM storyboard_versions WHERE storyboard_id = %s',
            (storyboard_id,), one=True)['n']
        versions_before = count_versions()
        prod_shot_id = str(second_target['id'])

        apply_event(ws, prod_shot_id, 'start_frame')   # regenerate: rolls back frame_approved
        apply_event(ws, prod_shot_id, 'frame_done')
        apply_event(ws, prod_shot_id, 'approve_frame')
        apply_event(ws, prod_shot_id, 'start_video')   # forward progress out of frame_approved
        apply_event(ws, prod_shot_id, 'video_done')
        _, _, prod_final_state = apply_event(ws, prod_shot_id, 'approve_video')
        assert prod_final_state == 'video_approved', prod_final_state
        assert count_versions() == versions_before, \
            'production events on an approved version must never fork'
        prod_row = db.query('SELECT version_id, state FROM storyboard_shots WHERE id = %s',
                            (prod_shot_id,), one=True)
        assert str(prod_row['version_id']) == second_fork_version, \
            'apply_event must update the shot in place, not move it to another version'
        assert prod_row['state'] == 'video_approved'

        # the regenerate above is the only rollback in that chain -> exactly one reset
        # row, and start_video (forward progress out of frame_approved) logged none
        reset_stages = [r['stage'] for r in db.query(
            "SELECT stage FROM approvals WHERE shot_id = %s AND decision = 'reset'",
            (prod_shot_id,))]
        assert reset_stages == ['frame'], reset_stages

        # --- update_spec demoting an approved shot still forks, and logs the reset ----
        # --- on the fork's shot, never on the frozen original --------------------------
        versions_before = count_versions()
        forked_version_2, forked_shot_2, state_after_edit = update_spec(
            ws, prod_shot_id, {'camera_move': 'orbit'})
        assert state_after_edit == 'ready_for_frame', state_after_edit
        assert forked_version_2 != second_fork_version, 'editing an approved version must fork'
        assert count_versions() == versions_before + 1

        assert not db.query(
            "SELECT 1 FROM approvals WHERE shot_id = %s AND decision = 'reset' "
            "AND stage = 'video'", (prod_shot_id,)), \
            'the reset must land on the fork, not the frozen original shot row'
        fork_reset_stages = [r['stage'] for r in db.query(
            "SELECT stage FROM approvals WHERE shot_id = %s AND decision = 'reset'",
            (forked_shot_2,))]
        assert fork_reset_stages == ['video'], fork_reset_stages

        # --- production events on a superseded version raise ---------------------------
        superseded = db.query('SELECT status FROM storyboard_versions WHERE id = %s',
                              (version_id,), one=True)
        assert superseded['status'] == 'superseded', superseded
        try:
            apply_event(ws, target_id, 'start_frame')
            raise AssertionError('apply_event allowed a production event on a '
                                 'superseded version')
        except ValueError:
            pass
        try:
            select_asset(ws, target_id, asset_ids[0])
            raise AssertionError('select_asset allowed a production event on a '
                                 'superseded version')
        except ValueError:
            pass

        # --- editing a SUPERSEDED version (not just an approved one) also forks --------
        # ensure_editable's fix: only 'draft' is edited in place. An old, superseded
        # direction must fork a fresh draft from itself when touched again, exactly like
        # editing the currently-approved version does — never edited in place, and never
        # refused either.
        versions_before_superseded_edit = count_versions()
        forked_from_superseded, _, _ = update_spec(ws, target_id, {'camera_angle': 'zz'})
        assert forked_from_superseded != version_id, \
            'editing a superseded version must fork, not edit it in place'
        assert count_versions() == versions_before_superseded_edit + 1
        sb_row = db.query('SELECT active_version_id FROM storyboards WHERE id = %s',
                          (storyboard_id,), one=True)
        assert str(sb_row['active_version_id']) == forked_from_superseded, \
            'active_version_id must move to the fork made from a superseded version'
        original_row = db.query('SELECT spec FROM storyboard_shots WHERE id = %s',
                                (target_id,), one=True)
        assert original_row['spec'].get('camera_angle') != 'zz', \
            'the frozen superseded shot row must be untouched by the edit'

        # --- complete_generation routes a job's result to the ACTIVE version's shot ---
        job_created = create_storyboard(ws, campaign_id,
            {'target_duration': 4, 'title': 'job-routing'}, [{'duration': 4}])
        job_storyboard_id = job_created['storyboard_id']
        job_version_id = job_created['version_id']
        job_shot = get_version(ws, job_version_id)['shots'][0]
        job_shot_id, job_shot_key = str(job_shot['id']), job_shot['shot_key']

        apply_event(ws, job_shot_id, 'approve_instructions')
        apply_event(ws, job_shot_id, 'start_frame')   # the "job" starts running here
        approve_version(ws, job_version_id)

        # an unrelated edit forks the version while that job is still in flight
        forked_job_version_id, forked_job_shot_id = _resolve_shot(ws, job_shot_id)
        assert forked_job_version_id != job_version_id
        sb_row = db.query('SELECT active_version_id FROM storyboards WHERE id = %s',
                          (job_storyboard_id,), one=True)
        assert str(sb_row['active_version_id']) == forked_job_version_id

        transitioned = complete_generation(ws, job_storyboard_id, job_shot_key,
                                           'frame_done')
        assert transitioned == forked_job_shot_id, transitioned
        landed = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                          (forked_job_shot_id,), one=True)
        assert landed['state'] == 'frame_review', landed['state']

        # the pre-fork row the job actually started against is untouched
        stale = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                         (job_shot_id,), one=True)
        assert stale['state'] == 'frame_generating', stale['state']

        # the active shot has moved past frame_generating now -> a repeat call is a no-op
        assert complete_generation(ws, job_storyboard_id, job_shot_key,
                                   'frame_done') is None
        assert shot_in_active_version(ws, uuid.uuid4(), job_storyboard_id) is None

        # --- regenerating a VIDEO (a new clip from the SAME approved frame) is a --------
        # --- rollback of the VIDEO approval only, never the frame's, and never forks ---
        # (an isolated shot/version, so it cannot interact with prod_shot_id's own reset
        # history checked above).
        video_regen_created = create_storyboard(
            ws, campaign_id, {'target_duration': 4, 'title': 'video-regen-check'},
            [{'duration': 4}])
        video_regen_version_id = video_regen_created['version_id']
        video_regen_shot_id = str(get_version(ws, video_regen_version_id)['shots'][0]['id'])
        apply_event(ws, video_regen_shot_id, 'approve_instructions')
        apply_event(ws, video_regen_shot_id, 'start_frame')
        apply_event(ws, video_regen_shot_id, 'frame_done')
        apply_event(ws, video_regen_shot_id, 'approve_frame')
        apply_event(ws, video_regen_shot_id, 'start_video')
        apply_event(ws, video_regen_shot_id, 'video_done')
        apply_event(ws, video_regen_shot_id, 'approve_video')
        approve_version(ws, video_regen_version_id)

        versions_before_video_regen = count_versions()
        _, _, video_regen_state = apply_event(ws, video_regen_shot_id, 'start_video')
        assert video_regen_state == 'video_generating', video_regen_state
        assert count_versions() == versions_before_video_regen, \
            'regenerating a video must never fork (production state, like start_frame)'
        video_regen_reset_stages = [r['stage'] for r in db.query(
            "SELECT stage FROM approvals WHERE shot_id = %s AND decision = 'reset'",
            (video_regen_shot_id,))]
        assert video_regen_reset_stages == ['video'], video_regen_reset_stages

        # --- cross-workspace access must return nothing / raise ----------------------
        assert get_campaign(other_ws, campaign_id) is None
        try:
            get_version(other_ws, version_id)
            raise AssertionError('a foreign workspace read a version')
        except NotFound:
            pass
        try:
            update_spec(other_ws, target_id, {'camera_angle': 'x'})
            raise AssertionError('a foreign workspace edited a shot')
        except NotFound:
            pass

        # --- the CHECK constraint rejects a video_clip with no shot_id ----------------
        try:
            db.query(
                """INSERT INTO generated_assets (workspace_id, campaign_id, storyboard_id,
                        version_id, type, key)
                   VALUES (%s, %s, %s, %s, 'video_clip', 's3://fake/orphan')""",
                (ws, campaign_uuid, forked['storyboard']['id'], second_fork_version))
            raise AssertionError('a video_clip with no shot_id was accepted')
        except psycopg.errors.CheckViolation:
            pass

    finally:
        db.query('DELETE FROM approvals WHERE workspace_id IN (%s, %s)', (ws, other_ws))
        db.query('DELETE FROM generated_assets WHERE workspace_id IN (%s, %s)',
                (ws, other_ws))
        db.query('DELETE FROM jobs WHERE workspace_id IN (%s, %s)', (ws, other_ws))
        db.query('DELETE FROM campaigns WHERE workspace_id IN (%s, %s)', (ws, other_ws))
        db.query('DELETE FROM pieces WHERE workspace_id = %s', (ws,))
        db.query('DELETE FROM workspaces WHERE id IN (%s, %s)', (ws, other_ws))
        db.query('DELETE FROM users WHERE id = %s', (user,))
        db.close()

    print('storyboard ok')


if __name__ == '__main__':
    demo()
