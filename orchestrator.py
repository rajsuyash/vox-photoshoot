"""Generation service layer for the storyboard ad editor: reference frames (Phase 3) and
shot clips (Phase 4) — start_video/run_video_shot mirror start_frame/run_frame's exact
claim/heartbeat/finish/settle shape, which itself mirrors app.py's run_video.

No FastAPI here — ads_api.py turns these into HTTP, the same split storyboard.py/ads_api.py
already draw. storyboard.py owns the state machine and persistence; this file owns talking
to providers and turning the result back into storyboard.py calls.

    .venv/bin/python orchestrator.py     # prompt composition + reference ordering + estimate
                                          # (DB parts skip without DATABASE_URL; no network)
"""

import base64
import concurrent.futures
import hashlib
import io
import json
import logging
import math
import os
import pathlib
import re
import statistics
import subprocess
import tempfile

import credits
import db
import jobs
import motion
import music
import pieces
import providers
import render
import shoot
import shot_state
import storage
import storyboard
import talent
import video

OUT_DIR = pathlib.Path('out/ads')
REF_CACHE_DIR = OUT_DIR / 'refs'

QUALITY = 'high'                 # matches shoot.py's DEFAULTS — the house default for frames
MAX_REFERENCE_IMAGES = 4
MAX_PROMPT_CHARS = 4000          # "keep it readable" — plenty for a shot prompt, never hit
                                  # in practice; a hard cap rather than a token-accurate budget.

# One-line switchable: fidelity_eval.py overrides this via the FIDELITY_MODEL_ADS env var
# to A/B a stricter/pricier model without touching this file.
FIDELITY_MODEL_ADS = os.environ.get('FIDELITY_MODEL_ADS', 'claude-haiku-4-5')

# A generic "is it the same piece" yes/no (the old FRAME_FIDELITY_SCHEMA) passed a frame
# that swapped a coin-link Lakshmi temple necklace + jhumka earrings for a plain chain and
# generic studs — the model had nothing to anchor "same" against beyond a vague impression.
# Itemizing per piece (chain construction, pendant motif, earring type, metal colour) before
# asking for a verdict is what actually caught it in the 2026-09-29 eval — see fidelity_eval.py.
FIDELITY_VISION_MAX_EDGE = 1024   # smaller than product.VISION_MAX_EDGE (1568): these checks
                                   # send several images per call and only need to compare
                                   # overall design, not fine print.

# check_shot_clip's per-sampled-frame jewellery checks (via check_frame_fidelity's max_edge
# param) can use a different resolution than the primary frame check — env-overridable, same
# pattern as FIDELITY_MODEL_ADS, so fidelity_eval.py can A/B it without a code change.
CLIP_FRAME_VISION_MAX_EDGE = int(os.environ.get('CLIP_FRAME_VISION_MAX_EDGE',
                                                 FIDELITY_VISION_MAX_EDGE))

ITEMIZED_PIECES_SCHEMA = {
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'piece': {'type': 'string'},
            'visible': {'type': 'boolean'},
            'matches': {'type': 'boolean'},
            'difference': {'type': 'string'},
        },
        'required': ['piece', 'visible', 'matches', 'difference'],
        'additionalProperties': False,
    },
}
FRAME_FIDELITY_SCHEMA = {
    'type': 'object',
    'properties': {
        'pieces': ITEMIZED_PIECES_SCHEMA,
        'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
        'reason': {'type': 'string'},
    },
    'required': ['pieces', 'verdict', 'reason'],
    'additionalProperties': False,
}

FRAME_FIDELITY_SYSTEM = (
    'You are a strict jewellery QC inspector. You will be shown the client\'s product '
    'reference photo(s) (the ground truth) followed by a generated ad frame. First list '
    'each distinct piece across the product photo(s) (e.g. necklace, earrings). For each, '
    'judge from the ad frame whether it is visible and whether it is the SAME design: '
    'chain/link construction, pendant shape and motif, earring type (stud/drop/jhumka) and '
    'motif, metal colour, proportions. A generic or simpler piece of the same category is '
    'NOT a match. Pieces not visible are not failures unless a clearly different piece is '
    'worn in their place. verdict=fail if any visible piece does not match or a different '
    'piece is worn instead.'
)


def _fidelity_image_b64(path, max_edge: int = FIDELITY_VISION_MAX_EDGE) -> str:
    """base64 JPEG for a fidelity-check image, downscaled to `max_edge` (defaults to
    FIDELITY_VISION_MAX_EDGE; check_shot_clip's per-frame jewellery checks can override
    it via CLIP_FRAME_VISION_MAX_EDGE to test finer detail on clip-extracted frames).

    Reuses product.encode() for its HEIC handling and decompression-bomb guard rather than
    duplicating them, then re-thumbnails its output down from product's own 1568px cap to
    this module's smaller one — these checks send several images per call.
    """
    from PIL import Image

    import product as product_module

    raw = base64.standard_b64decode(product_module.encode(path))
    with Image.open(io.BytesIO(raw)) as image:
        image.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
    return base64.standard_b64encode(buffer.getvalue()).decode()


def _fidelity_image_block(path, max_edge: int = FIDELITY_VISION_MAX_EDGE) -> dict:
    return {'type': 'image', 'source': {'type': 'base64',
            'media_type': 'image/jpeg', 'data': _fidelity_image_b64(path, max_edge)}}

NEGATIVE_CLAUSE = (
    'The jewellery must match the reference image exactly in shape, proportion, stone '
    'layout and metal colour — no redesign, and do not add any jewellery beyond what is '
    'described above.'
)

# The spec fields that describe the still image itself, in reading order, with the label
# each gets in the composed prompt. Mirrors shot_state.FIELD_GROUPS' 'frame' group —
# everything that invalidates a generated frame is exactly what belongs in its prompt.
SPEC_FIELDS = [
    ('shot_type', 'Shot'), ('camera_angle', 'Camera angle'), ('lens', 'Lens'),
    ('depth_of_field', 'Depth of field'), ('scene_description', 'Scene'),
    ('character_action', 'Action'), ('facial_expression', 'Expression'),
    ('product_interaction', 'Product interaction'), ('environment', 'Environment'),
    ('lighting', 'Lighting'), ('time_of_day', 'Time of day'), ('wardrobe', 'Wardrobe'),
    ('props', 'Props'),
]


class NotApproved(Exception):
    """A production event (start_frame) was requested against a version that is not
    'approved' — frames only generate against the storyboard the customer signed off."""


class EstimateMismatch(Exception):
    """A batch's confirm_credits didn't match the server's own estimate. .estimate is the
    fresh one, so the caller (ads_api.py) can hand it straight back to the client."""

    def __init__(self, estimate: dict):
        super().__init__(f"confirm_credits did not match the current estimate: {estimate}")
        self.estimate = estimate


class NotRenderable(Exception):
    """The version failed validate_for_render(). .problems is the human-readable list —
    the caller (ads_api.py) hands it straight back as the 422 body."""

    def __init__(self, problems: list[str]):
        super().__init__('; '.join(problems))
        self.problems = problems


# --- context: everything a version's shots need to compose a prompt, built once ----------

def _local_product_path(piece_row: dict) -> str | None:
    """The product's reference photo, on local disk — fetched from storage once and
    cached under REF_CACHE_DIR, the same recovery path app.piece_path uses."""
    key = pieces.key_for(piece_row)
    if not key:
        return None
    local = REF_CACHE_DIR / 'products' / pathlib.Path(key).name
    if not local.exists():
        try:
            storage.fetch(key, local)
        except FileNotFoundError:
            return None
    return str(local)


def _load_products(product_ids: list[str]) -> list[dict]:
    if not product_ids:
        return []
    rows = db.query(
        """SELECT cp.id, cp.fidelity_instructions, cp.piece_id, p.s3_key, p.category,
                  p.description, p.sku
             FROM campaign_products cp JOIN pieces p ON p.id = cp.piece_id
            WHERE cp.id = ANY(%s::uuid[])""", (product_ids,))
    out = []
    for r in rows:
        name = (r['sku'] or r['description'] or r['category'] or '').strip()
        out.append({
            'id': str(r['id']), 'name': name, 'description': name, 'category': r['category'],
            'fidelity_instructions': r['fidelity_instructions'],
            'local_path': _local_product_path({'id': r['piece_id'], 's3_key': r['s3_key']}),
        })
    return out


def _clean_description(text: str) -> str:
    """Some house cast.json entries carry a literal, unfilled '{EXPRESSION}' token (a
    pre-existing bug in how cast.json is generated, tracked separately from this phase —
    see video_spike.py's _subject_clause for the same workaround). We have no
    expression-selection UI for ads yet, so the placeholder is simply dropped rather than
    leaking into a real generation prompt.
    """
    return (text or '').replace('{EXPRESSION}', '').strip()


def _load_characters(character_ids: list[str], workspace_id: str) -> list[dict]:
    if not character_ids:
        return []
    rows = db.query('SELECT * FROM campaign_characters WHERE id = ANY(%s::uuid[])',
                    (character_ids,))
    cast_entries = None
    out = []
    for r in rows:
        description, face_path = '', None
        if r['talent_id']:
            owned = talent.owned(r['talent_id'], workspace_id)
            if owned:
                face = talent.face(owned)
                description, face_path = face['description'], face['file']
        elif r['cast_key']:
            if cast_entries is None:
                cast_entries = shoot.load_cast()
            entry = cast_entries.get(r['cast_key'])
            if entry:
                description, face_path = entry['description'], entry['file']
        else:
            description = (r['appearance'] or {}).get('description', '')
        out.append({'id': str(r['id']), 'name': r['name'],
                    'description': _clean_description(description), 'face_path': face_path})
    return out


def build_context(workspace_id: str, version_id: str) -> dict:
    """Everything shared across every shot in this version — campaign/storyboard style,
    every product and character referenced anywhere in it, local reference files resolved
    once. Built once per version (or per batch), never once per shot.
    """
    version = storyboard.get_version(workspace_id, version_id)
    campaign = storyboard.get_campaign(workspace_id, version['storyboard']['campaign_id'])
    product_ids = sorted({str(pid) for s in version['shots'] for pid in (s['product_ids'] or [])})
    character_ids = sorted({str(cid) for s in version['shots']
                            for cid in (s['character_ids'] or [])})
    products = {p['id']: p for p in _load_products(product_ids)}
    characters = {c['id']: c for c in _load_characters(character_ids, workspace_id)}
    return {'campaign': campaign, 'storyboard': version['storyboard'], 'shots': version['shots'],
            'products': products, 'characters': characters}


# --- prompt composition + reference ordering, pure functions, no I/O ---------------------

# Words in product_interaction that mean hands legitimately appear (holding/wearing the
# piece) — a product-only frame/clip still bars a face and body even then, but not hands.
HAND_INTERACTION_WORDS = ('hand', 'hands', 'finger', 'fingers', 'hold', 'holds', 'holding',
                          'wear', 'wears', 'wearing', 'worn')


def _shot_has_person(shot: dict) -> bool:
    """Whether this shot is meant to show a person at all.

    A 2026-09-27 spike against a real storyboard found a product-only macro shot
    (character_ids=[], no character_action in its spec — just a necklace on a tray) whose
    composed motion prompt still carried motion.FIDELITY_LOCK's person language ("her
    whole face stays in frame"), and Kling obeyed by inventing a woman partway through the
    clip. compose_frame_prompt/compose_motion_prompt both call this first so a shot with no
    person in its own spec never gets prompt text describing one.
    """
    if shot.get('character_ids'):
        return True
    spec = shot.get('spec') or {}
    return bool((spec.get('character_action') or '').strip())


def _frame_person_clause(shot: dict) -> str:
    """compose_frame_prompt's product-only amendment: without this, a shot with no
    person in its spec still had no person language (compose_frame_prompt never invents
    one), but it also never said the STILL must stay person-free — so an image model is
    free to add one anyway. Empty string for a shot that IS meant to show a person."""
    if _shot_has_person(shot):
        return ''
    spec = shot.get('spec') or {}
    interaction = (spec.get('product_interaction') or '').lower()
    if any(re.search(rf'\b{word}s?\b', interaction) for word in HAND_INTERACTION_WORDS):
        return 'Only a hand may appear, holding or wearing the piece — no face or body.'
    return 'No people, hands or faces appear in this shot.'


def compose_frame_prompt(ctx: dict, shot: dict) -> str:
    """Deterministic, no LLM. Layers: brand/campaign style -> the one grade for the whole
    ad -> a block per character -> a block per product (with fidelity instructions) -> the
    shot spec (or the user's own image_prompt override, still wrapped in the layers above)
    -> a negative clause. Reuses the phrasing locations.compose's CRAFT_BASE/negative
    clauses use for product fidelity, rather than inventing a second vocabulary for it.
    """
    campaign = ctx['campaign'] or {}
    board = ctx['storyboard'] or {}
    spec = shot.get('spec') or {}
    parts = []

    style_bits = [b for b in (campaign.get('brand_style'), campaign.get('campaign_style'))
                 if b]
    if style_bits:
        parts.append('Brand style: ' + '; '.join(style_bits) + '.')

    grade_bits = [b for b in (board.get('visual_style'), board.get('palette')) if b]
    if grade_bits:
        parts.append('Visual grade, one look for the whole ad: ' + '; '.join(grade_bits) + '.')

    for cid in shot.get('character_ids') or []:
        character = ctx['characters'].get(str(cid))
        if not character:
            continue
        bits = [character.get('name', ''), character.get('description', '')]
        if spec.get('wardrobe'):
            bits.append(f"wearing {spec['wardrobe']}")
        text = ', '.join(b for b in bits if b)
        if text:
            parts.append(f'Character — {text}.')

    for pid in shot.get('product_ids') or []:
        product_ctx = ctx['products'].get(str(pid))
        if not product_ctx:
            continue
        desc = product_ctx.get('description') or product_ctx.get('category') or 'the piece'
        parts.append(
            f'Product — {desc}, shown from the reference image. Reproduce exactly: shape, '
            'stones, metal colour, proportions, scale.')
        if product_ctx.get('fidelity_instructions'):
            parts.append(product_ctx['fidelity_instructions'])

    if shot.get('image_prompt'):
        parts.append(shot['image_prompt'])
    else:
        spec_bits = [f'{label}: {spec[key]}' for key, label in SPEC_FIELDS if spec.get(key)]
        if spec_bits:
            parts.append('. '.join(spec_bits) + '.')

    person_clause = _frame_person_clause(shot)
    if person_clause:
        parts.append(person_clause)

    parts.append(NEGATIVE_CLAUSE)
    prompt = ' '.join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS].rsplit(' ', 1)[0] + '…'
    return prompt


def reference_images(ctx: dict, shot: dict) -> list[str]:
    """Local file paths, product photos first (the reference that must not be
    compromised — same ordering shoot.build uses), then the first character's master
    portrait, then (continuity) the previous shot's selected+approved frame if it shares
    a character with this one. Capped at MAX_REFERENCE_IMAGES. Uploading is the caller's
    job (run_frame), same split shoot.shoot() draws between build() and the upload call.
    """
    paths = []
    for pid in shot.get('product_ids') or []:
        product_ctx = ctx['products'].get(str(pid))
        if product_ctx and product_ctx.get('local_path'):
            paths.append(product_ctx['local_path'])

    character_ids = [str(cid) for cid in (shot.get('character_ids') or [])]
    if character_ids:
        character = ctx['characters'].get(character_ids[0])
        if character and character.get('face_path'):
            paths.append(character['face_path'])

    shots = ctx.get('shots') or []
    index = next((i for i, s in enumerate(shots) if str(s['id']) == str(shot['id'])), None)
    if index is not None and index > 0 and character_ids:
        previous = shots[index - 1]
        previous_characters = {str(cid) for cid in (previous.get('character_ids') or [])}
        if (previous_characters & set(character_ids) and previous.get('selected_frame_asset_id')
                and previous.get('state') in ('frame_approved', 'video_review',
                                              'video_approved', 'video_failed')):
            continuity_path = _local_asset_path(previous['selected_frame_asset_id'])
            if continuity_path:
                paths.append(continuity_path)

    return paths[:MAX_REFERENCE_IMAGES]


def _local_asset_path(asset_id: str) -> str | None:
    """A local copy of one generated_assets row's key, fetched once and cached.

    Used both for continuity references and (Phase 4) for the frame a clip animates —
    real keys look like `ads/<campaign>/<shot_key>/frame-<variant>.png`, so the bare
    filename alone is NOT unique: every shot's own first variant is literally
    "frame-1.png". The cache path keeps the shot_key segment too, or two different
    shots' assets collide on one cached file (the second call would silently hand back
    the FIRST shot's picture instead of fetching its own).
    """
    row = db.query('SELECT key FROM generated_assets WHERE id = %s', (asset_id,), one=True)
    if row is None:
        return None
    key_path = pathlib.Path(row['key'])
    local_name = f'{key_path.parent.name}-{key_path.name}' if key_path.parent.name else key_path.name
    local = REF_CACHE_DIR / 'continuity' / local_name
    if not local.exists():
        try:
            storage.fetch(row['key'], local)
        except FileNotFoundError:
            return None
    return str(local)


def _pick_aspect(provider, aspect_ratio: str) -> tuple[str, str | None]:
    """(aspect actually requested, a note if it had to be substituted). fal supports
    9:16/4:5/1:1/16:9 directly today, so the substitution path is a safety net, not the
    common case."""
    if aspect_ratio in provider.aspect_ratios:
        return aspect_ratio, None
    width, height = (int(x) for x in aspect_ratio.split(':'))
    nearest = provider.nearest_aspect(width, height)
    return nearest, f'{provider.name} does not support {aspect_ratio}; used {nearest} instead'


def _seed_for(shot_key: str, variant: int) -> int:
    """Stable-ish seed from shot_key+variant — not a global uniqueness guarantee (the
    real variant number is only known once add_asset's transaction runs), just enough
    that two generations of the same shot/variant tend to reproduce."""
    digest = hashlib.sha256(f'{shot_key}:{variant}'.encode()).hexdigest()
    return int(digest[:8], 16)


# --- motion prompt composition, pure, no I/O, no LLM ------------------------------------
#
# Deterministic like compose_frame_prompt, not a video.direct()-style Anthropic call: a
# storyboard shot's spec already names the action, camera move and intensity (the
# director LLM chose them once when the board was written), so there is nothing left for
# a second model call to decide. Reuses motion.py's FIDELITY_LOCK/NEGATIVE rather than a
# second copy of either — see motion.render() for the sibling prompt this mirrors.

# One phrase per director.py CAMERA_MOVES value — a different vocabulary from
# motion.CAMERAS (that one is keyed for a single still's reveal-a-piece video; this one is
# keyed for a storyboard shot's own camera_move spec field), so it is its own small table
# rather than forcing one shared dict across two unrelated domains.
CAMERA_MOVE_PROSE = {
    'static': 'the camera holds still',
    'rack_focus': 'the focus shifts smoothly from the background to the foreground',
    'slow_push': 'the camera pushes in slowly',
    'slow_pull': 'the camera pulls back slowly',
    'orbit': 'the camera orbits gently around the subject',
    'pan': 'the camera pans slowly across the scene',
    'drift': 'the camera drifts gently sideways',
    'crane_rise': 'the camera rises smoothly, revealing more of the scene',
}
DEFAULT_CAMERA_MOVE = 'static'

# director.py's motion_intensity has no 'high' pace in motion.PACE (only slow/medium) —
# 'high' still maps to medium rather than inventing a third pace value nothing else uses.
MOTION_INTENSITY_PACE = {'low': motion.PACE['slow'], 'medium': motion.PACE['medium'],
                         'high': motion.PACE['medium']}

# motion.FIDELITY_LOCK was written for the old single-still flow, where there is always a
# model in frame — "her whole face stays in frame; her identity does not change" — and a
# 2026-09-27 spike showed that language, injected into a product-only macro shot (no
# character_ids, nothing in the spec naming a person), made Kling invent a woman partway
# through the clip. PRODUCT_LOCK is the person-free equivalent, used whenever
# _shot_has_person(shot) is False; the extra negative terms below go with it, since
# motion.NEGATIVE (Kling only) never had to rule out an invented person before.
PRODUCT_LOCK = (
    'One continuous shot, no cuts. No people, hands or faces appear. The jewellery '
    'stays exactly as in the first frame — same metal, stones, shape, size and '
    'position; nothing added or removed. Only light and camera move.'
)
PRODUCT_NEGATIVE_EXTRA = ('person, woman, model, face, hands, body, cut, scene change, '
                          'transition')


def _cap_first(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def compose_motion_prompt(ctx: dict, shot: dict) -> tuple[str, str]:
    """(prompt, negative) for one shot's clip. Deterministic, built from the shot's own
    spec (character_action, camera_move, product_interaction, motion_intensity,
    environment, emotional_beat) plus the storyboard's palette — the video counterpart of
    compose_frame_prompt.

    shot['motion_prompt'] (a user override) replaces only the action/camera beat, exactly
    like video.direct's motion/mood override never touches FIDELITY_LOCK: the fidelity
    lock and negative are appended either way, never left to an override to drop.
    """
    board = ctx['storyboard'] or {}
    spec = shot.get('spec') or {}
    override = (shot.get('motion_prompt') or '').strip()

    if override:
        body = override
    else:
        parts = []
        action = (spec.get('character_action') or '').strip()
        if action:
            parts.append(f'{_cap_first(action)}.')
        interaction = (spec.get('product_interaction') or '').strip()
        if interaction:
            parts.append(f'{_cap_first(interaction)}.')
        move_prose = CAMERA_MOVE_PROSE.get(spec.get('camera_move'),
                                           CAMERA_MOVE_PROSE[DEFAULT_CAMERA_MOVE])
        pace = MOTION_INTENSITY_PACE.get(spec.get('motion_intensity'),
                                         motion.PACE['medium'])
        parts.append(f'{_cap_first(move_prose)}, {pace}.')
        environment = (spec.get('environment') or '').strip()
        if environment:
            parts.append(f'{_cap_first(environment)}.')
        beat = (spec.get('emotional_beat') or '').strip()
        if beat:
            parts.append(f'Mood: {beat}.')
        palette = board.get('palette') or board.get('visual_style') or ''
        if palette:
            parts.append(f'One grade throughout: {palette}.')
        # parts always has at least the camera-move beat (CAMERA_MOVE_PROSE always
        # resolves, defaulting to DEFAULT_CAMERA_MOVE), so this never joins empty.
        body = ' '.join(parts)

    if _shot_has_person(shot):
        lock = f'{motion.FIDELITY_LOCK} One continuous shot, no cuts.'
        negative = motion.NEGATIVE
    else:
        lock = PRODUCT_LOCK
        negative = f'{motion.NEGATIVE}, {PRODUCT_NEGATIVE_EXTRA}'

    prompt = f'{body} {lock}'
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS].rsplit(' ', 1)[0] + '…'
    return prompt, negative


def _clip_seconds(shot_duration: float, provider) -> int:
    """The clip length to actually generate at: at least the provider's own minimum, at
    least the shot's own duration rounded up, and always a value the provider actually
    supports — the smallest one that clears both floors. Shots generate at their own
    length (Phase 0 finding), not a fixed 5/10s, so a 2.5s shot on a 3-15s-capable
    provider generates a 3s clip, not a wasted 5s one."""
    needed = max(min(provider.durations), math.ceil(float(shot_duration)))
    supported = sorted(d for d in provider.durations if d >= needed)
    return supported[0] if supported else max(provider.durations)


# --- estimate ------------------------------------------------------------------------

def estimate(workspace_id: str, version_id: str, stage: str = 'frames') -> dict:
    if stage not in ('frames', 'videos', 'music'):
        raise ValueError(f'unknown stage {stage!r}')
    version = storyboard.get_version(workspace_id, version_id)
    if stage == 'frames':
        eligible = [str(s['id']) for s in version['shots']
                    if s['kind'] == 'shot' and s['state'] in ('ready_for_frame', 'frame_failed')]
        per_shot = credits.cost('ad_frame')
        return {'shots': eligible, 'per_shot': per_shot, 'credits': per_shot * len(eligible)}

    if stage == 'music':
        # One flat price for the whole bed — there is no per-shot breakdown, unlike
        # frames/videos, so 'shots' stays empty and per_shot IS the total.
        provider = music.get()
        seconds = music.seconds_for(version)
        total = music.credits_for(seconds, provider)
        return {'shots': [], 'per_shot': total, 'credits': total, 'seconds': seconds}

    # 'videos': a clip's price depends on its own length, so per_shot is a map, not one
    # flat number — the UI shows each shot's own price rather than an average.
    #
    # per_shot is priced for every shot a NEW clip could legally start against
    # (shot_state.py allows start_video from frame_approved, video_failed, video_review
    # AND video_approved — a customer reshooting an already-approved clip), so the
    # card-level "New clip" button always has a price. 'shots'/'credits' stay narrower —
    # only frame_approved/video_failed count toward the BATCH "generate all videos"
    # estimate, unchanged from before this widening.
    provider = video.get()
    per_shot: dict[str, int] = {}
    eligible: list[str] = []
    for s in version['shots']:
        if s['kind'] != 'shot' or s['state'] not in (
                'frame_approved', 'video_failed', 'video_review', 'video_approved'):
            continue
        clip_seconds = _clip_seconds(float(s['duration']), provider)
        per_shot[str(s['id'])] = video.credits_for(clip_seconds, provider)
        if s['state'] in ('frame_approved', 'video_failed'):
            eligible.append(str(s['id']))
    return {'shots': eligible, 'per_shot': per_shot,
           'credits': sum(per_shot[sid] for sid in eligible)}


# --- start a single frame job ----------------------------------------------------------

def _frame_shot_row(workspace_id: str, shot_id: str) -> dict:
    row = db.query(
        """SELECT sh.id, sh.kind, sh.state, sh.shot_key, sh.version_id,
                  sv.status AS version_status, sv.storyboard_id, sb.campaign_id,
                  c.workspace_id
             FROM storyboard_shots sh
             JOIN storyboard_versions sv ON sv.id = sh.version_id
             JOIN storyboards sb ON sb.id = sv.storyboard_id
             JOIN campaigns c ON c.id = sb.campaign_id
            WHERE sh.id = %s AND c.workspace_id = %s""",
        (shot_id, workspace_id), one=True)
    if row is None:
        raise storyboard.NotFound('shot not found')
    return row


def start_frame(workspace_id: str, shot_id: str, idempotency_key: str, user_id: str) -> str:
    """Validate, reserve, and start one shot's frame job. Mirrors POST /api/videos: job
    plus credit reserve in one transaction, then the state event — never the reverse,
    or a crash between them either loses the credit or starts a job nothing paid for.
    """
    shot = _frame_shot_row(workspace_id, shot_id)
    if shot['kind'] != 'shot':
        raise ValueError('only ordinary shots generate frames — not the end card')
    if shot['version_status'] != 'approved':
        raise NotApproved('approve the storyboard before generating frames')
    event = 'retry' if shot['state'] == 'frame_failed' else 'start_frame'
    shot_state.transition(shot['state'], event, kind=shot['kind'])   # IllegalTransition if not

    per_shot = credits.cost('ad_frame')
    params = {'shot_id': str(shot_id), 'shot_key': str(shot['shot_key']),
             'storyboard_id': str(shot['storyboard_id']), 'campaign_id': str(shot['campaign_id']),
             'version_id': str(shot['version_id'])}
    with db.tx() as conn:
        job = jobs.create(workspace_id, user_id, 'ad_frame', idempotency_key, params,
                          reserved_credits=per_shot, conn=conn,
                          storyboard_version_id=shot['version_id'], storyboard_shot_id=shot_id)
        if job['created']:
            credits.reserve(conn, workspace_id, str(job['id']), per_shot)

    if job['created']:
        try:
            storyboard.apply_event(workspace_id, shot_id, event, user_id=user_id)
        except Exception as error:
            # Vanishingly rare (the shot changed state between the check above and here)
            # but the job and its reservation already committed, so both are unwound by
            # hand rather than through jobs.finish/settle, which expect a claimed job.
            credits.settle(str(job['id']), delivered=0)
            db.query("UPDATE jobs SET status = 'failed', error = %s, finished_at = now() "
                     'WHERE id = %s', (str(error), job['id']))
            raise
    return str(job['id'])


def start_frames(workspace_id: str, version_id: str, confirm_credits: int,
                 idempotency_key: str, user_id: str) -> list[str]:
    """Batch: recompute the estimate server-side and refuse a stale confirm, then one job
    per eligible shot with a per-shot idempotency key derived from the batch's."""
    fresh = estimate(workspace_id, version_id, stage='frames')
    if int(confirm_credits) != fresh['credits']:
        raise EstimateMismatch(fresh)
    return [start_frame(workspace_id, shot_id, f'{idempotency_key}:{shot_id}', user_id)
            for shot_id in fresh['shots']]


# --- start a single video job ------------------------------------------------------------

def _video_shot_row(workspace_id: str, shot_id: str) -> dict:
    row = db.query(
        """SELECT sh.id, sh.kind, sh.state, sh.shot_key, sh.version_id, sh.duration,
                  sh.selected_frame_asset_id, sv.status AS version_status,
                  sv.storyboard_id, sb.campaign_id, c.workspace_id
             FROM storyboard_shots sh
             JOIN storyboard_versions sv ON sv.id = sh.version_id
             JOIN storyboards sb ON sb.id = sv.storyboard_id
             JOIN campaigns c ON c.id = sb.campaign_id
            WHERE sh.id = %s AND c.workspace_id = %s""",
        (shot_id, workspace_id), one=True)
    if row is None:
        raise storyboard.NotFound('shot not found')
    return row


def start_video(workspace_id: str, shot_id: str, idempotency_key: str, user_id: str) -> str:
    """Validate, reserve, and start one shot's clip job. Mirrors start_frame exactly —
    job plus credit reserve in one transaction, then the state event.

    A clip needs an approved frame to animate (text-to-video isn't supported by any
    registered provider). Most of the time that surfaces as an IllegalTransition — a shot
    that never reached frame_approved cannot take a start_video/retry event at all — but
    the state machine alone does not guarantee a SELECTED frame (approve-frame can legally
    fire with no asset_id and nothing pre-selected), so the explicit check below is a real
    backstop, not just paranoia. It runs after the transition check so the common case
    (no frame yet at all) reads as "wrong state" rather than "no frame selected".
    """
    shot = _video_shot_row(workspace_id, shot_id)
    if shot['kind'] != 'shot':
        raise ValueError('only ordinary shots generate clips — not the end card')
    if shot['version_status'] != 'approved':
        raise NotApproved('approve the storyboard before generating videos')
    event = 'retry' if shot['state'] == 'video_failed' else 'start_video'
    shot_state.transition(shot['state'], event, kind=shot['kind'])   # IllegalTransition if not
    if not shot['selected_frame_asset_id']:
        raise ValueError('select and approve a frame before generating a clip')

    provider = video.get()
    clip_seconds = _clip_seconds(float(shot['duration']), provider)
    per_shot = video.credits_for(clip_seconds, provider)
    params = {'shot_id': str(shot_id), 'shot_key': str(shot['shot_key']),
             'storyboard_id': str(shot['storyboard_id']), 'campaign_id': str(shot['campaign_id']),
             'version_id': str(shot['version_id']), 'clip_seconds': clip_seconds}
    with db.tx() as conn:
        job = jobs.create(workspace_id, user_id, 'ad_video', idempotency_key, params,
                          reserved_credits=per_shot, conn=conn,
                          storyboard_version_id=shot['version_id'], storyboard_shot_id=shot_id)
        if job['created']:
            credits.reserve(conn, workspace_id, str(job['id']), per_shot)

    if job['created']:
        try:
            storyboard.apply_event(workspace_id, shot_id, event, user_id=user_id)
        except Exception as error:
            # Same unwind as start_frame: the job/reservation already committed, and the
            # shot changed state between the check above and here.
            credits.settle(str(job['id']), delivered=0)
            db.query("UPDATE jobs SET status = 'failed', error = %s, finished_at = now() "
                     'WHERE id = %s', (str(error), job['id']))
            raise
    return str(job['id'])


def start_videos(workspace_id: str, version_id: str, confirm_credits: int,
                 idempotency_key: str, user_id: str) -> list[str]:
    """Batch, mirrors start_frames: recompute the estimate server-side and refuse a stale
    confirm, then one job per eligible shot."""
    fresh = estimate(workspace_id, version_id, stage='videos')
    if int(confirm_credits) != fresh['credits']:
        raise EstimateMismatch(fresh)
    return [start_video(workspace_id, shot_id, f'{idempotency_key}:{shot_id}', user_id)
            for shot_id in fresh['shots']]


# --- music (version-level, no shot) -----------------------------------------------------

def start_music(workspace_id: str, version_id: str, confirm_credits: int,
                idempotency_key: str, user_id: str) -> str:
    """Validate, reserve, and start the version's music job. Mirrors start_frames'
    EstimateMismatch gate (there is only one "shot" here — the whole version — so this
    reads like a batch-of-one rather than start_frame's single-shot shape)."""
    version = storyboard.get_version(workspace_id, version_id)
    if version['version']['status'] != 'approved':
        raise NotApproved('approve the storyboard before generating music')
    fresh = estimate(workspace_id, version_id, stage='music')
    if int(confirm_credits) != fresh['credits']:
        raise EstimateMismatch(fresh)

    params = {'version_id': str(version_id),
             'storyboard_id': str(version['storyboard']['id']),
             'campaign_id': str(version['storyboard']['campaign_id'])}
    with db.tx() as conn:
        job = jobs.create(workspace_id, user_id, 'ad_music', idempotency_key, params,
                          reserved_credits=fresh['credits'], conn=conn,
                          storyboard_version_id=version_id)
        if job['created']:
            credits.reserve(conn, workspace_id, str(job['id']), fresh['credits'])
    return str(job['id'])


def run_music(job_id: str) -> None:
    """Claim, compose the score, save, auto-select if nothing is selected yet, settle.
    No shot_state event — music has no shot to drive through the state machine."""
    if not jobs.claim(job_id):
        return
    job = db.query('SELECT workspace_id, reserved_credits, params FROM jobs WHERE id = %s',
                   (job_id,), one=True)
    workspace_id = str(job['workspace_id'])
    reserved = int(job['reserved_credits'])
    params = job['params'] or {}
    version_id = params['version_id']
    storyboard_id, campaign_id = params['storyboard_id'], params['campaign_id']

    try:
        jobs.progress(job_id, 'composing', None, 'composing the score…', force=True)
        version = storyboard.get_version(workspace_id, version_id)
        provider = music.get()

        def on_provider_progress(status):
            stage = {'Queued': 'queued at provider',
                     'InProgress': 'composing the score'}.get(type(status).__name__)
            if stage:
                jobs.progress(job_id, stage, None, f'{stage}…')
            else:
                jobs.heartbeat(job_id)

        variant_guess = int(db.query(
            "SELECT COUNT(*) AS n FROM generated_assets "
            "WHERE storyboard_id = %s AND type = 'music'", (storyboard_id,),
            one=True)['n']) + 1
        out_path = OUT_DIR / 'music' / job_id / f'variant-{variant_guess}.mp3'
        result = music.generate(version, out_path, provider, on_progress=on_provider_progress,
                                seed=_seed_for(str(version_id), variant_guess))
        jobs.heartbeat(job_id)

        jobs.progress(job_id, 'saving', 0.98, 'saving the score…', force=True)
        key = f'ads/{campaign_id}/music/{version_id}-{variant_guess}.mp3'
        storage.put(result['path'], key)
        chunks = len((result['arguments'].get('composition_plan') or {}).get('chunks') or [])

        asset = storyboard.add_asset(
            workspace_id, campaign_id, storyboard_id, version_id, 'music', key,
            provider=provider.backend, provider_model=provider.model,
            settings={'arguments': result['arguments']},
            job_id=job_id, metadata={'duration': result['duration'], 'chunks': chunks})

        current = db.query('SELECT selected_music_asset_id FROM storyboard_versions '
                           'WHERE id = %s', (version_id,), one=True)
        if not current or not current['selected_music_asset_id']:
            try:
                storyboard.select_music(workspace_id, version_id, asset['id'])
            except ValueError:
                # The version was superseded while this job ran — the asset still
                # exists (and is still pickable from any other version of the same
                # storyboard), it just cannot be auto-selected onto a frozen version.
                pass

        credits.settle(job_id, delivered=reserved)
        jobs.finish(job_id, 'succeeded', settled_credits=reserved)
    except Exception as error:                       # noqa: BLE001 - report, don't crash
        logging.exception('orchestrator.run_music failed for job %s', job_id)
        credits.settle(job_id, delivered=0)
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


# --- final render ------------------------------------------------------------------------

def validate_for_render(workspace_id: str, version_id: str) -> list[str]:
    """Human-readable problems standing between this version and a final render. Empty
    means ready. Checked fresh on every GET /api/versions/{id} (render_ready) and again,
    authoritatively, at start_render — the readiness checklist and the render button's
    gate must never disagree."""
    version = storyboard.get_version(workspace_id, version_id)
    shots = version['shots']
    ordinary = [s for s in shots if s['kind'] == 'shot']
    end_cards = [s for s in shots if s['kind'] == 'end_card']
    problems = []

    if not ordinary:
        problems.append('the ad has no shots yet')

    for shot in ordinary:
        label = f"shot {int(shot['position']) + 1}"
        if shot['state'] != 'video_approved' or not shot.get('selected_video_asset_id'):
            problems.append(f'{label} has no approved clip yet')
            continue
        asset = db.query('SELECT key, metadata FROM generated_assets WHERE id = %s',
                         (shot['selected_video_asset_id'],), one=True)
        if asset is None:
            problems.append(f"{label}'s approved clip could not be found")
            continue
        duration = ((asset['metadata'] or {}).get('probe') or {}).get('duration')
        if duration is None:
            local = _local_asset_path(str(shot['selected_video_asset_id']))
            if not local:
                problems.append(f"{label}'s clip file is missing from storage")
                continue
            duration = video._probe(pathlib.Path(local))['duration']
        if float(duration) < float(shot['duration']) - 0.05:
            problems.append(f"{label}'s clip ({float(duration):.1f}s) is shorter than "
                            f"its {float(shot['duration']):.1f}s shot duration")

    if len(end_cards) != 1:
        problems.append(f'exactly one end card is required, found {len(end_cards)}')
    elif not ((end_cards[0].get('spec') or {}).get('brand_text') or '').strip():
        problems.append('the end card needs brand text')

    music_info = storyboard.music_state(workspace_id, version_id)
    if not (music_info['skipped'] or (music_info['selected_id'] and music_info['approved'])):
        problems.append('choose music, or silent, before rendering')

    return problems


def start_render(workspace_id: str, version_id: str, idempotency_key: str,
                 user_id: str) -> str:
    """Validate, then start the render job. 0 credits — this is local ffmpeg compute,
    never a paid provider call."""
    version = storyboard.get_version(workspace_id, version_id)
    if version['version']['status'] != 'approved':
        raise NotApproved('approve the storyboard before rendering')
    problems = validate_for_render(workspace_id, version_id)
    if problems:
        raise NotRenderable(problems)

    params = {'version_id': str(version_id),
             'storyboard_id': str(version['storyboard']['id']),
             'campaign_id': str(version['storyboard']['campaign_id'])}
    job = jobs.create(workspace_id, user_id, 'ad_render', idempotency_key, params,
                      reserved_credits=0, storyboard_version_id=version_id)
    return str(job['id'])


def _workspace_logo_path(workspace_id: str) -> pathlib.Path | None:
    """The workspace's brand logo, fetched from storage once and cached — same recovery
    path as _local_product_path, for the end card's branding block."""
    row = db.query('SELECT brand_logo_key FROM workspaces WHERE id = %s',
                   (workspace_id,), one=True)
    key = row.get('brand_logo_key') if row else None
    if not key:
        return None
    local = REF_CACHE_DIR / 'logos' / pathlib.Path(key).name
    if not local.exists():
        try:
            storage.fetch(key, local)
        except FileNotFoundError:
            return None
    return local


def run_render(job_id: str) -> None:
    """Claim, assemble every approved clip + the end card + (optional) music into the
    final MP4 via render.render(), save it, and record a final_renders row. A failure
    here still writes a final_renders row (status='failed') so the version's render
    history shows the attempt, not just silence."""
    if not jobs.claim(job_id):
        return
    job = db.query('SELECT workspace_id, params FROM jobs WHERE id = %s',
                   (job_id,), one=True)
    workspace_id = str(job['workspace_id'])
    params = job['params'] or {}
    version_id = params['version_id']
    storyboard_id, campaign_id = params['storyboard_id'], params['campaign_id']
    aspect = '9:16'

    try:
        jobs.progress(job_id, 'preparing', None, 'preparing the render…', force=True)
        version = storyboard.get_version(workspace_id, version_id)
        aspect = version['storyboard'].get('aspect_ratio') or '9:16'
        shots = sorted(version['shots'], key=lambda s: s['position'])
        ordinary = [s for s in shots if s['kind'] == 'shot']
        end_card_shot = next(s for s in shots if s['kind'] == 'end_card')

        segments, clip_asset_ids = [], []
        for shot in ordinary:
            asset_id = str(shot['selected_video_asset_id'])
            local_clip = _local_asset_path(asset_id)
            if not local_clip:
                raise RuntimeError(
                    f"clip for shot {int(shot['position']) + 1} is missing from storage")
            transition_out = (shot.get('spec') or {}).get('transition_out') or 'cut'
            segments.append(render.Segment(clip=pathlib.Path(local_clip),
                                           duration=float(shot['duration']),
                                           transition_out=transition_out))
            clip_asset_ids.append(asset_id)

        end_spec = end_card_shot.get('spec') or {}
        end_card = render.EndCard(duration=float(end_card_shot['duration']),
                                  brand_text=(end_spec.get('brand_text') or '').strip(),
                                  tagline=(end_spec.get('tagline') or '').strip(),
                                  logo=_workspace_logo_path(workspace_id),
                                  style=end_spec.get('end_card_style') or render.EndCard.style,
                                  keep_case=bool(end_spec.get('end_card_keep_case')))

        music_info = storyboard.music_state(workspace_id, version_id)
        music_path, music_asset_id = None, None
        if music_info['selected_id'] and music_info['approved']:
            music_asset_id = music_info['selected_id']
            music_row = db.query('SELECT key FROM generated_assets WHERE id = %s',
                                 (music_asset_id,), one=True)
            music_path = REF_CACHE_DIR / 'music' / f'{music_asset_id}.mp3'
            if not music_path.exists():
                storage.fetch(music_row['key'], music_path)

        def on_render_progress(stage, fraction):
            jobs.progress(job_id, stage, fraction, f'{stage}…')

        out_path = OUT_DIR / 'renders' / f'{job_id}.mp4'
        manifest = render.render(segments, end_card, out_path, aspect=aspect,
                                 music=music_path, on_progress=on_render_progress)

        jobs.progress(job_id, 'saving', 0.98, 'saving the render…', force=True)
        video_key = f'ads/{campaign_id}/renders/{job_id}.mp4'
        storage.put(manifest['out_path'], video_key)
        poster_key = f'ads/{campaign_id}/renders/{job_id}-poster.jpg'
        storage.put(manifest['poster_path'], poster_key)

        asset = storyboard.add_asset(
            workspace_id, campaign_id, storyboard_id, version_id, 'final_render', video_key,
            thumb_key=poster_key, job_id=job_id,
            metadata={'duration': manifest['duration'], 'width': manifest['width'],
                     'height': manifest['height']})

        full_manifest = {
            **{k: v for k, v in manifest.items() if k not in ('out_path', 'poster_path')},
            'version_id': str(version_id),
            'version_number': version['version']['version_number'],
            'clip_asset_ids': clip_asset_ids, 'music_asset_id': music_asset_id,
            'shot_durations': [float(s['duration']) for s in shots],
        }
        db.query(
            """INSERT INTO final_renders (campaign_id, storyboard_id, version_id,
                    aspect_ratio, resolution, status, asset_id, job_id, manifest)
               VALUES (%s, %s, %s, %s, %s, 'succeeded', %s, %s, %s)""",
            (campaign_id, storyboard_id, version_id, aspect,
             f"{manifest['width']}x{manifest['height']}", asset['id'], job_id,
             json.dumps(full_manifest)))
        db.query("UPDATE storyboards SET status = 'completed', updated_at = now() "
                 'WHERE id = %s', (storyboard_id,))

        jobs.finish(job_id, 'succeeded', settled_credits=0)
    except Exception as error:                       # noqa: BLE001 - report, don't crash
        logging.exception('orchestrator.run_render failed for job %s', job_id)
        db.query(
            """INSERT INTO final_renders (campaign_id, storyboard_id, version_id,
                    aspect_ratio, resolution, status, job_id, manifest)
               VALUES (%s, %s, %s, %s, '', 'failed', %s, %s)""",
            (campaign_id, storyboard_id, version_id, aspect, job_id,
             json.dumps({'error': str(error)})))
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


# --- the background job body ------------------------------------------------------------

def check_frame_fidelity(product_paths: list, frame_path, description: str = '',
                         max_edge: int = FIDELITY_VISION_MAX_EDGE) -> tuple[bool, str, dict]:
    """Did the generated frame keep every referenced product's real design? Mirrors
    video.py's check_fidelity: never raises (a checker outage must not fail a paid job).

    Accepts every one of the shot's product photos (not just the first) — a shot with a
    necklace AND earrings needs both checked, or a redesigned second piece ships silently.
    `max_edge` lets a caller (check_shot_clip, via CLIP_FRAME_VISION_MAX_EDGE) send finer-
    detail images than the frame-check default — this same function is reused per sampled
    clip frame, where fine jewellery detail matters more than for a single approved frame.
    Returns (ok, reason, detail); detail['pieces'] is the itemized per-piece judgement, for
    storing in the asset's metadata so the UI can show which piece failed.
    """
    if not product_paths:
        return True, 'no product reference photo to check against', {}
    try:
        import anthropic

        content = []
        for i, path in enumerate(product_paths, 1):
            label = ('the client\'s product reference photo' if len(product_paths) == 1
                     else f'client product reference photo {i} of {len(product_paths)}')
            content.append({'type': 'text', 'text': f'{label} (ground truth):'})
            content.append(_fidelity_image_block(path, max_edge))
        content.append({'type': 'text', 'text': 'The generated ad frame to inspect:'})
        content.append(_fidelity_image_block(frame_path, max_edge))
        piece_clause = f'Context: {description}.' if description else ''
        content.append({'type': 'text', 'text': (
            f'{piece_clause} List each distinct piece from the product photo(s) above, '
            'then judge each against the ad frame per the instructions.')})

        reply = anthropic.Anthropic().messages.create(
            model=FIDELITY_MODEL_ADS, max_tokens=800,
            system=FRAME_FIDELITY_SYSTEM,
            messages=[{'role': 'user', 'content': content}],
            output_config={'format': {'type': 'json_schema', 'schema': FRAME_FIDELITY_SCHEMA}},
        )
        text = next(block.text for block in reply.content if block.type == 'text')
        answer = json.loads(text)
        ok = answer['verdict'] == 'pass'
        return ok, answer['reason'], {'pieces': answer['pieces'], 'verdict': answer['verdict']}
    except Exception as error:                       # noqa: BLE001 - never fail a paid job
        logging.warning('orchestrator.check_frame_fidelity unavailable: %r', error)
        return True, f'fidelity check unavailable: {error!r}', {}


# --- cut detector: free, deterministic, no LLM -----------------------------------------
#
# A 2026-09-27 spike found a product-only clip that held the approved frame for ~1.5s then
# hard-cut to an invented scene — video.check_fidelity's schema never asked "did this stay
# one shot", so it passed. ffmpeg's own scene-change score flags candidate cuts for free;
# CUT_SCENE_THRESHOLD alone is not enough (fast in-shot motion — a hand sweeping close to
# camera — can trip the same score), so each candidate is confirmed by a pixel-diff check
# across a small window either side of it. Thresholds calibrated 2026-09-29 against two
# real clips from this project (one with a genuine hard cut at ~1.5s, one without) plus two
# synthetic ffmpeg fixtures (a hard cut and continuous fast motion) — see this change's
# report for the measured numbers.
CUT_SCENE_THRESHOLD = 0.4
CUT_EDGE_GUARD = 0.15        # ignore a "cut" within this many seconds of either end
CUT_CONFIRM_DELTA = 0.2      # compare frames this far before/after a candidate cut
CUT_CONFIRM_PIXEL_DIFF = 40  # mean abs diff on a 64x36 greyscale pair: a genuine hard cut
                             # measured 60-95 here; fast in-shot motion that also trips the
                             # scene-score threshold measured ~28; a clip with no cut at
                             # all measured <10.


def _grey_frame(mp4_path, timestamp: float) -> bytes:
    """A small greyscale raw frame at `timestamp`, downscaled to 64x36 so the pixel-diff
    confirmation in detect_cuts compares overall composition, not per-pixel noise."""
    out = subprocess.run(
        [video.FFMPEG, '-y', '-ss', f'{max(timestamp, 0.0):.3f}', '-i', str(mp4_path),
         '-frames:v', '1', '-vf', 'scale=64:36,format=gray', '-f', 'rawvideo', '-'],
        capture_output=True, check=True)
    return out.stdout


def detect_cuts(mp4_path) -> list[float]:
    """Timestamps (seconds) where this clip hard-cuts to a different scene.

    Never raises: ffmpeg missing or a malformed clip degrades to "no cuts detected"
    rather than failing a paid job on a checker outage — same policy as check_fidelity.
    """
    try:
        info = video._probe(pathlib.Path(mp4_path))
        duration = info['duration'] or 0.0
        result = subprocess.run(
            [video.FFMPEG, '-i', str(mp4_path),
             '-vf', f"select='gt(scene,{CUT_SCENE_THRESHOLD})',showinfo",
             '-f', 'null', '-'],
            capture_output=True, text=True)
        candidates = [float(m) for m in re.findall(r'pts_time:([\d.]+)', result.stderr)]
        candidates = [t for t in candidates
                     if CUT_EDGE_GUARD <= t <= duration - CUT_EDGE_GUARD]

        confirmed = []
        for t in candidates:
            before = _grey_frame(mp4_path, t - CUT_CONFIRM_DELTA)
            after = _grey_frame(mp4_path, min(t + CUT_CONFIRM_DELTA, duration))
            n = min(len(before), len(after))
            if n == 0:
                continue
            diff = sum(abs(a - b) for a, b in zip(before[:n], after[:n])) / n
            if diff > CUT_CONFIRM_PIXEL_DIFF:
                confirmed.append(round(t, 2))
        return confirmed
    except Exception as error:                       # noqa: BLE001 - degrade, don't fail
        logging.warning('orchestrator.detect_cuts unavailable: %r', error)
        return []


# --- pose/composition jump detector: free, deterministic, complements detect_cuts -------
#
# detect_cuts only catches a hard scene-boundary cut (ffmpeg's own scene-change score,
# confirmed by a pixel diff). It does NOT catch a jump WITHIN the same nominal scene — a
# body or product snapping to a different pose/framing without a scene-detected boundary.
# This samples every JUMP_SAMPLE_INTERVAL seconds and flags a step whose greyscale frame
# diff is an outlier for the CLIP'S OWN motion level (JUMP_RATIO x its median step) and
# above an absolute floor (JUMP_ABS_FLOOR), so a near-static clip's largest step never
# trips on noise alone.
#
# Calibrated 2026-09-29 against the real clips this change's report measures (see the
# report for the full numbers): abb7ce48 (no product, no jump) maxed at ~8.4, e2e's two
# ring clips (normal rotation, no jump) maxed at ~26.8 and ~14.2, and 8a87bd1a (the clip
# with wrong jewellery + a described "pose jump ~2.5s") maxed at ~17.4 — LOWER than the
# ring clips' ordinary motion. A synthetic hard-splice fixture (two different images joined
# by a 3-frame crossfade) maxed at ~94.6. There is no threshold that flags 8a87bd1a without
# also flagging the good ring clips: this detector's signal cannot separate that specific
# subtle drift from normal continuous motion. JUMP_ABS_FLOOR is set above every real clip's
# measured max (so it never false-flags a real, good clip) and still catches a genuinely
# large discontinuity like the synthetic fixture — it is a backstop for a gross jump, not
# a substitute for check_shot_clip's itemized, product-photo-anchored piece check, which is
# what actually caught 8a87bd1a's real defect (wrong jewellery throughout).
JUMP_SAMPLE_INTERVAL = 0.25
JUMP_ABS_FLOOR = 30.0
JUMP_RATIO = 3.5


def detect_pose_jump(mp4_path) -> list[float]:
    """Timestamps (seconds) where this clip's composition jumps abruptly, within what
    detect_cuts still considers one scene. Never raises: degrades to "no jump detected",
    same policy as detect_cuts."""
    try:
        info = video._probe(pathlib.Path(mp4_path))
        duration = info['duration'] or 0.0
        if duration <= JUMP_SAMPLE_INTERVAL:
            return []
        timestamps = []
        t = 0.0
        while t < duration:
            timestamps.append(t)
            t += JUMP_SAMPLE_INTERVAL
        frames = [_grey_frame(mp4_path, t) for t in timestamps]
        diffs = []
        for a, b in zip(frames, frames[1:]):
            n = min(len(a), len(b))
            diffs.append(sum(abs(x - y) for x, y in zip(a[:n], b[:n])) / n if n else 0.0)
        if not diffs:
            return []
        threshold = max(JUMP_ABS_FLOOR, JUMP_RATIO * statistics.median(diffs))
        jumps = []
        for i, diff in enumerate(diffs):
            t = timestamps[i + 1]
            if diff > threshold and CUT_EDGE_GUARD <= t <= duration - CUT_EDGE_GUARD:
                jumps.append(round(t, 2))
        return jumps
    except Exception as error:                       # noqa: BLE001 - degrade, don't fail
        logging.warning('orchestrator.detect_pose_jump unavailable: %r', error)
        return []


# --- vision check for storyboard ad clips -----------------------------------------------

CLIP_SCENE_SCHEMA = {
    'type': 'object',
    'properties': {
        'same_scene': {'type': 'boolean'},
        'person_appears': {'type': 'boolean'},
        'person_matches_frame': {'anyOf': [{'type': 'boolean'}, {'type': 'null'}]},
        'reason': {'type': 'string'},
    },
    'required': ['same_scene', 'person_appears', 'person_matches_frame', 'reason'],
    'additionalProperties': False,
}

# Fractions of clip duration sampled for the jewellery verdict — 0.5, 0.85, and the last
# frame, plus 0.25 for clips >= CLIP_LONG_ENOUGH_FOR_QUARTER_FRAME. 2026-09-29's combined
# single-call check (product photos + approved frame + all clip frames in one prompt) missed
# a6e0d95e's earring morph and 8a87bd1a's wrong-jewellery-throughout — both had been caught
# correctly by check_frame_fidelity run per-frame earlier the same day. Reusing that proven
# per-frame check against each sampled clip frame, rather than asking one combined prompt to
# reason about design fidelity AND scene continuity AND several images at once, is the fix.
CLIP_FRAME_SAMPLE_FRACTIONS = (0.5, 0.85, 1.0)
CLIP_LONG_ENOUGH_FOR_QUARTER_FRAME = 4.0


def _clip_sample_timestamps(duration: float) -> list[float]:
    fractions = list(CLIP_FRAME_SAMPLE_FRACTIONS)
    if duration >= CLIP_LONG_ENOUGH_FOR_QUARTER_FRAME:
        fractions.insert(0, 0.25)
    near_end = max(duration - 0.1, 0.0)
    return [near_end if f == 1.0 else duration * f for f in fractions]


def check_shot_clip(product_paths: list, frame_path, mp4_path, description: str = '',
                    has_person: bool = False) -> tuple[bool, str, dict]:
    """Did this storyboard shot's clip stay faithful to BOTH its approved frame (scene/
    composition ground truth) AND the product photo(s) (jewellery design ground truth)?

    Two separate judgements, not one combined prompt:
      1. Jewellery verdict: extract frames at CLIP_FRAME_SAMPLE_FRACTIONS (+25% for a long
         clip), run the PROVEN check_frame_fidelity on each concurrently (a small
         ThreadPoolExecutor — each call is itself never-raising, so a single frame's outage
         degrades to a pass for that frame rather than failing the whole clip), and fail if
         ANY sampled frame fails. This reuses exactly the itemized per-piece logic that
         correctly caught 8a87bd1a's frame and a6e0d95e's frame earlier — asking one combined
         prompt to also judge design fidelity across several images at once is what caused
         the 2026-09-29 regression on those same two clips.
      2. Scene/person judgement: a small separate call, approved frame vs. the same sampled
         clip frames, asking only same_scene/person_appears/person_matches_frame — unchanged
         in spirit from before 2026-09-29 (the fix for a product-only shot that grew an
         invented person mid-clip).

    `has_person` comes from the shot's own spec (_shot_has_person) — for a product-only
    shot, person_matches_frame is meaningless, so it is only consulted when has_person is
    True. Never raises (a checker outage must never fail a paid job). Returns (ok, reason,
    detail); detail['per_frame'] lists each sampled timestamp's (ok, reason, pieces) and
    detail['scene'] is the scene/person call's raw answer, for the asset's metadata.
    """
    try:
        info = video._probe(pathlib.Path(mp4_path))
        duration = info['duration'] or 0.0
        timestamps = _clip_sample_timestamps(duration)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = pathlib.Path(tmp_dir)
            sample_paths = []
            for i, t in enumerate(timestamps):
                p = tmp_dir / f'sample-{i}.jpg'
                video._extract_frame(mp4_path, t, p)
                sample_paths.append(p)

            # 1. jewellery verdict — check_frame_fidelity per sampled frame, concurrently.
            per_frame = []
            if product_paths:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=len(sample_paths)) as pool:
                    futures = [pool.submit(check_frame_fidelity, product_paths, str(p),
                                           description, CLIP_FRAME_VISION_MAX_EDGE)
                              for p in sample_paths]
                    per_frame = [(t, *future.result())
                                for t, future in zip(timestamps, futures)]
            else:
                per_frame = [(t, True, 'no product reference photo to check against', {})
                            for t in timestamps]

            # 2. scene/person judgement — its own small call vs. the approved frame. Its own
            # try/except: a truncated/malformed reply here (seen with a more verbose model
            # blowing past max_tokens mid-string) must not discard step 1's already-good,
            # already-computed jewellery verdict — degrade ONLY this half of the check.
            person_clause = (
                'This shot is meant to show a person; person_matches_frame: does the '
                'person in the clip frames match the one in the approved frame (same '
                'identity)? '
                if has_person else
                'This shot is meant to be PRODUCT-ONLY — no person should appear anywhere '
                'in the clip frames. Set person_matches_frame to null.'
            )
            try:
                import anthropic

                content = [{'type': 'text',
                           'text': 'The APPROVED FRAME (ground truth for scene/composition):'},
                          _fidelity_image_block(frame_path)]
                for t, p in zip(timestamps, sample_paths):
                    content.append({'type': 'text', 'text': f"the clip's frame at {t:.2f}s:"})
                    content.append(_fidelity_image_block(p))

                reply = anthropic.Anthropic().messages.create(
                    model=FIDELITY_MODEL_ADS, max_tokens=700,
                    system=(
                        'You are a strict ad QC inspector judging ONLY scene continuity '
                        'and who appears — a separate check already judges jewellery '
                        'design. same_scene: do the clip frames show the SAME setting/'
                        'composition as the approved frame — not a hard cut to a '
                        'different scene? person_appears: does a person (face, hands or '
                        'body) appear anywhere in the clip frames? When unsure, answer '
                        'the stricter (failing) value: a false pass ships a wrong ad, a '
                        'false fail only costs one retry.'
                    ),
                    messages=[{'role': 'user', 'content': content + [
                        {'type': 'text', 'text': f'{person_clause} Give a one-sentence '
                                                 'reason.'},
                    ]}],
                    output_config={'format': {'type': 'json_schema',
                                              'schema': CLIP_SCENE_SCHEMA}},
                )
                text = next(block.text for block in reply.content if block.type == 'text')
                scene = json.loads(text)
            except Exception as scene_error:          # noqa: BLE001 - degrade this half only
                logging.warning('orchestrator.check_shot_clip scene check unavailable: %r',
                               scene_error)
                scene = {'same_scene': True, 'person_appears': has_person,
                         'person_matches_frame': True if has_person else None,
                         'reason': f'scene check unavailable: {scene_error!r}'}

        detail = {
            'per_frame': [{'t': t, 'ok': ok, 'reason': reason, 'pieces': d.get('pieces', [])}
                         for t, ok, reason, d in per_frame],
            'scene': scene,
        }
        failing = next(((t, reason, d) for t, ok, reason, d in per_frame if not ok), None)
        if failing:
            t, reason, d = failing
            bad_piece = next((p for p in d.get('pieces', [])
                              if p['visible'] and not p['matches']), None)
            piece_txt = bad_piece['difference'] if bad_piece else reason
            return False, f'at {t:.2f}s: {piece_txt}', detail
        if not scene['same_scene']:
            return False, scene['reason'], detail
        if not has_person and scene['person_appears']:
            return False, scene['reason'], detail
        if has_person and scene['person_matches_frame'] is False:
            return False, scene['reason'], detail
        return True, 'looks right', detail
    except Exception as error:                       # noqa: BLE001 - never fail a paid job
        logging.warning('orchestrator.check_shot_clip unavailable: %r', error)
        return True, f'fidelity check unavailable: {error!r}', {}


def _shot_products(ctx: dict, shot: dict) -> tuple[list[str], str]:
    """This shot's product reference photo paths (every one with a local file, not just
    the first) and a combined description, for check_frame_fidelity/check_shot_clip.
    Shared by run_frame and run_video_shot."""
    product_ctxs = [ctx['products'][str(pid)] for pid in shot.get('product_ids') or []
                    if str(pid) in ctx['products']]
    paths = [p['local_path'] for p in product_ctxs if p.get('local_path')]
    description = '; '.join(p['description'] for p in product_ctxs if p.get('description'))
    return paths, description


def run_frame(job_id: str) -> None:
    """Claim, compose, generate, verify fidelity (no auto-retry — the user reviews),
    record the asset, settle. A failure here only ever touches this one job/shot: a
    version's other shots are separate jobs with separate reservations.
    """
    if not jobs.claim(job_id):
        return
    job = db.query('SELECT workspace_id, reserved_credits, params FROM jobs WHERE id = %s',
                   (job_id,), one=True)
    workspace_id = str(job['workspace_id'])
    reserved = int(job['reserved_credits'])
    params = job['params'] or {}
    shot_id, shot_key = params['shot_id'], params['shot_key']
    storyboard_id, campaign_id = params['storyboard_id'], params['campaign_id']
    version_id = params['version_id']

    try:
        jobs.progress(job_id, 'preparing references', None, 'preparing references…',
                     force=True)
        ctx = build_context(workspace_id, version_id)
        shot = next(s for s in ctx['shots'] if str(s['id']) == str(shot_id))
        provider = providers.get()
        prompt = compose_frame_prompt(ctx, shot)
        aspect, aspect_note = _pick_aspect(
            provider, ctx['storyboard'].get('aspect_ratio') or '9:16')
        reference_paths = reference_images(ctx, shot)
        image_urls = [provider.upload(path) for path in reference_paths]

        variant_guess = len(
            storyboard.list_assets(workspace_id, shot_key, 'storyboard_image')) + 1
        seed = _seed_for(shot_key, variant_guess)

        jobs.progress(job_id, 'generating image', None, 'generating the image…',
                     force=True)
        urls = provider.generate(prompt, image_urls=image_urls, aspect_ratio=aspect,
                                 quality=QUALITY, seed=seed, num_images=1)
        if not urls:
            raise RuntimeError('the provider returned no image')
        jobs.heartbeat(job_id)

        import hf
        [local_path] = hf.download(urls, OUT_DIR / job_id, prefix='frame')
        key = f'ads/{campaign_id}/{shot_key}/frame-{variant_guess}.png'
        storage.put(local_path, key)

        fidelity = None
        # Runs for every shot with a product reference, regardless of product_visibility —
        # a 'small' shot can still silently redesign the piece; visibility only affects how
        # prominent the product is in frame, not whether it must match. Checks EVERY
        # product photo the shot references, not just the first (a necklace+earrings shot
        # needs both checked).
        product_paths, description = _shot_products(ctx, shot)
        if product_paths:
            jobs.progress(job_id, 'checking the piece matches', None,
                         'checking the piece matches…', force=True)
            ok, reason, detail = check_frame_fidelity(product_paths, local_path, description)
            fidelity = {'ok': ok, 'reason': reason, 'pieces': detail.get('pieces', [])}

        jobs.progress(job_id, 'saving', 0.98, 'saving the image…', force=True)

        active_shot = storyboard.shot_in_active_version(workspace_id, shot_key, storyboard_id)
        asset_shot_id = str(active_shot['id']) if active_shot else shot_id
        asset_version_id = str(active_shot['version_id']) if active_shot else version_id

        asset = storyboard.add_asset(
            workspace_id, campaign_id, storyboard_id, asset_version_id, 'storyboard_image',
            key, shot_id=asset_shot_id, shot_key=shot_key, provider=provider.name,
            prompt=prompt,
            settings={'aspect_ratio': aspect, 'quality': QUALITY, 'seed': seed,
                     'reference_count': len(image_urls), 'aspect_note': aspect_note},
            job_id=job_id, metadata={'fidelity': fidelity} if fidelity else {})

        # complete_generation transitions + selects on EVERY shot row (any version) still
        # waiting on this exact job, not just the active version's — see its docstring.
        storyboard.complete_generation(workspace_id, storyboard_id, shot_key, 'frame_done',
                                       asset_id=asset['id'])
        credits.settle(job_id, delivered=reserved)
        jobs.finish(job_id, 'succeeded',
                   failures=[['fidelity', fidelity['reason']]] if fidelity and not fidelity['ok']
                   else [], settled_credits=reserved)
    except Exception as error:                       # noqa: BLE001 - report, don't crash
        logging.exception('orchestrator.run_frame failed for job %s', job_id)
        try:
            storyboard.complete_generation(workspace_id, storyboard_id, shot_key, 'frame_failed')
        except Exception as reset_error:              # noqa: BLE001 - best-effort state fix
            logging.warning('orchestrator.run_frame: could not mark %s as frame_failed: %r',
                            shot_key, reset_error)
        credits.settle(job_id, delivered=0)
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


def run_video_shot(job_id: str) -> None:
    """Claim, fetch the approved frame, reframe if needed, compose the motion prompt,
    generate, verify fidelity (ONE free automatic retry — same as app.run_video, the old
    single-clip flow), record the asset + a poster thumbnail, settle. Isolated per shot,
    same failure shape as run_frame.
    """
    if not jobs.claim(job_id):
        return
    job = db.query('SELECT workspace_id, reserved_credits, params FROM jobs WHERE id = %s',
                   (job_id,), one=True)
    workspace_id = str(job['workspace_id'])
    reserved = int(job['reserved_credits'])
    params = job['params'] or {}
    shot_id, shot_key = params['shot_id'], params['shot_key']
    storyboard_id, campaign_id = params['storyboard_id'], params['campaign_id']
    version_id = params['version_id']
    clip_seconds = int(params['clip_seconds'])

    try:
        ctx = build_context(workspace_id, version_id)
        shot = next(s for s in ctx['shots'] if str(s['id']) == str(shot_id))
        frame_asset_id = shot.get('selected_frame_asset_id')
        if not frame_asset_id:
            raise RuntimeError('no approved frame to animate')
        local_frame = _local_asset_path(str(frame_asset_id))
        if not local_frame:
            raise RuntimeError('the selected frame is missing from storage')

        provider = video.get()
        aspect = ctx['storyboard'].get('aspect_ratio') or '9:16'
        out_dir = OUT_DIR / 'videos' / job_id

        jobs.progress(job_id, 'uploading frame', None, 'uploading the frame…', force=True)
        # A no-op the vast majority of the time: run_frame already generates at the
        # storyboard's own aspect, so this only ever does real (paid) work for a frame
        # that got there some other way. reframe() checks needs_reframe itself.
        working_frame = video.reframe(local_frame, aspect, out_dir)

        prompt, negative = compose_motion_prompt(ctx, shot)
        product_paths, description = _shot_products(ctx, shot)

        attempt_count = 0

        # The provider hands back its own Queued/InProgress/Completed status objects
        # (fal_client and higgsfield_client each define their own — see video._hf_submit/
        # _fal_submit) — matched by class name so this stays provider-agnostic, the same
        # abstraction video.py's own VideoProvider.submit already draws. Anything else
        # (e.g. Completed, or a backend that sends nothing recognisable) still bumps the
        # heartbeat so the job never reads as stalled mid-render.
        def on_provider_progress(status):
            stage = {'Queued': 'queued at provider',
                     'InProgress': 'rendering clip'}.get(type(status).__name__)
            if stage:
                jobs.progress(job_id, stage, None, f'{stage}…')
            else:
                jobs.heartbeat(job_id)

        def one_attempt():
            nonlocal attempt_count
            attempt_count += 1
            url = video.generate(working_frame, prompt, negative, clip_seconds, provider,
                                 on_progress=on_provider_progress)
            out_dir.mkdir(parents=True, exist_ok=True)
            import hf
            mp4_path = out_dir / f'attempt-{attempt_count}.mp4'
            mp4_path.write_bytes(hf._fetch_bytes(url))
            return mp4_path

        has_person = _shot_has_person(shot)

        def check_clip(path):
            """Cut detector, then pose-jump detector, both free/no-LLM — either fails the
            clip outright without spending a vision call; only a clean clip goes on to
            check_shot_clip. Replaces video.check_fidelity here: that checker's schema
            only ever asked person questions, so it passed the 2026-09-27 spike's clip
            (a product-only shot that hard-cut to an invented woman) on jewellery design
            alone. The old single-clip flow (app.run_video) still uses
            video.check_fidelity, untouched."""
            cuts = detect_cuts(path)
            if cuts:
                return False, f'the clip cuts to a different scene at {cuts[0]:.1f}s', {}
            jumps = detect_pose_jump(path)
            if jumps:
                return False, f'the clip jumps to a different pose at {jumps[0]:.2f}s', {}
            return check_shot_clip(product_paths, working_frame, path, description,
                                   has_person)

        attempts = []
        mp4_path = one_attempt()
        jobs.progress(job_id, 'checking fidelity', None, 'checking the piece matches…',
                     force=True)
        ok, reason, detail = check_clip(mp4_path)
        attempts.append({'ok': ok, 'reason': reason, 'pieces': detail.get('pieces', [])})
        if not ok:
            jobs.progress(job_id, 'retrying — the clip changed the piece', 0.0,
                         'retrying — the clip changed the piece…', force=True)
            mp4_path = one_attempt()                            # one free retry
            jobs.progress(job_id, 'checking fidelity', None,
                         'checking the piece matches…', force=True)
            ok2, reason2, detail2 = check_clip(mp4_path)
            attempts.append({'ok': ok2, 'reason': reason2, 'pieces': detail2.get('pieces', [])})
            # The second result is kept regardless of its own verdict, same as
            # app.run_video — there is no third try.

        jobs.progress(job_id, 'saving', 0.98, 'saving the clip…', force=True)
        variant_guess = len(
            storyboard.list_assets(workspace_id, shot_key, 'video_clip')) + 1
        key = f'ads/{campaign_id}/{shot_key}/clip-{variant_guess}.mp4'
        storage.put(mp4_path, key)

        poster_path = out_dir / f'poster-{variant_guess}.jpg'
        video._extract_frame(mp4_path, 0.0, poster_path)
        thumb_key = f'ads/{campaign_id}/{shot_key}/clip-{variant_guess}-poster.jpg'
        storage.put(poster_path, thumb_key)

        probe = video._probe(mp4_path)
        final_fidelity = attempts[-1]

        active_shot = storyboard.shot_in_active_version(workspace_id, shot_key, storyboard_id)
        asset_shot_id = str(active_shot['id']) if active_shot else shot_id
        asset_version_id = str(active_shot['version_id']) if active_shot else version_id

        asset = storyboard.add_asset(
            workspace_id, campaign_id, storyboard_id, asset_version_id, 'video_clip',
            key, shot_id=asset_shot_id, shot_key=shot_key,
            provider=f'{provider.backend}/{provider.model}', prompt=prompt,
            settings={'clip_seconds': clip_seconds, 'provider': provider.backend,
                     'model': provider.model, 'trim_to': float(shot['duration'])},
            thumb_key=thumb_key, job_id=job_id,
            metadata={'fidelity': final_fidelity, 'attempts': attempts, 'probe': probe})

        # complete_generation transitions + selects on EVERY shot row (any version) still
        # waiting on this exact job, not just the active version's — see its docstring.
        storyboard.complete_generation(workspace_id, storyboard_id, shot_key, 'video_done',
                                       asset_id=asset['id'])
        credits.settle(job_id, delivered=reserved)
        jobs.finish(job_id, 'succeeded',
                   failures=[['fidelity', final_fidelity['reason']]]
                   if not final_fidelity['ok'] else [],
                   settled_credits=reserved)
    except Exception as error:                       # noqa: BLE001 - report, don't crash
        logging.exception('orchestrator.run_video_shot failed for job %s', job_id)
        try:
            storyboard.complete_generation(workspace_id, storyboard_id, shot_key, 'video_failed')
        except Exception as reset_error:              # noqa: BLE001 - best-effort state fix
            logging.warning('orchestrator.run_video_shot: could not mark %s as '
                            'video_failed: %r', shot_key, reset_error)
        credits.settle(job_id, delivered=0)
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


def demo() -> None:
    """Prompt composition + reference ordering, pure and network-free — then, only if
    DATABASE_URL is set, a real estimate() against a fixture version."""
    ctx = {
        'campaign': {'brand_style': 'warm, editorial', 'campaign_style': 'festive'},
        'storyboard': {'visual_style': 'golden hour glow', 'palette': 'amber and gold'},
        'products': {
            'p1': {'id': 'p1', 'description': 'a gold signet ring', 'category': 'ring',
                  'fidelity_instructions': 'keep the hallmark exact', 'local_path': '/tmp/p1.png'},
        },
        'characters': {
            'c1': {'id': 'c1', 'name': 'Aanya', 'description': 'a 28 year old Delhi model',
                  'face_path': '/tmp/c1.png'},
        },
        'shots': [
            {'id': 's0', 'character_ids': ['c1'], 'product_ids': ['p1'],
             'selected_frame_asset_id': None, 'state': 'frame_approved',
             'spec': {'shot_type': 'medium', 'wardrobe': 'a green silk saree'}},
            {'id': 's1', 'character_ids': ['c1'], 'product_ids': ['p1'],
             'selected_frame_asset_id': 'asset-1', 'state': 'frame_approved',
             'spec': {'shot_type': 'macro'}},
        ],
    }

    prompt0 = compose_frame_prompt(ctx, ctx['shots'][0])
    assert 'warm, editorial' in prompt0 and 'festive' in prompt0, prompt0
    assert 'golden hour glow' in prompt0 and 'amber and gold' in prompt0, prompt0
    assert 'Aanya' in prompt0 and 'Delhi model' in prompt0, prompt0
    assert 'green silk saree' in prompt0, prompt0
    assert 'a gold signet ring' in prompt0 and 'keep the hallmark exact' in prompt0, prompt0
    assert 'medium' in prompt0, prompt0
    assert 'no redesign' in prompt0, prompt0
    # style/character/product all come before the shot spec, in that order
    assert prompt0.index('warm, editorial') < prompt0.index('golden hour glow') \
        < prompt0.index('Aanya') < prompt0.index('a gold signet ring') \
        < prompt0.index('medium'), prompt0

    # a user's image_prompt override still gets the style/character/product wrapper
    overridden = {**ctx['shots'][0], 'image_prompt': 'she looks directly at camera, smiling'}
    prompt_override = compose_frame_prompt(ctx, overridden)
    assert 'she looks directly at camera' in prompt_override
    assert 'Aanya' in prompt_override and 'gold signet ring' in prompt_override

    # length cap actually caps
    long_ctx = {**ctx, 'campaign': {'brand_style': 'x ' * 3000}}
    capped = compose_frame_prompt(long_ctx, ctx['shots'][0])
    assert len(capped) <= MAX_PROMPT_CHARS + 1, len(capped)

    # reference ordering: product first, then character, no continuity for shot 0 (first
    # shot, nothing before it)
    refs0 = reference_images(ctx, ctx['shots'][0])
    assert refs0 == ['/tmp/p1.png', '/tmp/c1.png'], refs0

    # shot 1 shares character c1 with shot 0, and shot 0 has no selected_frame_asset_id ->
    # no continuity reference yet
    refs1 = reference_images(ctx, ctx['shots'][1])
    assert refs1 == ['/tmp/p1.png', '/tmp/c1.png'], refs1

    # once the earlier shot has a selected+approved frame, continuity kicks in — proved by
    # swapping which shot is "earlier" so shot 1 becomes shot 0's predecessor.
    # _local_asset_path is swapped out by hand (not unittest.mock.patch('orchestrator....'),
    # which would import a SECOND copy of this module under its real name when this file is
    # run as __main__ — patching that copy leaves the one actually running untouched).
    global _local_asset_path
    real_local_asset_path = _local_asset_path
    _local_asset_path = lambda asset_id: '/tmp/continuity.png'
    try:
        ctx_with_history = {**ctx, 'shots': [
            {**ctx['shots'][1], 'id': 's1'}, {**ctx['shots'][0], 'id': 's0'}]}
        refs_continuity = reference_images(ctx_with_history, ctx_with_history['shots'][1])
        assert refs_continuity == ['/tmp/p1.png', '/tmp/c1.png', '/tmp/continuity.png'], \
            refs_continuity

        # the cap is real: 4 references max even if more would qualify
        assert MAX_REFERENCE_IMAGES == 4
        over_ctx = {**ctx, 'products': {
            'p1': ctx['products']['p1'],
            'p2': {**ctx['products']['p1'], 'id': 'p2', 'local_path': '/tmp/p2.png'},
            'p3': {**ctx['products']['p1'], 'id': 'p3', 'local_path': '/tmp/p3.png'},
        }, 'shots': ctx_with_history['shots']}
        crowded_shot = {**ctx['shots'][0], 'product_ids': ['p1', 'p2', 'p3']}
        refs_over = reference_images(over_ctx, crowded_shot)
        assert len(refs_over) == MAX_REFERENCE_IMAGES, refs_over
    finally:
        _local_asset_path = real_local_asset_path

    print('orchestrator.compose_frame_prompt / reference_images ok')

    # --- compose_motion_prompt: pure, no LLM -----------------------------------------
    video_shot = {
        'id': 'vs0', 'motion_prompt': '', 'product_ids': ['p1'],
        'spec': {'character_action': 'she turns her wrist toward the light',
                 'camera_move': 'orbit', 'motion_intensity': 'low',
                 'environment': 'a marble courtyard', 'emotional_beat': 'quiet pride',
                 'product_interaction': 'the light catches the stone'},
    }
    motion_ctx = {**ctx, 'shots': [video_shot]}
    motion_prompt, negative = compose_motion_prompt(motion_ctx, video_shot)
    assert 'She turns her wrist toward the light' in motion_prompt, motion_prompt
    assert 'orbits' in motion_prompt, motion_prompt
    assert motion.PACE['slow'] in motion_prompt, motion_prompt      # low intensity -> slow
    assert 'marble courtyard' in motion_prompt, motion_prompt
    assert 'quiet pride' in motion_prompt, motion_prompt
    assert 'amber and gold' in motion_prompt, motion_prompt          # storyboard palette
    assert motion.FIDELITY_LOCK in motion_prompt, motion_prompt
    assert negative == motion.NEGATIVE, negative
    # a character shot keeps FIDELITY_LOCK's identity language AND gets the new
    # "one continuous shot" line appended after it (never instead of it).
    assert 'her identity does not change' in motion_prompt, motion_prompt
    assert motion_prompt.endswith('One continuous shot, no cuts.'), motion_prompt

    # a user's motion_prompt override replaces the action/camera beat, but the fidelity
    # lock and negative still land — never left to an override to drop.
    override_shot = {**video_shot, 'motion_prompt': 'a custom cinematic beat'}
    override_prompt, override_negative = compose_motion_prompt(motion_ctx, override_shot)
    assert override_prompt.startswith('a custom cinematic beat'), override_prompt
    assert motion.FIDELITY_LOCK in override_prompt, override_prompt
    assert override_negative == motion.NEGATIVE

    # an empty spec still produces something sane (the default camera-move beat), not an
    # empty/whitespace prompt — red-before-green: prove this can actually fail first, by
    # asking for a camera move that isn't in the vocabulary at all. character_ids keeps
    # these two on the character-lock path — they are testing camera_move fallback, not
    # the person/product split (covered separately below).
    bare_prompt, _ = compose_motion_prompt(
        {**ctx, 'storyboard': {}}, {'id': 'vs9', 'character_ids': ['c1'], 'spec': {}})
    assert CAMERA_MOVE_PROSE[DEFAULT_CAMERA_MOVE] in bare_prompt.lower(), bare_prompt
    assert motion.FIDELITY_LOCK in bare_prompt, bare_prompt
    junk_prompt, _ = compose_motion_prompt(
        {**ctx, 'storyboard': {}},
        {'id': 'vs8', 'character_ids': ['c1'], 'spec': {'camera_move': 'dolly-zoom'}})
    assert CAMERA_MOVE_PROSE[DEFAULT_CAMERA_MOVE] in junk_prompt.lower(), \
        'an unknown camera_move must fall back to the default, not be dropped or raise'

    print('orchestrator.compose_motion_prompt ok')

    # --- product-only shots get PRODUCT_LOCK, never FIDELITY_LOCK's person language ---
    # The exact bug this section guards against (2026-09-27 spike): a product-only macro
    # shot (no character_ids, no character_action) whose prompt still said "her whole
    # face stays in frame" — Kling obeyed by inventing a woman mid-clip.
    product_shot = {
        'id': 'vs-product', 'motion_prompt': '', 'product_ids': ['p1'],
        'spec': {'camera_move': 'static', 'motion_intensity': 'low',
                 'environment': 'a brass tray', 'emotional_beat': 'quiet luxury',
                 'product_interaction': 'the necklace catches the light'},
    }
    assert not _shot_has_person(product_shot)
    product_ctx = {**ctx, 'shots': [product_shot]}
    product_prompt, product_negative = compose_motion_prompt(product_ctx, product_shot)
    assert PRODUCT_LOCK in product_prompt, product_prompt
    assert 'No people, hands or faces appear' in product_prompt, product_prompt
    padded = f' {product_prompt.lower()} '
    for banned in (' she ', ' her '):
        assert banned not in padded, (banned, product_prompt)
    assert 'her whole face' not in product_prompt.lower(), product_prompt
    assert 'her identity' not in product_prompt.lower(), product_prompt
    assert motion.NEGATIVE in product_negative and PRODUCT_NEGATIVE_EXTRA in product_negative, \
        product_negative
    assert motion.FIDELITY_LOCK not in product_prompt, product_prompt

    # a motion_prompt override on a product-only shot still gets PRODUCT_LOCK, not
    # FIDELITY_LOCK — the override replaces only the action/camera beat.
    product_override = {**product_shot, 'motion_prompt': 'light drifts across the pave'}
    override_product_prompt, _ = compose_motion_prompt(product_ctx, product_override)
    assert override_product_prompt.startswith('light drifts across the pave')
    assert PRODUCT_LOCK in override_product_prompt, override_product_prompt

    # character_ids alone (no character_action) is also enough to select the person path.
    character_only_shot = {**product_shot, 'id': 'vs-char-only', 'character_ids': ['c1']}
    assert _shot_has_person(character_only_shot)
    character_only_prompt, character_only_negative = compose_motion_prompt(
        {**ctx, 'shots': [character_only_shot]}, character_only_shot)
    assert motion.FIDELITY_LOCK in character_only_prompt, character_only_prompt
    assert character_only_negative == motion.NEGATIVE, character_only_negative

    print('orchestrator.compose_motion_prompt (product-only PRODUCT_LOCK) ok')

    # --- compose_frame_prompt: product-only shots get a no-person clause too -----------
    frame_product_shot = {**ctx['shots'][0], 'character_ids': [], 'product_ids': ['p1'],
                          'spec': {'shot_type': 'macro',
                                   'product_interaction': 'resting on a brass tray'}}
    frame_product_prompt = compose_frame_prompt(ctx, frame_product_shot)
    assert 'No people, hands or faces appear' in frame_product_prompt, frame_product_prompt

    frame_hands_shot = {**frame_product_shot,
                        'spec': {**frame_product_shot['spec'],
                                'product_interaction': 'a hand holds the pendant'}}
    frame_hands_prompt = compose_frame_prompt(ctx, frame_hands_shot)
    assert 'Only a hand may appear' in frame_hands_prompt, frame_hands_prompt
    assert 'No people, hands or faces appear' not in frame_hands_prompt, frame_hands_prompt

    # a character shot never gets the product-only clause at all.
    assert 'No people, hands or faces appear' not in prompt0, prompt0
    print('orchestrator.compose_frame_prompt (product-only no-person clause) ok')

    # --- detect_cuts: free, deterministic, no LLM --------------------------------------
    # Two synthetic ffmpeg fixtures: one with a genuine hard cut at 1.5s (two different
    # testsrc patterns concatenated), one continuous but with fast in-shot motion (the
    # exact case CUT_CONFIRM_PIXEL_DIFF exists to NOT flag as a cut).
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = pathlib.Path(tmp_dir)
        cut_fixture = tmp_dir / 'cut.mp4'
        continuous_fixture = tmp_dir / 'continuous.mp4'
        subprocess.run(
            [video.FFMPEG, '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=1.5',
             '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25:duration=1.5',
             '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0', str(cut_fixture)],
            capture_output=True, check=True)
        subprocess.run(
            [video.FFMPEG, '-y', '-f', 'lavfi', '-i',
             'testsrc2=size=320x240:rate=25:duration=3,rotate=t*0.5:c=black,'
             "zoompan=z='1+0.1*sin(time)':d=1",
             '-t', '3', str(continuous_fixture)],
            capture_output=True, check=True)

        cut_result = detect_cuts(cut_fixture)
        assert len(cut_result) == 1 and 1.3 <= cut_result[0] <= 1.7, cut_result
        # The continuous fixture's rotate/zoom is exactly the fast in-shot motion
        # CUT_CONFIRM_PIXEL_DIFF exists to tolerate — measured against the two REAL
        # clips this change was written against, a genuine hard cut reads 60-95 mean
        # abs diff, fast in-shot motion that still crosses CUT_SCENE_THRESHOLD reads
        # ~28, and a clip with no cut at all reads <10 (see this change's report).
        continuous_result = detect_cuts(continuous_fixture)
        assert continuous_result == [], continuous_result

        # --- detect_pose_jump: free, deterministic, no LLM -----------------------------
        # Continuous motion (the same fixture as above, no jump) must not flag. A hard
        # splice between two different images, blended by a 3-frame crossfade (so it's
        # NOT a hard cut ffmpeg's scene score would catch — that's exactly the gap this
        # detector fills) must flag. Measured this change's report: this synthetic splice
        # reads ~95 mean abs diff at its blend step vs continuous motion's ~27 max step —
        # comfortably either side of JUMP_ABS_FLOOR=30. See the report for why the naive
        # magnitude signal does NOT separate the real-world 8a87bd1a clip from a normal
        # ring-rotation clip — this fixture proves the detector catches a GROSS jump, not
        # that subtler one.
        crossfade_fixture = tmp_dir / 'crossfade.mp4'
        subprocess.run(
            [video.FFMPEG, '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=1.56',
             '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25:duration=1.56',
             '-filter_complex', '[0:v][1:v]xfade=transition=fade:duration=0.12:offset=1.5',
             str(crossfade_fixture)],
            capture_output=True, check=True)
        no_jump = detect_pose_jump(continuous_fixture)
        assert no_jump == [], no_jump
        jump_result = detect_pose_jump(crossfade_fixture)
        # The 0.25s sampling grid lands the flagged step at 1.75s (the sample straddling
        # the 1.5-1.62s blend window), not exactly at the 1.5s offset — a coarser grid
        # than detect_cuts' frame-accurate ffmpeg scene score, and an accepted tradeoff
        # for a free, no-LLM detector.
        assert len(jump_result) == 1 and 1.3 <= jump_result[0] <= 1.8, jump_result

    print('orchestrator.detect_cuts / detect_pose_jump ok')

    # --- new fidelity schemas: this codebase's stricter policy (zero optional properties,
    # additionalProperties: False everywhere, no unsupported keywords) --------------------
    try:
        from director import _assert_schema_supported
    except ImportError:
        _assert_schema_supported = None
    for schema in (FRAME_FIDELITY_SCHEMA, CLIP_SCENE_SCHEMA):
        if _assert_schema_supported:
            _assert_schema_supported(schema)          # raises on any unsupported keyword

        def _assert_all_required(node):
            if isinstance(node, list):
                for sub in node:
                    _assert_all_required(sub)
                return
            if not isinstance(node, dict):
                return
            if node.get('type') == 'object':
                properties = node.get('properties') or {}
                required = set(node.get('required') or [])
                assert not (set(properties) - required), (node.get('properties'), required)
                assert node.get('additionalProperties') is False, node
                for sub in properties.values():
                    _assert_all_required(sub)
            if 'items' in node:
                _assert_all_required(node['items'])
            for key in ('anyOf', 'allOf'):
                if key in node:
                    _assert_all_required(node[key])

        _assert_all_required(schema)
    print('orchestrator fidelity schemas ok (all required, additionalProperties: False)')

    # --- _clip_seconds: smallest supported duration >= max(min(durations), ceil(shot)) -
    kling_provider = video.get('higgsfield', 'kling')        # durations 3-15
    assert _clip_seconds(2.5, kling_provider) == 3, _clip_seconds(2.5, kling_provider)
    assert _clip_seconds(3, kling_provider) == 3
    assert _clip_seconds(3.1, kling_provider) == 4
    assert _clip_seconds(15, kling_provider) == 15
    assert _clip_seconds(20, kling_provider) == 15, 'must cap at the provider maximum'
    seedance_provider = video.get('higgsfield', 'seedance')  # durations 4-15
    assert _clip_seconds(1, seedance_provider) == 4, 'must never go below the minimum'
    print('orchestrator._clip_seconds ok')

    if not os.environ.get('DATABASE_URL'):
        print('orchestrator: DATABASE_URL not set, skipping the estimate() check')
        return

    import uuid

    db.migrate()
    ws = str(db.query("INSERT INTO workspaces (name) VALUES ('orch-check') RETURNING id",
                      one=True)['id'])
    user = str(db.query(
        "INSERT INTO users (email, password_hash) VALUES (%s, 'x') RETURNING id",
        (f'orch-{uuid.uuid4().hex[:8]}@test',), one=True)['id'])
    piece = f'oc{uuid.uuid4().hex[:10]}'
    db.query("INSERT INTO pieces (id, workspace_id, user_id, category) "
             "VALUES (%s, %s, %s, 'ring')", (piece, ws, user))
    try:
        campaign = storyboard.create_campaign(ws, 'Orchestrator check')
        campaign_id = str(campaign['id'])
        storyboard.add_product(ws, campaign_id, piece)
        shots = [{'duration': 3} for _ in range(3)] + [{'duration': 2, 'kind': 'end_card'}]
        created = storyboard.create_storyboard(
            ws, campaign_id, {'target_duration': 11}, shots)
        version_id = created['version_id']

        # draft version: nothing is eligible yet (instructions not approved)
        est_draft = estimate(ws, version_id, 'frames')
        assert est_draft == {'shots': [], 'per_shot': credits.cost('ad_frame'), 'credits': 0}, \
            est_draft

        # start_frame on a draft version's shot must refuse — the storyboard isn't
        # approved yet, so there is nothing to run production against
        ordinary = next(s for s in storyboard.get_version(ws, version_id)['shots']
                        if s['kind'] == 'shot')
        try:
            start_frame(ws, str(ordinary['id']), 'k1', user)
            raise AssertionError('start_frame allowed generation against an unapproved version')
        except NotApproved:
            pass

        for s in storyboard.get_version(ws, version_id)['shots']:
            if s['kind'] == 'shot':
                storyboard.apply_event(ws, str(s['id']), 'approve_instructions')
        storyboard.approve_version(ws, version_id)

        est = estimate(ws, version_id, 'frames')
        assert est['per_shot'] == credits.cost('ad_frame') == 1, est
        assert len(est['shots']) == 3, est
        assert est['credits'] == 3, est

        # a batch confirm that doesn't match the fresh estimate is refused, and the
        # exception carries the fresh one back
        try:
            start_frames(ws, version_id, confirm_credits=999,
                        idempotency_key='batch1', user_id=user)
            raise AssertionError('start_frames accepted a stale confirm_credits')
        except EstimateMismatch as mismatch:
            assert mismatch.estimate['credits'] == 3, mismatch.estimate

        print('orchestrator.estimate ok')

        # --- videos: same lifecycle, priced by clip length --------------------------
        credits.grant(ws, 20, 'orch-video-check')

        est_videos_before = estimate(ws, version_id, 'videos')
        assert est_videos_before == {'shots': [], 'per_shot': {}, 'credits': 0}, \
            est_videos_before

        # Take one shot to frame_approved by hand — a real (fake-keyed) asset row, no
        # provider call, exactly the trick storyboard.py's own demo() uses for assets.
        video_shot_id = str(ordinary['id'])
        storyboard.apply_event(ws, video_shot_id, 'start_frame')
        storyboard.apply_event(ws, video_shot_id, 'frame_done')
        version_shots = storyboard.get_version(ws, version_id)['shots']
        video_shot_row = next(s for s in version_shots if str(s['id']) == video_shot_id)
        fake_frame = storyboard.add_asset(
            ws, campaign_id, created['storyboard_id'], version_id, 'storyboard_image',
            'fake://frame.png', shot_id=video_shot_id, shot_key=video_shot_row['shot_key'])
        storyboard.select_asset(ws, video_shot_id, fake_frame['id'])
        storyboard.apply_event(ws, video_shot_id, 'approve_frame', asset_id=fake_frame['id'])

        # _local_asset_path must not collide two different shots' same-named variant —
        # every shot's own first frame is literally "frame-1.png" under its own shot_key
        # directory, so the cache key has to carry the shot_key segment too.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = pathlib.Path(tmp_dir)
            shot_a_source = tmp_dir / 'a.png'
            shot_b_source = tmp_dir / 'b.png'
            shot_a_source.write_bytes(b'shot-a-bytes')
            shot_b_source.write_bytes(b'shot-b-bytes')
            key_a = f'ads/collision-check/{uuid.uuid4()}/frame-1.png'
            key_b = f'ads/collision-check/{uuid.uuid4()}/frame-1.png'
            storage.put(shot_a_source, key_a)
            storage.put(shot_b_source, key_b)
            asset_a = storyboard.add_asset(ws, campaign_id, created['storyboard_id'],
                                           version_id, 'storyboard_image', key_a,
                                           shot_id=video_shot_id,
                                           shot_key=video_shot_row['shot_key'])
            asset_b = storyboard.add_asset(ws, campaign_id, created['storyboard_id'],
                                           version_id, 'storyboard_image', key_b,
                                           shot_id=video_shot_id,
                                           shot_key=video_shot_row['shot_key'])
            path_a = pathlib.Path(_local_asset_path(asset_a['id']))
            path_b = pathlib.Path(_local_asset_path(asset_b['id']))
            assert path_a != path_b, 'two assets with the same basename collided on one cache file'
            assert path_a.read_bytes() == b'shot-a-bytes', path_a.read_bytes()
            assert path_b.read_bytes() == b'shot-b-bytes', path_b.read_bytes()
        print('orchestrator._local_asset_path (no basename collisions) ok')

        kling_provider = video.get('higgsfield', 'kling')
        expected_clip_seconds = _clip_seconds(3.0, kling_provider)   # this fixture's shots are 3s
        assert expected_clip_seconds == 3, expected_clip_seconds

        est_videos = estimate(ws, version_id, 'videos')
        assert est_videos['shots'] == [video_shot_id], est_videos
        assert est_videos['per_shot'][video_shot_id] == video.credits_for(3, kling_provider), \
            est_videos
        assert est_videos['credits'] == video.credits_for(3, kling_provider), est_videos

        # a batch confirm that doesn't match the fresh videos estimate is refused too
        try:
            start_videos(ws, version_id, confirm_credits=999,
                        idempotency_key='vbatch1', user_id=user)
            raise AssertionError('start_videos accepted a stale confirm_credits')
        except EstimateMismatch as mismatch:
            assert mismatch.estimate['credits'] == video.credits_for(3, kling_provider), \
                mismatch.estimate

        # start_video refuses a shot that never reached frame_approved at all — the
        # common case, and an IllegalTransition (the shot_state table has no start_video
        # edge from ready_for_frame), not the "no frame selected" ValueError below.
        other_shot_id = str(next(s for s in version_shots
                                 if s['kind'] == 'shot' and str(s['id']) != video_shot_id)['id'])
        try:
            start_video(ws, other_shot_id, 'novid', user)
            raise AssertionError('start_video generated a clip from ready_for_frame')
        except shot_state.IllegalTransition:
            pass

        # start_video also refuses the rarer case: the STATE says frame_approved (a legal
        # approve_frame can fire with no asset_id and nothing pre-selected — the state
        # machine alone does not guarantee a selection), but nothing was ever selected.
        storyboard.apply_event(ws, other_shot_id, 'start_frame')
        storyboard.apply_event(ws, other_shot_id, 'frame_done')
        storyboard.apply_event(ws, other_shot_id, 'approve_frame')   # no asset_id at all
        try:
            start_video(ws, other_shot_id, 'novid2', user)
            raise AssertionError('start_video generated a clip with no selected frame')
        except ValueError as error:
            assert 'frame' in str(error), error

        # the real thing: reserves credits, creates an ad_video job, moves the shot to
        # video_generating — never calls run_video_shot, the one place that would spend
        # real provider money (the same split run_frame's own coverage above draws).
        balance_before = credits.balance(ws)
        video_job_id = start_video(ws, video_shot_id, 'v1', user)
        video_job_row = db.query('SELECT kind, reserved_credits FROM jobs WHERE id = %s',
                                 (video_job_id,), one=True)
        assert video_job_row['kind'] == 'ad_video', video_job_row
        assert int(video_job_row['reserved_credits']) == video.credits_for(3, kling_provider), \
            video_job_row
        assert credits.balance(ws) == balance_before - video.credits_for(3, kling_provider), \
            credits.balance(ws)
        moved = next(s for s in storyboard.get_version(ws, version_id)['shots']
                    if str(s['id']) == video_shot_id)
        assert moved['state'] == 'video_generating', moved['state']

        print('orchestrator.estimate/start_video (videos) ok')
    finally:
        # Order matters: approvals.asset_id and credit_ledger.job_id/final_renders.job_id
        # all RESTRICT deleting the row they reference, so approvals goes before
        # generated_assets, and credit_ledger before jobs — wrapped in its own try/finally
        # so a future ordering mistake still closes the pool instead of leaking its worker
        # threads on exit ("couldn't stop thread 'pool-1-worker-0'").
        try:
            db.query('DELETE FROM approvals WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM generated_assets WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM jobs WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM campaigns WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM pieces WHERE workspace_id = %s', (ws,))
            db.query('DELETE FROM workspaces WHERE id = %s', (ws,))
            db.query('DELETE FROM users WHERE id = %s', (user,))
        finally:
            db.close()


if __name__ == '__main__':
    demo()
