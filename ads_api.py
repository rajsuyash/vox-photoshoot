"""The storyboard-driven video ad editor's HTTP surface.

An `APIRouter` rather than more of app.py (already 1745 lines before this). Every route
sits behind `ads_enabled`: this feature is site-admin-only until Phase 5 removes the
gate (`ADS_PUBLIC=1` opens it for everyone, e.g. for a demo environment).

This file is the ONLY place that turns storyboard.py's domain calls into HTTP — it knows
about workspaces, sessions, jobs and idempotency; storyboard.py knows about none of that.
Two background jobs live here (ad_concepts, ad_board) that call director.py and then
persist through storyboard.py, exactly like run_video in app.py does for video.run —
same claim/finish shape, plus jobs.progress() heartbeats through director.py's streaming
callback so the "Directing…"/"Storyboarding…" buttons are not just a spinner (see
director.generate_concepts/generate_storyboard's on_progress). Both are priced at 0
credits (house LLM cost, per the plan), so neither reserves nor settles anything —
jobs.create/claim/finish still runs, for the fencing and page-refresh recovery, not for
the money.

    .venv/bin/python -c "import ads_api"     # import-only sanity check
"""

import hashlib
import os
import pathlib
import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, Response

import auth
import credits
import db
import director
import endcard
import jobs
import orchestrator
import pieces
import render
import shoot
import shot_state
import storage
import storyboard
import talent
import video

router = APIRouter(prefix='/api')

# Labelled "typical", never promised — see jobs.progress()/static/app.js's
# progressComponent, which shows these next to real elapsed time, not instead of it.
TYPICAL_SECONDS = {'ad_concepts': 30, 'ad_board': 75, 'ad_frame': 35, 'ad_video': 180,
                  'ad_music': 60, 'ad_render': 45}

ENDCARD_CACHE_DIR = pathlib.Path('out/ads/endcard_cache')


def ads_enabled(session: dict = Depends(auth.current_session)) -> dict:
    """Gate the whole router. Phase 5 removes this once frames/video/render ship."""
    if not (session.get('is_admin') or os.environ.get('ADS_PUBLIC') == '1'):
        raise HTTPException(403, 'video ads are not available on this account yet')
    return session


def _not_found(error: storyboard.NotFound):
    return HTTPException(404, str(error) or 'not found')


# --- frames: enrich GET /versions/{id} with variants/selection/running-job per shot -----

def _frame_variant(asset: dict) -> dict:
    metadata = asset.get('metadata') or {}
    return {'id': str(asset['id']), 'variant': asset['variant'],
            'url': storage.presign(asset['key']), 'fidelity': metadata.get('fidelity'),
            'created_at': asset['created_at']}


def _video_variant(asset: dict) -> dict:
    metadata = asset.get('metadata') or {}
    settings = asset.get('settings') or {}
    return {'id': str(asset['id']), 'variant': asset['variant'],
            'url': storage.presign(asset['key']),
            'poster_url': storage.presign(asset['thumb_key']) if asset.get('thumb_key') else None,
            'fidelity': metadata.get('fidelity'), 'clip_seconds': settings.get('clip_seconds'),
            'created_at': asset['created_at']}


def _music_variant(asset: dict) -> dict:
    metadata = asset.get('metadata') or {}
    return {'id': str(asset['id']), 'variant': asset['variant'],
            'url': storage.presign(asset['key']), 'duration': metadata.get('duration'),
            'created_at': asset['created_at']}


def _render_summary(row: dict) -> dict:
    manifest = row.get('manifest') or {}
    return {
        'id': str(row['id']), 'status': row['status'], 'created_at': row['created_at'],
        'aspect_ratio': row['aspect_ratio'], 'resolution': row['resolution'],
        'url': storage.presign(row['asset_key']) if row.get('asset_key') else None,
        'poster_url': storage.presign(row['thumb_key']) if row.get('thumb_key') else None,
        'duration': manifest.get('duration'), 'version_number': manifest.get('version_number'),
    }


def _jobs_by_shot(version_id: str, kind: str) -> dict:
    out = {}
    for job in db.query(
            """SELECT id, status, storyboard_shot_id, error, params, started_at,
                      heartbeat_at FROM jobs
                WHERE storyboard_version_id = %s AND kind = %s
             ORDER BY created_at""", (version_id, kind)):
        out[str(job['storyboard_shot_id'])] = job          # last one wins
    return out


def _job_progress_summary(job: dict | None, kind: str) -> dict | None:
    """The running-job shape shot cards/the inspector need — progress + started_at +
    typical_seconds, per the honest-progress-indicator brief. None once it's no longer
    queued/running: `frame_error`/`video_error` (below) carry a FAILED job's message."""
    if job is None or job['status'] not in ('queued', 'running'):
        return None
    params = job['params'] or {}
    return {'id': str(job['id']), 'status': job['status'], 'progress': params.get('progress'),
            'started_at': job['started_at'].isoformat() if job.get('started_at') else None,
            'heartbeat_at': job['heartbeat_at'].isoformat() if job.get('heartbeat_at') else None,
            'typical_seconds': TYPICAL_SECONDS.get(kind)}


def _enrich_shots_with_frames(workspace_id: str, version_id: str, shots: list[dict]) -> None:
    """Mutates each ordinary shot in place: every frame/video variant, each one's
    selected url, its running ad_frame/ad_video job (if any) and the last job's error (if
    the last one failed). One query per job kind, one list_assets call per shot per type
    (a version rarely has more than ~10 shots, so this stays well clear of an N+1 that
    would actually matter).
    """
    frame_jobs_by_shot = _jobs_by_shot(version_id, 'ad_frame')
    video_jobs_by_shot = _jobs_by_shot(version_id, 'ad_video')

    for shot in shots:
        if shot['kind'] != 'shot':
            continue
        variants = [_frame_variant(a) for a in
                    storyboard.list_assets(workspace_id, shot['shot_key'], 'storyboard_image')]
        shot['frame_variants'] = variants
        selected = shot.get('selected_frame_asset_id')
        shot['selected_frame_url'] = next(
            (v['url'] for v in variants if v['id'] == str(selected)), None) if selected else None
        job = frame_jobs_by_shot.get(str(shot['id']))
        shot['frame_job'] = _job_progress_summary(job, 'ad_frame')
        shot['frame_error'] = job['error'] if job and job['status'] == 'failed' else None

        video_variants = [_video_variant(a) for a in
                          storyboard.list_assets(workspace_id, shot['shot_key'], 'video_clip')]
        shot['video_variants'] = video_variants
        selected_video = shot.get('selected_video_asset_id')
        shot['selected_video_url'] = next(
            (v['url'] for v in video_variants if v['id'] == str(selected_video)), None) \
            if selected_video else None
        video_job = video_jobs_by_shot.get(str(shot['id']))
        shot['video_job'] = _job_progress_summary(video_job, 'ad_video')
        shot['video_error'] = (video_job['error']
                               if video_job and video_job['status'] == 'failed' else None)


# --- director inputs: the same product/character shape for the API and the two jobs ---

def _products_for_director(campaign_id: str) -> list[dict]:
    """`sku` and `description` are passed through SEPARATELY — a manufacturer catalogue
    piece has both (see LEARNINGS.md: SKU is the identifier, description is what it
    actually looks like), and collapsing them with `or` silently dropped whichever one
    lost. `name` stays as a display-only convenience for callers that just want a single
    label (falls back through sku -> description -> category)."""
    rows = db.query(
        """SELECT cp.id, cp.fidelity_instructions, p.category, p.description, p.sku
             FROM campaign_products cp JOIN pieces p ON p.id = cp.piece_id
            WHERE cp.campaign_id = %s ORDER BY cp.id""", (campaign_id,))
    return [
        {'id': str(r['id']), 'category': r['category'],
         'sku': (r['sku'] or '').strip(), 'description': (r['description'] or '').strip(),
         'name': (r['sku'] or r['description'] or r['category'] or '').strip(),
         'fidelity_instructions': r['fidelity_instructions']}
        for r in rows]


def _characters_for_director(campaign_id: str, workspace_id: str) -> list[dict]:
    rows = db.query('SELECT * FROM campaign_characters WHERE campaign_id = %s ORDER BY id',
                    (campaign_id,))
    cast_entries = shoot.load_cast()
    out = []
    for r in rows:
        if r['talent_id']:
            owned = talent.owned(r['talent_id'], workspace_id)
            description = owned['description'] if owned else ''
        elif r['cast_key']:
            description = cast_entries.get(r['cast_key'], {}).get('description', '')
        else:
            description = (r['appearance'] or {}).get('description', '')
        # House cast.json entries can carry a literal, unfilled '{EXPRESSION}' token (see
        # orchestrator._clean_description's docstring) -- reused here rather than
        # duplicated, so a director-facing prompt never leaks the placeholder either.
        out.append({'id': str(r['id']), 'name': r['name'],
                    'description': orchestrator._clean_description(description)})
    return out


# --- background jobs: generate, then persist through storyboard.py --------------------

def run_ad_concepts(job_id: str, workspace_id: str, campaign_id: str) -> None:
    if not jobs.claim(job_id):
        return
    on_progress = lambda stage, fraction, message: jobs.progress(  # noqa: E731
        job_id, stage, fraction, message)
    try:
        campaign = storyboard.get_campaign(workspace_id, campaign_id)
        if campaign is None:
            raise ValueError('campaign not found')
        brief = campaign.get('brief') or {}
        products = _products_for_director(campaign_id)
        characters = _characters_for_director(campaign_id, workspace_id)
        concepts = director.generate_concepts(brief, products, characters, on_progress)
        on_progress('saving', 0.98, 'saving the concepts…')
        storyboard.save_concepts(workspace_id, campaign_id, concepts)
        jobs.finish(job_id, 'succeeded', settled_credits=0)
    except Exception as error:              # noqa: BLE001 - report, don't crash the worker
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


def run_ad_board(job_id: str, workspace_id: str, campaign_id: str, concept_id: str,
                 aspect: str, duration: float, platform: str) -> None:
    if not jobs.claim(job_id):
        return
    on_progress = lambda stage, fraction, message: jobs.progress(  # noqa: E731
        job_id, stage, fraction, message)
    try:
        campaign = storyboard.get_campaign(workspace_id, campaign_id)
        if campaign is None:
            raise ValueError('campaign not found')
        base_brief = campaign.get('brief') or {}
        concept_row = db.query(
            'SELECT * FROM concepts WHERE id = %s AND campaign_id = %s',
            (concept_id, campaign_id), one=True)
        if concept_row is None:
            raise ValueError('concept not found')
        concept = concept_row          # concepts.mode is a real column (migration 012)
        products = _products_for_director(campaign_id)
        characters = _characters_for_director(campaign_id, workspace_id)
        brief = {**base_brief, 'aspect': aspect, 'duration': duration, 'platform': platform}

        board, warnings = director.generate_storyboard(brief, concept, products,
                                                        characters, on_progress)
        on_progress('saving', 0.98, 'saving the storyboard…')

        fields = {
            'concept_id': concept_id, 'title': board['title'], 'aspect_ratio': aspect,
            'target_duration': duration, 'platform': platform,
            'visual_style': board['visual_style'], 'emotional_arc': board['emotional_arc'],
            'music_direction': board['music_direction'], 'palette': board['palette'],
            'warnings': warnings,
        }
        created = storyboard.create_storyboard(workspace_id, campaign_id, fields,
                                               board['shots'])
        jobs.update_params(job_id, {'storyboard_id': created['storyboard_id'],
                                    'version_id': created['version_id']})
        jobs.finish(job_id, 'succeeded', settled_credits=0)
    except director.DirectorError as error:
        jobs.finish(job_id, 'failed', error='; '.join(error.errors), settled_credits=0)
    except Exception as error:              # noqa: BLE001 - report, don't crash the worker
        jobs.finish(job_id, 'failed', error=str(error), settled_credits=0)


# --- campaigns --------------------------------------------------------------------------

@router.post('/campaigns')
def create_campaign(name: str = Form(...), brand: str = Form(''), goal: str = Form(''),
                    audience: str = Form(''), platform: str = Form(''),
                    duration: float = Form(25), aspect: str = Form('9:16'),
                    mood: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    if not name.strip():
        raise HTTPException(400, 'the campaign needs a name')
    brief = {'brand': brand.strip(), 'goal': goal.strip(), 'audience': audience.strip(),
             'platform': platform.strip(), 'duration': duration, 'aspect': aspect,
             'mood': mood.strip()}
    return storyboard.create_campaign(workspace_id, name.strip(), brief=brief)


@router.get('/campaigns')
def list_campaigns(session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    return storyboard.list_campaigns(workspace_id)


@router.get('/campaigns/{campaign_id}')
def get_campaign_detail(campaign_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    campaign = storyboard.get_campaign(workspace_id, campaign_id)
    if campaign is None:
        raise HTTPException(404, 'no such campaign')

    products = db.query(
        """SELECT cp.id, cp.piece_id, cp.fidelity_instructions, p.category,
                  p.description, p.sku, p.s3_key
             FROM campaign_products cp JOIN pieces p ON p.id = cp.piece_id
            WHERE cp.campaign_id = %s ORDER BY cp.id""", (campaign_id,))
    characters = db.query(
        'SELECT * FROM campaign_characters WHERE campaign_id = %s ORDER BY id',
        (campaign_id,))
    concepts = db.query(
        'SELECT * FROM concepts WHERE campaign_id = %s ORDER BY created_at',
        (campaign_id,))
    storyboards = db.query(
        'SELECT * FROM storyboards WHERE campaign_id = %s ORDER BY created_at DESC',
        (campaign_id,))
    running_jobs = db.query(
        """SELECT id, kind, status, params, started_at, heartbeat_at, created_at FROM jobs
            WHERE workspace_id = %s AND kind IN ('ad_concepts', 'ad_board')
              AND params->>'campaign_id' = %s AND status IN ('queued', 'running')
         ORDER BY created_at""", (workspace_id, campaign_id))

    return {
        'campaign': campaign,
        'products': [
            {**p, 'id': str(p['id']), 's3_key': None,   # not the client's business
             'name': (p['sku'] or p['description'] or p['category'] or '').strip(),
             'image': storage.presign(p['s3_key']) if p['s3_key'] else None}
            for p in products],
        'characters': characters,
        'concepts': concepts,          # concepts.mode is a real column (migration 012)
        'storyboards': storyboards,
        # Lets the page recover a "Directing…"/"Storyboarding…" progress component on
        # reload — state comes from the server, never from something the tab remembered.
        'running_jobs': [
            {'id': str(j['id']), 'kind': j['kind'], 'status': j['status'],
             'concept_id': (j['params'] or {}).get('concept_id'),
             'progress': (j['params'] or {}).get('progress'),
             'started_at': j['started_at'].isoformat() if j.get('started_at') else None,
             'heartbeat_at': j['heartbeat_at'].isoformat() if j.get('heartbeat_at') else None,
             'typical_seconds': TYPICAL_SECONDS.get(j['kind'])}
            for j in running_jobs],
    }


@router.post('/campaigns/{campaign_id}/products')
def add_product(campaign_id: str, piece_id: str = Form(...),
                fidelity_instructions: str = Form(''),
                session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    if pieces.owned(piece_id, workspace_id) is None:
        raise HTTPException(404, 'that product is not in your library')
    try:
        return storyboard.add_product(workspace_id, campaign_id, piece_id,
                                      fidelity_instructions.strip())
    except storyboard.NotFound as error:
        raise _not_found(error)


@router.delete('/campaigns/{campaign_id}/products/{product_id}')
def remove_product(campaign_id: str, product_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.remove_product(workspace_id, campaign_id, product_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except storyboard.Conflict as error:
        raise HTTPException(409, str(error))
    return {'product_id': product_id}


@router.post('/campaigns/{campaign_id}/characters')
def add_character(campaign_id: str, name: str = Form(...), role: str = Form(''),
                  talent_id: str = Form(''), cast_key: str = Form(''),
                  description: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    talent_id = talent_id.strip() or None
    cast_key = cast_key.strip() or None
    if talent_id and cast_key:
        raise HTTPException(400, 'pick a talent model or a house cast key, not both')
    if talent_id and talent.owned(talent_id, workspace_id) is None:
        raise HTTPException(404, 'no such model in your workspace')
    if cast_key and cast_key not in shoot.load_cast():
        raise HTTPException(400, f'unknown cast key {cast_key!r}')

    appearance = {} if (talent_id or cast_key) else {'description': description.strip()}
    try:
        return storyboard.add_character(workspace_id, campaign_id, name.strip() or 'Untitled',
                                        role.strip(), appearance, talent_id, cast_key)
    except storyboard.NotFound as error:
        raise _not_found(error)


@router.delete('/campaigns/{campaign_id}/characters/{character_id}')
def remove_character(campaign_id: str, character_id: str,
                     session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.remove_character(workspace_id, campaign_id, character_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except storyboard.Conflict as error:
        raise HTTPException(409, str(error))
    return {'character_id': character_id}


@router.post('/campaigns/{campaign_id}/concepts')
def create_concepts_job(campaign_id: str, background: BackgroundTasks,
                        idempotency_key: str = Form(''),
                        session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    if storyboard.get_campaign(workspace_id, campaign_id) is None:
        raise HTTPException(404, 'no such campaign')
    if not _products_for_director(campaign_id):
        raise HTTPException(422, 'add at least one product before generating concepts')

    key = idempotency_key or f'ad-concepts:{uuid.uuid4()}'
    job = jobs.create(workspace_id, session['user_id'], 'ad_concepts', key,
                      {'campaign_id': campaign_id})
    if job['created']:
        background.add_task(run_ad_concepts, str(job['id']), workspace_id, campaign_id)
    return {'job_id': str(job['id']), 'status': 'running'}


@router.post('/campaigns/{campaign_id}/concepts/{concept_id}/choose')
def choose_concept(campaign_id: str, concept_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        return {'concept_id': storyboard.choose_concept(workspace_id, campaign_id,
                                                         concept_id)}
    except storyboard.NotFound as error:
        raise _not_found(error)


@router.post('/campaigns/{campaign_id}/storyboards')
def create_storyboard_job(campaign_id: str, background: BackgroundTasks,
                          concept_id: str = Form(...), aspect: str = Form('9:16'),
                          duration: float = Form(25), platform: str = Form(''),
                          idempotency_key: str = Form(''),
                          session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    if storyboard.get_campaign(workspace_id, campaign_id) is None:
        raise HTTPException(404, 'no such campaign')
    if db.query('SELECT id FROM concepts WHERE id = %s AND campaign_id = %s',
               (concept_id, campaign_id), one=True) is None:
        raise HTTPException(404, 'no such concept')

    key = idempotency_key or f'ad-board:{uuid.uuid4()}'
    job = jobs.create(workspace_id, session['user_id'], 'ad_board', key,
                      {'campaign_id': campaign_id, 'concept_id': concept_id})
    if job['created']:
        background.add_task(run_ad_board, str(job['id']), workspace_id, campaign_id,
                            concept_id, aspect, duration, platform)
    return {'job_id': str(job['id']), 'status': 'running'}


@router.get('/jobs/{job_id}')
def get_job(job_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    job = jobs.get(job_id, workspace_id)
    if job is None:
        raise HTTPException(404, 'no such job')
    params = job['params'] or {}
    return {'job_id': job_id, 'kind': job['kind'], 'status': job['status'],
            'error': job['error'], 'storyboard_id': params.get('storyboard_id'),
            'version_id': params.get('version_id'), 'shot_id': params.get('shot_id'),
            'progress': params.get('progress'),
            'started_at': job['started_at'].isoformat() if job.get('started_at') else None,
            'heartbeat_at': job['heartbeat_at'].isoformat() if job.get('heartbeat_at') else None,
            'typical_seconds': TYPICAL_SECONDS.get(job['kind'])}


# --- versions -----------------------------------------------------------------------

@router.get('/versions/{version_id}')
def get_version_detail(version_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        # Self-heal before reading: a shot stuck in frame_generating/video_generating
        # with no job actually able to complete it any more (see
        # storyboard.reconcile_generating) must not sit that way forever just because
        # nobody happened to load this version while its job was still alive.
        storyboard.reconcile_generating(workspace_id, version_id)
        state = storyboard.get_version(workspace_id, version_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    versions = db.query(
        'SELECT id, version_number, status, created_at, approved_at '
        'FROM storyboard_versions WHERE storyboard_id = %s ORDER BY version_number',
        (state['storyboard']['id'],))
    _enrich_shots_with_frames(workspace_id, version_id, state['shots'])
    ordinary_shots = [s for s in state['shots'] if s['kind'] == 'shot']
    ready_for_render = bool(ordinary_shots) and all(
        s['state'] == 'video_approved' for s in ordinary_shots)

    music_info = storyboard.music_state(workspace_id, version_id)
    music_running_job = db.query(
        """SELECT id, status, params, started_at, heartbeat_at FROM jobs
            WHERE storyboard_version_id = %s AND kind = 'ad_music'
              AND status IN ('queued', 'running')
         ORDER BY created_at DESC LIMIT 1""", (version_id,), one=True)
    # The most recent ad_music job regardless of status — music_running_job above only
    # ever carries queued/running, so a FAILED job (the user clicked Generate music,
    # nothing came back) was previously invisible: the page just showed no music, with
    # no explanation. Kept separate from the running-job summary rather than widening
    # _job_progress_summary's own filter, since every OTHER caller of that helper still
    # wants "only while it's actually in flight".
    music_last_job = db.query(
        """SELECT id, status, error FROM jobs
            WHERE storyboard_version_id = %s AND kind = 'ad_music'
         ORDER BY created_at DESC LIMIT 1""", (version_id,), one=True)
    try:
        music_estimate = orchestrator.estimate(workspace_id, version_id, 'music')
    except Exception:                          # noqa: BLE001 - never break the page over pricing
        music_estimate = None

    render_problems = orchestrator.validate_for_render(workspace_id, version_id)
    render_rows = db.query(
        """SELECT fr.id, fr.status, fr.created_at, fr.aspect_ratio, fr.resolution,
                  fr.manifest, ga.key AS asset_key, ga.thumb_key
             FROM final_renders fr LEFT JOIN generated_assets ga ON ga.id = fr.asset_id
            WHERE fr.version_id = %s ORDER BY fr.created_at DESC LIMIT 10""", (version_id,))
    render_running_job = db.query(
        """SELECT id, status, params, started_at, heartbeat_at FROM jobs
            WHERE storyboard_version_id = %s AND kind = 'ad_render'
              AND status IN ('queued', 'running')
         ORDER BY created_at DESC LIMIT 1""", (version_id,), one=True)

    return {**state, 'versions': versions,
            'active_jobs': storyboard.active_jobs(workspace_id, version_id),
            'frame_estimate': orchestrator.estimate(workspace_id, version_id, 'frames'),
            'video_estimate': orchestrator.estimate(workspace_id, version_id, 'videos'),
            'ready_for_render': ready_for_render,
            'warnings': state['version'].get('warnings') or [],
            'music': {
                'variants': [_music_variant(a) for a in music_info['variants']],
                'selected_id': music_info['selected_id'], 'approved': music_info['approved'],
                'skipped': music_info['skipped'], 'estimate': music_estimate,
                'job': _job_progress_summary(music_running_job, 'ad_music'),
                'last_job': ({'status': music_last_job['status'],
                             'error': music_last_job['error']}
                            if music_last_job else None),
            },
            'render_ready': {'ready': not render_problems, 'problems': render_problems},
            'renders': [_render_summary(r) for r in render_rows],
            'render_job': _job_progress_summary(render_running_job, 'ad_render')}


@router.post('/versions/{version_id}/approve')
def approve_version(version_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        return {'version_id': storyboard.approve_version(workspace_id, version_id)}
    except storyboard.NotFound as error:
        raise _not_found(error)


@router.post('/versions/{version_id}/fork')
def fork_version(version_id: str, session: dict = Depends(ads_enabled)):
    """Force an editable copy. A no-op (same id back) if it's already a draft — there is
    nothing to fork FROM when the version being viewed is the editable one."""
    workspace_id = auth.current_workspace(session)
    try:
        new_id = storyboard.ensure_editable(workspace_id, version_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    return {'version_id': new_id, 'forked': new_id != version_id}


@router.post('/versions/{version_id}/shots')
def add_shot(version_id: str, payload: dict = Body(...),
            session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    shot_fields = {
        'kind': payload.get('kind', 'shot'), 'duration': payload.get('duration', 2),
        'spec': payload.get('spec') or {}, 'character_ids': payload.get('character_ids', []),
        'product_ids': payload.get('product_ids', []),
    }
    try:
        new_version_id, shot_id = storyboard.add_shot(
            workspace_id, version_id, int(payload.get('after_position', -1)), shot_fields)
    except storyboard.NotFound as error:
        raise _not_found(error)
    return {'version_id': new_version_id, 'shot_id': shot_id}


@router.post('/versions/{version_id}/reorder')
def reorder_shots(version_id: str, shot_ids: list[str] = Body(...),
                  session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        new_version_id = storyboard.reorder(workspace_id, version_id, shot_ids)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': new_version_id}


# --- shots ----------------------------------------------------------------------------

@router.patch('/shots/{shot_id}')
def patch_shot(shot_id: str, patch: dict = Body(...), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        version_id, effective_shot_id, state = storyboard.update_spec(
            workspace_id, shot_id, patch)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id, 'shot_id': effective_shot_id, 'state': state}


@router.post('/shots/{shot_id}/end-card-style')
def set_end_card_style(shot_id: str, style: str = Form(...),
                       session: dict = Depends(ads_enabled)):
    """Which end-card look renders: heritage/modern/minimal. A PRODUCTION-level choice
    (routed through storyboard.set_end_card_style, never storyboard.update_spec) — picking
    a different look must not fork an approved version or demote its state, the same way
    choosing a different music track doesn't. See storyboard.set_end_card_style's
    docstring for why update_spec would have forked here even though brand_text/tagline
    (the 'none' invalidation group end_card_style also sits in) never advance/demote state."""
    workspace_id = auth.current_workspace(session)
    try:
        version_id = storyboard.set_end_card_style(workspace_id, shot_id, style)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id, 'style': style}


@router.post('/shots/{shot_id}/end-card-keep-case')
def set_end_card_keep_case(shot_id: str, keep_case: bool = Form(...),
                           session: dict = Depends(ads_enabled)):
    """Whether heritage/modern keep the brand text's typed capitalisation instead of
    displaying it uppercase. Same non-forking shape as end-card-style — see
    storyboard.set_end_card_keep_case."""
    workspace_id = auth.current_workspace(session)
    try:
        version_id = storyboard.set_end_card_keep_case(workspace_id, shot_id, keep_case)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id, 'keep_case': keep_case}


def _endcard_source_frame(workspace_id: str, end_card_shot: dict):
    """(source_asset_id, local_png_path) for the frame the real render will actually use
    — the LAST frame of the last ordinary shot's selected clip if one exists, else that
    shot's selected frame image, else None. Mirrors orchestrator.run_render's own
    end_card_shot/ordinary-shots resolution so the preview never shows a different frame
    than the one the render will freeze."""
    version = storyboard.get_version(workspace_id, str(end_card_shot['version_id']))
    ordinary = sorted((s for s in version['shots'] if s['kind'] == 'shot'),
                      key=lambda s: s['position'])
    if not ordinary:
        return None
    last = ordinary[-1]
    if last.get('selected_video_asset_id'):
        asset_id = str(last['selected_video_asset_id'])
        local_clip = orchestrator._local_asset_path(asset_id)
        if not local_clip:
            return None
        cache_dir = ENDCARD_CACHE_DIR / 'sources'
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame_path = cache_dir / f'{asset_id}-last-frame.png'
        if not frame_path.exists():
            probe = video._probe(pathlib.Path(local_clip))
            # render._truncate_ms, not a bare `- 1/fps`: -ss landing AT or a hair past the
            # clip's true last decodable frame makes ffmpeg write nothing at all (exit 0,
            # zero bytes) -- exactly the failure render.py's own end-card extraction
            # already had to guard against (see its docstring). UNLIKE render.py's own
            # segments (always re-encoded to a known fps by _normalize before this kind
            # of seek), this reads the asset's ORIGINAL clip directly -- a real provider
            # clip measured 24fps here, not render.py's usual 30, so the margin must come
            # from the clip's own probed fps, not a hardcoded one.
            fps = probe.get('fps') or 24
            near_end = render._truncate_ms(max((probe['duration'] or 0) - 1 / fps, 0))
            # Extract to a per-request temp name, then atomically rename into place --
            # three concurrent preview requests (one per style, exactly what the UI's
            # Style row fires on first paint) all race this same shared frame_path; two
            # ffmpeg processes writing the SAME destination path concurrently produced a
            # truncated, unreadable PNG the very first time this ran against a browser.
            tmp_path = cache_dir / f'{asset_id}-last-frame-{uuid.uuid4().hex}.png'
            video._extract_frame(local_clip, near_end, tmp_path)
            os.replace(tmp_path, frame_path)
        return asset_id, frame_path
    if last.get('selected_frame_asset_id'):
        asset_id = str(last['selected_frame_asset_id'])
        local_frame = orchestrator._local_asset_path(asset_id)
        if not local_frame:
            return None
        return asset_id, pathlib.Path(local_frame)
    return None


def _endcard_cache_path(source_asset_id: str, style: str, brand: str, tagline: str,
                        keep_case: bool) -> pathlib.Path:
    digest = hashlib.sha256(
        f'{source_asset_id}|{style}|{brand}|{tagline}|{keep_case}'.encode()).hexdigest()
    return ENDCARD_CACHE_DIR / f'{digest[:32]}.png'


@router.get('/shots/{shot_id}/endcard-preview')
def endcard_preview(shot_id: str, style: str = endcard.DEFAULT_STYLE, brand_text: str = '',
                    tagline: str = '', keep_case: bool | None = None,
                    session: dict = Depends(ads_enabled)):
    """A PNG preview of `style` on the frame the final render will actually freeze on.
    Workspace-scoped; cached on disk keyed by (source asset, style, brand, tagline,
    keep_case) so repeated views (switching styles back and forth, or the debounced
    live-typing refresh) are instant. brand_text/tagline/keep_case default to the
    end_card shot's own spec — the query params exist only so the UI can preview text/
    the checkbox the user hasn't saved yet."""
    workspace_id = auth.current_workspace(session)
    if style not in endcard.STYLES:
        raise HTTPException(400, f'unknown style {style!r}; use one of {endcard.STYLES}')
    try:
        shot = storyboard.get_shot(workspace_id, shot_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    if shot['kind'] != 'end_card':
        raise HTTPException(400, 'endcard-preview only applies to the end_card shot')

    source = _endcard_source_frame(workspace_id, shot)
    if source is None:
        raise HTTPException(404, 'previews appear once the last shot has a frame or clip')
    asset_id, frame_path = source

    spec = shot.get('spec') or {}
    brand = brand_text or (spec.get('brand_text') or '').strip()
    tag = tagline or (spec.get('tagline') or '').strip()
    kc = bool(spec.get('end_card_keep_case')) if keep_case is None else keep_case

    cache_path = _endcard_cache_path(asset_id, style, brand, tag, kc)
    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        logo_path = orchestrator._workspace_logo_path(workspace_id)
        tmp_path = cache_path.with_name(f'{cache_path.stem}-{uuid.uuid4().hex}.png')
        endcard.preview(frame_path, style, brand, tag, tmp_path, logo_path=logo_path,
                        keep_case=kc)
        os.replace(tmp_path, cache_path)
    return Response(cache_path.read_bytes(), media_type='image/png')


@router.post('/shots/{shot_id}/duplicate')
def duplicate_shot(shot_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        version_id, new_shot_id = storyboard.duplicate_shot(workspace_id, shot_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    return {'version_id': version_id, 'shot_id': new_shot_id}


@router.post('/shots/{shot_id}/split')
def split_shot(shot_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        version_id, shot_a, shot_b = storyboard.split_shot(workspace_id, shot_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    return {'version_id': version_id, 'shot_ids': [shot_a, shot_b]}


@router.post('/shots/{shot_id}/delete')
def delete_shot(shot_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        return {'version_id': storyboard.delete_shot(workspace_id, shot_id)}
    except storyboard.NotFound as error:
        raise _not_found(error)


@router.post('/shots/{shot_id}/approve-instructions')
def approve_instructions(shot_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        version_id, effective_shot_id, state = storyboard.apply_event(
            workspace_id, shot_id, 'approve_instructions')
    except storyboard.NotFound as error:
        raise _not_found(error)
    except (ValueError, shot_state.IllegalTransition) as error:
        raise HTTPException(409, str(error))
    return {'version_id': version_id, 'shot_id': effective_shot_id, 'state': state}


# --- frames ------------------------------------------------------------------------------

@router.post('/shots/{shot_id}/frames')
def generate_frame(shot_id: str, background: BackgroundTasks,
                   idempotency_key: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-frame:{uuid.uuid4()}'
    try:
        job_id = orchestrator.start_frame(workspace_id, shot_id, key, session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except shot_state.IllegalTransition as error:
        raise HTTPException(409, str(error))
    except credits.Insufficient as error:
        raise HTTPException(402, f'not enough credits — {error}')
    except ValueError as error:
        raise HTTPException(422, str(error))
    background.add_task(orchestrator.run_frame, job_id)
    return {'job_id': job_id, 'status': 'running'}


@router.get('/versions/{version_id}/estimate')
def get_frame_estimate(version_id: str, stage: str = 'frames',
                       session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        return orchestrator.estimate(workspace_id, version_id, stage)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.post('/versions/{version_id}/frames')
def generate_frames_batch(version_id: str, background: BackgroundTasks,
                          confirm_credits: int = Form(...), idempotency_key: str = Form(''),
                          session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-frames-batch:{uuid.uuid4()}'
    try:
        job_ids = orchestrator.start_frames(workspace_id, version_id, confirm_credits, key,
                                            session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.EstimateMismatch as mismatch:
        raise HTTPException(409, f'the price changed to {mismatch.estimate["credits"]} '
                                 'credits — refresh the estimate and confirm again')
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except credits.Insufficient as error:
        raise HTTPException(402, f'not enough credits — {error}')
    for job_id in job_ids:
        background.add_task(orchestrator.run_frame, job_id)
    return {'job_ids': job_ids, 'status': 'running'}


@router.post('/shots/{shot_id}/select')
def select_frame(shot_id: str, asset_id: str = Form(...), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.select_asset(workspace_id, shot_id, asset_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'shot_id': shot_id}


@router.post('/shots/{shot_id}/approve-frame')
def approve_frame(shot_id: str, asset_id: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        if asset_id:
            storyboard.select_asset(workspace_id, shot_id, asset_id)
        current = db.query(
            """SELECT sh.selected_frame_asset_id FROM storyboard_shots sh
                 JOIN storyboard_versions sv ON sv.id = sh.version_id
                 JOIN storyboards sb ON sb.id = sv.storyboard_id
                 JOIN campaigns c ON c.id = sb.campaign_id
                WHERE sh.id = %s AND c.workspace_id = %s""", (shot_id, workspace_id), one=True)
        approved_asset_id = asset_id or (
            str(current['selected_frame_asset_id'])
            if current and current['selected_frame_asset_id'] else None)
        version_id, effective_shot_id, state = storyboard.apply_event(
            workspace_id, shot_id, 'approve_frame', asset_id=approved_asset_id,
            user_id=session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except (ValueError, shot_state.IllegalTransition) as error:
        raise HTTPException(409, str(error))
    return {'version_id': version_id, 'shot_id': effective_shot_id, 'state': state}


# --- videos ------------------------------------------------------------------------------
# GET /versions/{id}?stage=videos already works — get_frame_estimate above takes `stage`
# generically. /shots/{id}/select already handles a video_clip asset_id too —
# storyboard.select_asset branches on the asset's own `type`, not on the caller's route.

@router.post('/shots/{shot_id}/videos')
def generate_video(shot_id: str, background: BackgroundTasks,
                   idempotency_key: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-video:{uuid.uuid4()}'
    try:
        job_id = orchestrator.start_video(workspace_id, shot_id, key, session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except shot_state.IllegalTransition as error:
        raise HTTPException(409, str(error))
    except credits.Insufficient as error:
        raise HTTPException(402, f'not enough credits — {error}')
    except ValueError as error:
        raise HTTPException(422, str(error))
    background.add_task(orchestrator.run_video_shot, job_id)
    return {'job_id': job_id, 'status': 'running'}


@router.post('/versions/{version_id}/videos')
def generate_videos_batch(version_id: str, background: BackgroundTasks,
                          confirm_credits: int = Form(...), idempotency_key: str = Form(''),
                          session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-videos-batch:{uuid.uuid4()}'
    try:
        job_ids = orchestrator.start_videos(workspace_id, version_id, confirm_credits, key,
                                            session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.EstimateMismatch as mismatch:
        raise HTTPException(409, f'the price changed to {mismatch.estimate["credits"]} '
                                 'credits — refresh the estimate and confirm again')
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except credits.Insufficient as error:
        raise HTTPException(402, f'not enough credits — {error}')
    for job_id in job_ids:
        background.add_task(orchestrator.run_video_shot, job_id)
    return {'job_ids': job_ids, 'status': 'running'}


@router.post('/shots/{shot_id}/approve-video')
def approve_video(shot_id: str, asset_id: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        if asset_id:
            storyboard.select_asset(workspace_id, shot_id, asset_id)
        current = db.query(
            """SELECT sh.selected_video_asset_id FROM storyboard_shots sh
                 JOIN storyboard_versions sv ON sv.id = sh.version_id
                 JOIN storyboards sb ON sb.id = sv.storyboard_id
                 JOIN campaigns c ON c.id = sb.campaign_id
                WHERE sh.id = %s AND c.workspace_id = %s""", (shot_id, workspace_id), one=True)
        approved_asset_id = asset_id or (
            str(current['selected_video_asset_id'])
            if current and current['selected_video_asset_id'] else None)
        version_id, effective_shot_id, state = storyboard.apply_event(
            workspace_id, shot_id, 'approve_video', asset_id=approved_asset_id,
            user_id=session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except (ValueError, shot_state.IllegalTransition) as error:
        raise HTTPException(409, str(error))
    return {'version_id': version_id, 'shot_id': effective_shot_id, 'state': state}


# --- music -------------------------------------------------------------------------------

@router.post('/versions/{version_id}/music')
def generate_music(version_id: str, background: BackgroundTasks,
                   confirm_credits: int = Form(...), idempotency_key: str = Form(''),
                   session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-music:{uuid.uuid4()}'
    try:
        job_id = orchestrator.start_music(workspace_id, version_id, confirm_credits, key,
                                          session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.EstimateMismatch as mismatch:
        raise HTTPException(409, f'the price changed to {mismatch.estimate["credits"]} '
                                 'credits — refresh the estimate and confirm again')
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except credits.Insufficient as error:
        raise HTTPException(402, f'not enough credits — {error}')
    background.add_task(orchestrator.run_music, job_id)
    return {'job_id': job_id, 'status': 'running'}


@router.post('/versions/{version_id}/music/select')
def select_music(version_id: str, asset_id: str = Form(...),
                 session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.select_music(workspace_id, version_id, asset_id)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id}


@router.post('/versions/{version_id}/music/approve')
def approve_music(version_id: str, asset_id: str = Form(''),
                  session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.approve_music(workspace_id, version_id, asset_id or None)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id}


@router.post('/versions/{version_id}/music/skip')
def skip_music(version_id: str, skipped: bool = Form(True),
              session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    try:
        storyboard.set_music_skipped(workspace_id, version_id, skipped)
    except storyboard.NotFound as error:
        raise _not_found(error)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return {'version_id': version_id}


# --- final render --------------------------------------------------------------------------

@router.post('/versions/{version_id}/render')
def render_version(version_id: str, background: BackgroundTasks,
                   idempotency_key: str = Form(''), session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    key = idempotency_key or f'ad-render:{uuid.uuid4()}'
    try:
        job_id = orchestrator.start_render(workspace_id, version_id, key, session['user_id'])
    except storyboard.NotFound as error:
        raise _not_found(error)
    except orchestrator.NotApproved as error:
        raise HTTPException(409, str(error))
    except orchestrator.NotRenderable as error:
        # A plain string, not {'problems': [...]} — static/app.js's api() helper reads
        # `detail` straight into an Error's message, so a dict here would stringify to
        # the useless "[object Object]" for anyone who somehow reaches this (the client
        # disables the button until GET .../versions/{id}'s own render_ready.ready is
        # true, so this is a race-condition backstop, not the normal path).
        raise HTTPException(422, '; '.join(error.problems))
    background.add_task(orchestrator.run_render, job_id)
    return {'job_id': job_id, 'status': 'running'}


@router.get('/renders/{render_id}')
def get_render(render_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    row = db.query(
        """SELECT fr.*, ga.key AS asset_key, ga.thumb_key
             FROM final_renders fr JOIN campaigns c ON c.id = fr.campaign_id
             LEFT JOIN generated_assets ga ON ga.id = fr.asset_id
            WHERE fr.id = %s AND c.workspace_id = %s""", (render_id, workspace_id), one=True)
    if row is None:
        raise HTTPException(404, 'no such render')
    return {**_render_summary(row), 'manifest': row.get('manifest') or {}}


@router.get('/renders/{render_id}/download')
def download_render(render_id: str, session: dict = Depends(ads_enabled)):
    workspace_id = auth.current_workspace(session)
    row = db.query(
        """SELECT fr.*, ga.key AS asset_key, c.name AS campaign_name
             FROM final_renders fr JOIN campaigns c ON c.id = fr.campaign_id
             LEFT JOIN generated_assets ga ON ga.id = fr.asset_id
            WHERE fr.id = %s AND c.workspace_id = %s""", (render_id, workspace_id), one=True)
    if row is None or not row.get('asset_key'):
        raise HTTPException(404, 'no such render')
    manifest = row.get('manifest') or {}
    filename = f"{row['campaign_name']}-v{manifest.get('version_number', '')}.mp4"
    return RedirectResponse(storage.presign(row['asset_key'], filename), status_code=307)
