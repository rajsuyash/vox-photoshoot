"""End-to-end check of the storyboard ad editor, against a real database and a real
FastAPI app, with only director.py's two Anthropic calls stubbed.

Template: video_flow_test.py. director.generate_concepts/generate_storyboard are
monkeypatched exactly the way video.run/check_fidelity are there — module-level
attribute assignment, which works because ads_api.py calls them by attribute lookup
(`director.generate_concepts(...)`) rather than binding a local reference at import time.

    .venv/bin/python ads_flow_test.py     # needs DATABASE_URL pointed at a THROWAWAY db
"""

import os
import pathlib
import subprocess
import sys
import time
import uuid

os.environ.setdefault('DATABASE_URL',
                      'postgresql://postgres:pg@localhost:55432/donna?sslmode=disable')
os.environ.pop('S3_BUCKET', None)          # local storage, never S3, for this check
os.environ.pop('ADS_PUBLIC', None)         # the gate must be admin-only unless set

from fastapi.testclient import TestClient

import admin
import app as app_module
import auth
import credits
import db
import director
import hf
import jobs
import music
import orchestrator
import product as product_module   # `product` is already a local var name inside main()
import providers
import render
import storage
import storyboard
import video


FIXTURE_MP4 = pathlib.Path('out/ads_test_fixtures/fixture.mp4')
FIXTURE_FRAME = pathlib.Path('out/ads_test_fixtures/frame-fixture.jpg')
CUT_FIXTURE_MP4 = pathlib.Path('out/ads_test_fixtures/cut-fixture.mp4')


def ensure_fixture_frame() -> None:
    """A real, tiny, 9:16 JPEG — run_video_shot calls video.reframe(), which opens the
    frame with PIL to check its aspect (video.needs_reframe), so this has to be real
    image bytes, unlike the frames section's own text-content 'fixture', which is only
    ever read as opaque bytes by a stubbed provider/fidelity check."""
    if FIXTURE_FRAME.exists():
        return
    from PIL import Image

    FIXTURE_FRAME.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (576, 1024), 'red').save(FIXTURE_FRAME)   # 9:16, matches the board


def ensure_fixture_mp4() -> None:
    """A real, tiny, valid mp4 — ffprobe/ffmpeg (video._probe/_extract_frame) need an
    actual container, not garbage bytes, so the fixture is real media, not a stub."""
    if FIXTURE_MP4.exists():
        return
    FIXTURE_MP4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=64x64:rate=5',
         '-pix_fmt', 'yuv420p', str(FIXTURE_MP4)],
        capture_output=True, check=True)


def ensure_cut_fixture_mp4() -> None:
    """A real mp4 with a genuine hard cut at 1.5s — two different testsrc patterns
    concatenated — for exercising orchestrator.detect_cuts (unstubbed, real ffmpeg) end
    to end through run_video_shot, the same shape as the real clip this whole change
    was written against (a product-only shot's clip that hard-cut to an invented
    person)."""
    if CUT_FIXTURE_MP4.exists():
        return
    CUT_FIXTURE_MP4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=1.5',
         '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25:duration=1.5',
         '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0', '-pix_fmt', 'yuv420p',
         str(CUT_FIXTURE_MP4)],
        capture_output=True, check=True)


class FakeVideoProvider:
    """Stands in for video.get() — no real Higgsfield/fal call. Same duration range AND
    price as the real HF Kling 3.0 pro provider (3-15s), so both _clip_seconds' rounding
    and credits_for's pricing are exercised for real rather than against toy numbers."""
    backend = 'fake'
    model = 'kling'
    durations = frozenset(range(3, 16))
    usd_per_second = video.KLING_USD_PER_SECOND


class FakeFrameProvider:
    """Stands in for providers.get() — no real fal/Higgsfield call. generate() is a
    plain attribute (not a bound method) so a test can swap it mid-run to fail once,
    the way a real provider outage would."""
    name = 'fake'
    aspect_ratios = frozenset({'9:16', '4:5', '1:1', '16:9'})

    def upload(self, path):
        return f'fake://{path}'

    def generate(self, prompt, image_urls=None, aspect_ratio='9:16', quality='high',
                seed=None, num_images=1):
        return ['fake://frame.png']

    def nearest_aspect(self, width, height):
        return '1:1'


def fake_hf_download(urls, directory, prefix='frame'):
    """hf.download without the network fetch — writes the same fixture bytes urls
    would have resolved to, so storage.put() has a real local file to read."""
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, _url in enumerate(urls):
        path = directory / f'{prefix}-{index}.png'
        path.write_bytes(b'fake-png-bytes')
        paths.append(path)
    return paths


def fake_music_generate(version, out_path, provider=None, on_progress=None, seed=None):
    """Stands in for music.generate() — no real fal call. A real 20s sine tone mp3, so
    render.render() (called FOR REAL in the music+render test section below) has an
    actual audio file to mux, not garbage bytes."""
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=20',
                    '-ar', '44100', str(out_path)], capture_output=True, check=True)
    arguments = music.build_arguments(version, seed=seed)
    return {'path': out_path, 'duration': 20.0, 'provider': 'fal/elevenlabs-music-2.5',
           'model': 'elevenlabs/music/v2.5', 'arguments': arguments}


def fake_generate_concepts(brief, products, characters, on_progress=None):
    if on_progress:
        on_progress('thinking', None, 'thinking it through…')
        on_progress('writing', 0.5, 'writing 3 concepts…')
    return [
        {'name': f'Concept {i}', 'core_idea': 'idea', 'emotional_hook': 'hook',
         'visual_world': 'world', 'story_arc': 'arc', 'tagline': f'Tag {i}',
         'mode': 'showcase' if i == 2 else 'story'}
        for i in range(3)
    ]


def _shot(duration, shot_type, visibility, character_id, product_id, camera_move='static',
         transition_out='cut'):
    return {'kind': 'shot', 'duration': duration,
            'character_ids': [character_id] if character_id else [],
            'product_ids': [product_id] if product_id and visibility != 'none' else [],
            'spec': {'shot_type': shot_type, 'product_visibility': visibility,
                     'camera_move': camera_move, 'transition_out': transition_out}}


def fake_generate_storyboard(brief, concept, products, characters, on_progress=None):
    """A fixed, valid 24s 'story'-mode board — the same shape director.py's own demo()
    proves against validate_storyboard, just built here from the caller's real ids.
    No warnings: matches director.generate_storyboard's (board, warnings) return shape."""
    if on_progress:
        on_progress('thinking', None, 'thinking it through…')
        on_progress('writing', 0.5, 'writing the storyboard…')
        on_progress('checking rules', 0.95, 'checking the storyboard against the rules…')
    pid = products[0]['id'] if products else None
    cid = characters[0]['id'] if characters else None
    shots = [
        _shot(3, 'medium', 'small', cid, pid),
        _shot(2, 'macro', 'hero', cid, pid),
        _shot(2.5, 'close', 'small', cid, pid),
        _shot(2, 'medium', 'medium', cid, pid),
        _shot(3, 'wide', 'none', cid, pid),
        _shot(3, 'close', 'small', cid, pid, camera_move='crane_rise'),
        _shot(5, 'medium', 'small', cid, pid, transition_out='dissolve'),
        {'kind': 'end_card', 'duration': 3.5, 'character_ids': [], 'product_ids': [],
         'spec': {'brand_text': 'ACME', 'tagline': 'Forever'}},
    ]
    board = {'title': 'Test board', 'visual_style': 'warm', 'palette': 'gold and amber',
            'emotional_arc': 'longing to joy', 'music_direction': 'soft strings',
            'shots': shots}
    return board, []


def fake_generate_storyboard_with_warnings(brief, concept, products, characters,
                                           on_progress=None):
    """Same board as fake_generate_storyboard, but with a SOFT rule-9 warning still
    open — the real bug this covers: a hard-clean board that is merely off-style must be
    ACCEPTED (never DirectorError), with the warning persisted on the version."""
    board, _ = fake_generate_storyboard(brief, concept, products, characters, on_progress)
    warning = {'rule': 9, 'severity': 'soft',
              'message': "[9] the product-visible share matches the concept's mode: "
                         "the product is visible 50% of the runtime, 'story' mode "
                         "expects 15%-35%"}
    return board, [warning]


def fake_generate_storyboard_invalid(brief, concept, products, characters, on_progress=None):
    """The DirectorError path: the model's board still broke the rules after director.py's
    own one retry. run_ad_board must catch this and fail the job, not the process."""
    raise director.DirectorError(['[3] each ordinary shot is 1-6s: shot 0 is 40s'])


director.generate_concepts = fake_generate_concepts
director.generate_storyboard = fake_generate_storyboard


def _await_job(client, job_id: str, timeout_s: float = 10.0) -> dict:
    """TestClient runs BackgroundTasks synchronously before returning the response (see
    video_flow_test.py), so this is normally already terminal on the first read."""
    deadline = time.monotonic() + timeout_s
    detail = client.get(f'/api/jobs/{job_id}').json()
    while detail['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(0.2)
        detail = client.get(f'/api/jobs/{job_id}').json()
    return detail


def _positions(version: dict) -> list[int]:
    return [s['position'] for s in version['shots']]


def main() -> None:
    db.migrate()
    client = TestClient(app_module.app)
    made_workspaces, made_users = [], []

    try:
        # --- provision: an admin workspace, and a second (foreign) workspace -----------
        ws = admin.create_workspace(f'ads-test-{uuid.uuid4().hex[:8]}')
        ws_id = str(ws['id'])
        made_workspaces.append(ws_id)
        admin_account = admin.create_account(f'ads-{uuid.uuid4().hex[:8]}@test', ws_id,
                                             'owner', is_admin=True)
        made_users.append(str(admin_account['id']))
        admin_token = auth.start_session(str(admin_account['id']), ws_id)
        client.cookies.set(auth.COOKIE, admin_token)

        member_account = admin.create_account(f'member-{uuid.uuid4().hex[:8]}@test', ws_id,
                                              'member', is_admin=False)
        made_users.append(str(member_account['id']))
        member_token = auth.start_session(str(member_account['id']), ws_id)
        member_client = TestClient(app_module.app)
        member_client.cookies.set(auth.COOKIE, member_token)

        other_ws = admin.create_workspace(f'ads-test-other-{uuid.uuid4().hex[:8]}')
        other_ws_id = str(other_ws['id'])
        made_workspaces.append(other_ws_id)
        other_account = admin.create_account(f'ads-other-{uuid.uuid4().hex[:8]}@test',
                                             other_ws_id, 'owner', is_admin=True)
        made_users.append(str(other_account['id']))
        other_token = auth.start_session(str(other_account['id']), other_ws_id)
        other_client = TestClient(app_module.app)
        other_client.cookies.set(auth.COOKIE, other_token)

        # --- a piece fixture, in the ADMIN workspace ------------------------------------
        piece_id = f'adspiece{uuid.uuid4().hex[:6]}'
        db.query("INSERT INTO pieces (id, workspace_id, user_id, s3_key, category, "
                 "description) VALUES (%s, %s, %s, 'uploads/fake.jpg', 'ring', %s)",
                 (piece_id, ws_id, str(admin_account['id']), 'a gold signet ring'))

        # --- 1) non-admin gets 403 -------------------------------------------------------
        resp = member_client.post('/api/campaigns', data={'name': 'nope'})
        assert resp.status_code == 403, resp.text

        # --- 2) create campaign -----------------------------------------------------------
        campaign = client.post('/api/campaigns', data={
            'name': 'Diwali 2026', 'brand': 'Aurum', 'goal': 'awareness',
            'audience': 'young professionals', 'platform': 'instagram', 'duration': '24',
            'aspect': '9:16', 'mood': 'warm'}).json()
        campaign_id = str(campaign['id'])
        assert campaign['brief']['brand'] == 'Aurum', campaign

        # a campaign with no products yet cannot start a concepts job — the client
        # disables the button on this, but the API must refuse it too
        no_product_concepts = client.post(f'/api/campaigns/{campaign_id}/concepts',
                                          data={'idempotency_key': f'nc-{uuid.uuid4()}'})
        assert no_product_concepts.status_code == 422, no_product_concepts.text

        # --- 3) add a product + a described character -------------------------------------
        product = client.post(f'/api/campaigns/{campaign_id}/products', data={
            'piece_id': piece_id, 'fidelity_instructions': 'keep the hallmark exact'}).json()
        product_row_id = str(product['id'])

        character = client.post(f'/api/campaigns/{campaign_id}/characters', data={
            'name': 'Aanya', 'role': 'lead', 'description': 'a 28 year old Delhi model'
        }).json()
        assert character['name'] == 'Aanya', character

        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['products']) == 1 and detail['products'][0]['id'] == product_row_id
        assert len(detail['characters']) == 1

        # a bad piece_id (not in this workspace) is refused, not silently attached
        bad_product = client.post(f'/api/campaigns/{campaign_id}/products',
                                  data={'piece_id': 'not-a-real-piece'})
        assert bad_product.status_code == 404, bad_product.text

        # --- 3b) casting/product dedupe -> one row, not two -------------------------------
        # posting the SAME piece_id again must return the existing campaign_products row,
        # never a second chip for the same product
        dup_product = client.post(f'/api/campaigns/{campaign_id}/products',
                                  data={'piece_id': piece_id}).json()
        assert dup_product['id'] == product_row_id, dup_product
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['products']) == 1, detail['products']

        # posting the same house cast_key twice must return the existing character, not
        # add "Meera, Meera" as two chips (the reported bug)
        cast_first = client.post(f'/api/campaigns/{campaign_id}/characters',
                                 data={'name': 'Meera', 'cast_key': 'meera'}).json()
        cast_second = client.post(f'/api/campaigns/{campaign_id}/characters',
                                  data={'name': 'Meera', 'cast_key': 'meera'}).json()
        assert cast_second['id'] == cast_first['id'], (cast_first, cast_second)
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['characters']) == 2, detail['characters']   # Aanya + Meera

        # foreign workspace cannot remove this campaign's character or product
        cross_ws_del = other_client.delete(
            f'/api/campaigns/{campaign_id}/characters/{cast_first["id"]}')
        assert cross_ws_del.status_code == 404, cross_ws_del.text

        # deleting an unreferenced character removes it
        del_cast = client.delete(f'/api/campaigns/{campaign_id}/characters/{cast_first["id"]}')
        assert del_cast.status_code == 200, del_cast.text
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['characters']) == 1, detail['characters']
        assert all(c['id'] != cast_first['id'] for c in detail['characters'])

        # a second, unrelated piece + product, added only to prove delete removes it
        piece_id_2 = f'adspiece{uuid.uuid4().hex[:6]}'
        db.query("INSERT INTO pieces (id, workspace_id, user_id, s3_key, category, "
                 "description) VALUES (%s, %s, %s, 'uploads/fake2.jpg', 'ring', %s)",
                 (piece_id_2, ws_id, str(admin_account['id']), 'a silver band'))
        product_2 = client.post(f'/api/campaigns/{campaign_id}/products',
                                data={'piece_id': piece_id_2}).json()
        del_product = client.delete(
            f'/api/campaigns/{campaign_id}/products/{product_2["id"]}')
        assert del_product.status_code == 200, del_product.text
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['products']) == 1, detail['products']

        print('ads_flow: cast/product dedupe + remove (unreferenced) ok')

        # --- 3c) upload flow: POST /api/pieces (product.identify monkeypatched, no
        #     network) straight into the campaign — the path ads.html's setup-screen
        #     drop zone exercises end to end, instead of the SQL-inserted piece fixture
        #     the rest of this file uses. -------------------------------------------------
        upload_dir = pathlib.Path('out/ads_test_fixtures')
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path_a = upload_dir / 'upload-a.jpg'
        upload_path_a.write_bytes(b'fake-jpeg-bytes-a')
        upload_path_b = upload_dir / 'upload-b.jpg'
        upload_path_b.write_bytes(b'fake-jpeg-bytes-b')

        real_identify = product_module.identify
        product_module.identify = lambda path: product_module.Piece(
            product_module.CATEGORIES['ring'], 'a gold signet ring', detected=True)
        try:
            with upload_path_a.open('rb') as handle:
                upload_a = client.post(
                    '/api/pieces', files={'upload': ('upload-a.jpg', handle, 'image/jpeg')})
            assert upload_a.status_code == 200, upload_a.text
            piece_a = upload_a.json()
            assert piece_a['detected'] is True, piece_a

            add_a = client.post(f'/api/campaigns/{campaign_id}/products', data={
                'piece_id': piece_a['piece_id'],
                'fidelity_instructions': 'keep the hallmark exact'})
            assert add_a.status_code == 200, add_a.text
            add_a_id = add_a.json()['id']

            detail = client.get(f'/api/campaigns/{campaign_id}').json()
            uploaded = next(p for p in detail['products']
                            if p['piece_id'] == piece_a['piece_id'])
            assert uploaded['image'], \
                'the campaign payload is missing the uploaded piece\'s image url'

            # a second upload of a DIFFERENT file -> 2 products. Unlike the same-piece-
            # twice dedupe case above, two different photographs are two pieces, not one
            # re-selected — see pieces.create's own docstring on this.
            with upload_path_b.open('rb') as handle:
                upload_b = client.post(
                    '/api/pieces', files={'upload': ('upload-b.jpg', handle, 'image/jpeg')})
            assert upload_b.status_code == 200, upload_b.text
            piece_b = upload_b.json()
            assert piece_b['piece_id'] != piece_a['piece_id']

            add_b = client.post(f'/api/campaigns/{campaign_id}/products',
                                data={'piece_id': piece_b['piece_id']})
            assert add_b.status_code == 200, add_b.text
            add_b_id = add_b.json()['id']

            detail = client.get(f'/api/campaigns/{campaign_id}').json()
            uploaded_ids = {piece_a['piece_id'], piece_b['piece_id']}
            matched = [p for p in detail['products'] if p['piece_id'] in uploaded_ids]
            assert len(matched) == 2, detail['products']
            assert all(p['image'] for p in matched), matched

            # Remove both again: this section only proves the upload -> campaign pipeline
            # and its image url, not a permanent addition — the rest of this file's
            # product-count assertions (e.g. section 6b) assume the single SQL-inserted
            # fixture piece is the only product left in the campaign.
            client.delete(f'/api/campaigns/{campaign_id}/products/{add_a_id}')
            client.delete(f'/api/campaigns/{campaign_id}/products/{add_b_id}')
            detail = client.get(f'/api/campaigns/{campaign_id}').json()
            assert len(detail['products']) == 1, detail['products']
        finally:
            product_module.identify = real_identify

        print('ads_flow: upload -> add to campaign -> image url in campaign payload ok')

        # --- 4) concepts job -> poll -> exactly 3, with modes attached --------------------
        concepts_key = f'concepts-{uuid.uuid4()}'
        concepts_job = client.post(f'/api/campaigns/{campaign_id}/concepts',
                                   data={'idempotency_key': concepts_key}).json()
        concepts_detail = _await_job(client, concepts_job['job_id'])
        assert concepts_detail['status'] == 'succeeded', concepts_detail

        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['concepts']) == 3, detail['concepts']
        modes = sorted(c['mode'] for c in detail['concepts'])
        assert modes == ['showcase', 'story', 'story'], modes

        # a repeated idempotency key is one job, not a second (duplicate) generation —
        # TestClient has already run the first job's BackgroundTask to completion by now
        # (see _await_job's docstring), so this proves jobs.create's own dedup, not luck.
        concepts_job_again = client.post(f'/api/campaigns/{campaign_id}/concepts',
                                         data={'idempotency_key': concepts_key}).json()
        assert concepts_job_again['job_id'] == concepts_job['job_id'], \
            'a repeated idempotency key made a second concepts job'
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['concepts']) == 3, \
            'a repeated idempotency key generated concepts twice'

        # --- 5) choose a concept ------------------------------------------------------------
        chosen = next(c for c in detail['concepts'] if c['mode'] == 'story')
        choose_resp = client.post(
            f'/api/campaigns/{campaign_id}/concepts/{chosen["id"]}/choose')
        assert choose_resp.status_code == 200, choose_resp.text

        # --- 6) storyboard job -> poll -> a version with shots + correct start_times -------
        board_job = client.post(f'/api/campaigns/{campaign_id}/storyboards', data={
            'concept_id': chosen['id'], 'aspect': '9:16', 'duration': '24',
            'platform': 'instagram', 'idempotency_key': f'board-{uuid.uuid4()}'}).json()
        board_detail = _await_job(client, board_job['job_id'])
        assert board_detail['status'] == 'succeeded', board_detail
        version_id = board_detail['version_id']
        storyboard_id = board_detail['storyboard_id']
        assert version_id and storyboard_id, board_detail

        version = client.get(f'/api/versions/{version_id}').json()
        assert len(version['shots']) == 8, len(version['shots'])
        assert version['version']['status'] == 'draft'

        # red-before-green: prove the start_time assertion can actually fail before
        # trusting that it passes (mirrors video_flow_test.py's own price-check pattern).
        expected_starts = [0, 3, 5, 7.5, 9.5, 12.5, 15.5, 20.5]
        actual_starts = [float(s['start_time']) for s in version['shots']]
        try:
            assert actual_starts == [x + 1 for x in expected_starts], \
                'deliberately wrong on purpose'
        except AssertionError:
            print('RED (expected): start_time check fails against a deliberately wrong '
                 f'value -> {actual_starts} != {[x + 1 for x in expected_starts]}')
        else:
            raise AssertionError('the deliberately wrong start_time assertion should '
                                 'have failed')
        assert actual_starts == expected_starts, actual_starts
        assert version['total_duration'] == 24, version['total_duration']
        print(f'GREEN: derived start_times check out -> {actual_starts}')

        # --- 6a) progress: on_progress calls reach jobs.progress with real stage changes
        # and a non-decreasing fraction, and the finished job carries no stale error -------
        progress_calls = []
        real_jobs_progress = jobs.progress

        def spy_progress(job_id, stage, fraction=None, message='', force=False):
            progress_calls.append((stage, fraction))
            return real_jobs_progress(job_id, stage, fraction, message, force=force)

        jobs.progress = spy_progress
        try:
            progress_job = client.post(f'/api/campaigns/{campaign_id}/concepts',
                                       data={'idempotency_key': f'concepts-prog-{uuid.uuid4()}'}
                                       ).json()
            progress_detail = _await_job(client, progress_job['job_id'])
            assert progress_detail['status'] == 'succeeded', progress_detail
        finally:
            jobs.progress = real_jobs_progress

        stages_seen = [s for s, _ in progress_calls]
        assert 'thinking' in stages_seen, stages_seen
        assert 'writing' in stages_seen, stages_seen
        assert 'saving' in stages_seen, stages_seen
        writing_fractions = [f for s, f in progress_calls if s == 'writing' and f is not None]
        assert writing_fractions == sorted(writing_fractions), \
            f'the writing fraction must be non-decreasing: {writing_fractions}'

        final_job = client.get(f'/api/jobs/{progress_job["job_id"]}').json()
        assert final_job['status'] == 'succeeded', final_job
        assert final_job['progress']['stage'] == 'saving', final_job['progress']
        assert not final_job['error'], 'a succeeded job must carry no stale progress error'
        print('ads_flow: progress stages + non-decreasing fraction, no stale error ok')

        # --- 6a-warnings) a board with SOFT-only errors is ACCEPTED (never fails the ------
        # --- job) and its warnings are persisted on the version + returned by GET ---------
        showcase_concept = next(c for c in detail['concepts'] if c['mode'] == 'showcase')
        choose_resp2 = client.post(
            f'/api/campaigns/{campaign_id}/concepts/{showcase_concept["id"]}/choose')
        assert choose_resp2.status_code == 200, choose_resp2.text

        director.generate_storyboard = fake_generate_storyboard_with_warnings
        try:
            warn_job = client.post(f'/api/campaigns/{campaign_id}/storyboards', data={
                'concept_id': showcase_concept['id'], 'aspect': '9:16', 'duration': '24',
                'platform': 'instagram',
                'idempotency_key': f'board-warn-{uuid.uuid4()}'}).json()
            warn_detail = _await_job(client, warn_job['job_id'])
            assert warn_detail['status'] == 'succeeded', warn_detail   # SOFT never fails a job
        finally:
            director.generate_storyboard = fake_generate_storyboard

        warn_version = client.get(f'/api/versions/{warn_detail["version_id"]}').json()
        assert warn_version['warnings'], warn_version
        assert warn_version['warnings'][0]['rule'] == 9, warn_version['warnings']
        assert warn_version['warnings'][0]['severity'] == 'soft', warn_version['warnings']
        # and the version's own row carries them too, not just the enriched response
        assert warn_version['version']['warnings'] == warn_version['warnings']
        print('ads_flow: soft warnings persisted on the version and returned by GET ok')

        # --- 6b) a character/product a shot actually uses cannot be removed --------------
        # fake_generate_storyboard puts characters[0] (Aanya) and products[0] on every
        # ordinary shot in this board, so both must now be refused with 409, not deleted
        # out from under the storyboard that names them.
        del_used_character = client.delete(
            f'/api/campaigns/{campaign_id}/characters/{character["id"]}')
        assert del_used_character.status_code == 409, del_used_character.text
        del_used_product = client.delete(
            f'/api/campaigns/{campaign_id}/products/{product_row_id}')
        assert del_used_product.status_code == 409, del_used_product.text
        detail = client.get(f'/api/campaigns/{campaign_id}').json()
        assert len(detail['characters']) == 1 and len(detail['products']) == 1, detail
        print('ads_flow: delete refused (409) for a character/product a shot references ok')

        # --- 7) PATCH camera_move on a draft: no fork ---------------------------------------
        first_shot = version['shots'][0]
        patch_resp = client.patch(f'/api/shots/{first_shot["id"]}',
                                  json={'camera_move': 'orbit'}).json()
        assert patch_resp['version_id'] == version_id, \
            'editing a draft must not fork'
        assert patch_resp['state'] == 'draft', patch_resp    # never advances a draft

        # --- 8) approve the version -----------------------------------------------------------
        approve_resp = client.post(f'/api/versions/{version_id}/approve').json()
        assert approve_resp['version_id'] == version_id

        # --- 9) PATCH again -> forks, and the response names the NEW version -------------------
        patch_resp2 = client.patch(f'/api/shots/{first_shot["id"]}',
                                   json={'camera_angle': 'low angle'}).json()
        forked_version_id = patch_resp2['version_id']
        assert forked_version_id != version_id, 'editing an approved version must fork'

        forked = client.get(f'/api/versions/{forked_version_id}').json()
        assert forked['version']['status'] == 'draft'
        assert len(forked['versions']) == 2, forked['versions']

        # --- 10) structural ops on the fork keep positions contiguous ----------------------
        fork_shots = forked['shots']
        target_shot_id = str(fork_shots[2]['id'])

        add_resp = client.post(f'/api/versions/{forked_version_id}/shots',
                               json={'after_position': 1, 'duration': 2}).json()
        v = client.get(f'/api/versions/{add_resp["version_id"]}').json()
        assert _positions(v) == list(range(len(v['shots']))), _positions(v)
        assert len(v['shots']) == 9, len(v['shots'])

        dup_resp = client.post(f'/api/shots/{target_shot_id}/duplicate').json()
        v = client.get(f'/api/versions/{dup_resp["version_id"]}').json()
        assert _positions(v) == list(range(len(v['shots']))), _positions(v)
        assert len(v['shots']) == 10, len(v['shots'])

        split_resp = client.post(f'/api/shots/{dup_resp["shot_id"]}/split').json()
        v = client.get(f'/api/versions/{split_resp["version_id"]}').json()
        assert _positions(v) == list(range(len(v['shots']))), _positions(v)
        assert len(v['shots']) == 11, len(v['shots'])

        delete_resp = client.post(f'/api/shots/{split_resp["shot_ids"][1]}/delete').json()
        v = client.get(f'/api/versions/{delete_resp["version_id"]}').json()
        assert _positions(v) == list(range(len(v['shots']))), _positions(v)
        assert len(v['shots']) == 10, len(v['shots'])

        reordered_ids = [s['id'] for s in reversed(v['shots'])]
        reorder_resp = client.post(f'/api/versions/{delete_resp["version_id"]}/reorder',
                                   json=reordered_ids)
        assert reorder_resp.status_code == 200, reorder_resp.text
        v = client.get(f'/api/versions/{reorder_resp.json()["version_id"]}').json()
        assert _positions(v) == list(range(len(v['shots']))), _positions(v)
        assert [s['id'] for s in v['shots']] == reordered_ids

        # --- 11) illegal event -> 409 ---------------------------------------------------------
        ordinary_shot = next(s for s in v['shots'] if s['kind'] == 'shot')
        first_approve = client.post(
            f'/api/shots/{ordinary_shot["id"]}/approve-instructions')
        assert first_approve.status_code == 200, first_approve.text
        second_approve = client.post(
            f'/api/shots/{ordinary_shot["id"]}/approve-instructions')
        assert second_approve.status_code == 409, second_approve.text

        # --- 12) the DirectorError path: a board that stays invalid fails the job, --------
        #         not the request ------------------------------------------------------------
        director.generate_storyboard = fake_generate_storyboard_invalid
        try:
            bad_job = client.post(f'/api/campaigns/{campaign_id}/storyboards', data={
                'concept_id': chosen['id'], 'aspect': '9:16', 'duration': '24',
                'platform': 'instagram',
                'idempotency_key': f'board-bad-{uuid.uuid4()}'}).json()
            bad_detail = _await_job(client, bad_job['job_id'])
            assert bad_detail['status'] == 'failed', bad_detail
            assert 'each ordinary shot is 1-6s' in bad_detail['error'], bad_detail
        finally:
            director.generate_storyboard = fake_generate_storyboard

        # --- 13) cross-workspace access -> 404 -------------------------------------------------
        assert other_client.get(f'/api/campaigns/{campaign_id}').status_code == 404
        assert other_client.get(f'/api/versions/{version_id}').status_code == 404
        assert other_client.patch(f'/api/shots/{first_shot["id"]}',
                                  json={'camera_angle': 'x'}).status_code == 404
        assert other_client.post(f'/api/versions/{version_id}/approve').status_code == 404

        # --- 14) frames: reference frame generation, stubbed provider + fidelity ----------------
        real_provider_get, real_check_fidelity, real_hf_download = (
            providers.get, orchestrator.check_frame_fidelity, hf.download)
        fake_provider = FakeFrameProvider()
        providers.get = lambda *a, **k: fake_provider
        hf.download = fake_hf_download
        fidelity_calls = []

        def fake_check_fidelity(product_paths, frame_path, description='', max_edge=None):
            fidelity_calls.append((product_paths, frame_path, description))
            return True, 'looks right', {'pieces': [], 'verdict': 'pass'}
        orchestrator.check_frame_fidelity = fake_check_fidelity
        credits.grant(ws_id, 20, 'ads-frame-test-fund')

        try:
            # a real local file behind the product's s3_key, so the (stubbed) fidelity
            # check has something to be gated on exactly like the real one would be
            fixture = pathlib.Path('out/ads_test_fixtures/fixture.jpg')
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(b'fixture-bytes')
            frame_piece_key = f'uploads/{piece_id}-frame.jpg'
            storage.put(fixture, frame_piece_key)
            db.query('UPDATE pieces SET s3_key = %s WHERE id = %s',
                     (frame_piece_key, piece_id))

            frame_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 9, 'title': 'Frame test board'}, [
                    {'duration': 2, 'character_ids': [character['id']],
                     'product_ids': [product_row_id], 'spec': {'product_visibility': 'hero'}},
                    {'duration': 2, 'character_ids': [character['id']],
                     'product_ids': [product_row_id], 'spec': {'product_visibility': 'small'}},
                    {'duration': 2, 'character_ids': [character['id']],
                     'product_ids': [product_row_id], 'spec': {'product_visibility': 'small'}},
                    {'duration': 3, 'kind': 'end_card', 'spec': {'brand_text': 'ACME'}},
                ])
            frame_version_id = frame_created['version_id']
            frame_v = client.get(f'/api/versions/{frame_version_id}').json()
            ordinary_ids = [s['id'] for s in frame_v['shots'] if s['kind'] == 'shot']

            # frame generation refused (409) on a draft version
            refused = client.post(f'/api/shots/{ordinary_ids[0]}/frames',
                                  data={'idempotency_key': 'f-refused'})
            assert refused.status_code == 409, refused.text

            # approve instructions on every ordinary shot, then the version itself
            for sid in ordinary_ids:
                approved = client.post(f'/api/shots/{sid}/approve-instructions')
                assert approved.status_code == 200, approved.text
            assert client.post(
                f'/api/versions/{frame_version_id}/approve').status_code == 200

            # --- single frame: job completes, v1 auto-selected, credits settle --------------
            balance_before = credits.balance(ws_id)
            single = client.post(f'/api/shots/{ordinary_ids[0]}/frames',
                                 data={'idempotency_key': 'f-single'})
            assert single.status_code == 200, single.text
            single_job = _await_job(client, single.json()['job_id'])
            assert single_job['status'] == 'succeeded', single_job

            frame_v = client.get(f'/api/versions/{frame_version_id}').json()
            shot0 = next(s for s in frame_v['shots'] if s['id'] == ordinary_ids[0])
            assert shot0['state'] == 'frame_review', shot0
            assert len(shot0['frame_variants']) == 1, shot0['frame_variants']
            v1_asset_id = shot0['frame_variants'][0]['id']
            assert shot0['selected_frame_url'], 'the new frame was not auto-selected'
            assert shot0['frame_variants'][0]['fidelity'] == \
                {'ok': True, 'reason': 'looks right', 'pieces': []}, shot0['frame_variants']
            assert fidelity_calls, 'the fidelity check never ran for a hero-visibility shot'
            assert credits.balance(ws_id) == balance_before - 1, credits.balance(ws_id)

            # --- regenerate: v2 exists, v1 still listed --------------------------------------
            balance_before = credits.balance(ws_id)
            regen = client.post(f'/api/shots/{ordinary_ids[0]}/frames',
                                data={'idempotency_key': 'f-regen'})
            regen_job = _await_job(client, regen.json()['job_id'])
            assert regen_job['status'] == 'succeeded', regen_job
            assert credits.balance(ws_id) == balance_before - 1, credits.balance(ws_id)
            frame_v = client.get(f'/api/versions/{frame_version_id}').json()
            shot0 = next(s for s in frame_v['shots'] if s['id'] == ordinary_ids[0])
            variant_ids = [v['id'] for v in shot0['frame_variants']]
            assert v1_asset_id in variant_ids and len(variant_ids) == 2, variant_ids

            # --- select v1, approve-frame -> frame_approved + an approvals row ---------------
            select = client.post(f'/api/shots/{ordinary_ids[0]}/select',
                                 data={'asset_id': v1_asset_id})
            assert select.status_code == 200, select.text
            approve = client.post(f'/api/shots/{ordinary_ids[0]}/approve-frame', data={})
            assert approve.status_code == 200, approve.text
            assert approve.json()['state'] == 'frame_approved', approve.json()
            approvals = db.query(
                "SELECT asset_id FROM approvals WHERE shot_id = %s AND decision = 'approved' "
                "AND stage = 'frame'", (ordinary_ids[0],))
            assert len(approvals) == 1 and str(approvals[0]['asset_id']) == v1_asset_id, \
                approvals

            # --- batch: a stale confirm_credits is refused with a 409 ------------------------
            mismatch = client.post(f'/api/versions/{frame_version_id}/frames',
                                   data={'confirm_credits': 999, 'idempotency_key': 'batch-bad'})
            assert mismatch.status_code == 409, mismatch.text

            # --- batch: the right number -> one job per eligible shot ------------------------
            fresh_estimate = client.get(f'/api/versions/{frame_version_id}/estimate').json()
            assert set(fresh_estimate['shots']) == {ordinary_ids[1], ordinary_ids[2]}, \
                fresh_estimate
            assert fresh_estimate['credits'] == 2, fresh_estimate

            # the first shot the batch generates raises once — the rest of the batch must
            # still complete, and only that one shot ends up frame_failed/refunded
            failed_once = {'used': False}

            def flaky_generate(prompt, image_urls=None, aspect_ratio='9:16', quality='high',
                              seed=None, num_images=1):
                if not failed_once['used']:
                    failed_once['used'] = True
                    raise RuntimeError('provider exploded')
                return ['fake://frame.png']
            fake_provider.generate = flaky_generate

            balance_before = credits.balance(ws_id)
            batch = client.post(f'/api/versions/{frame_version_id}/frames', data={
                'confirm_credits': fresh_estimate['credits'], 'idempotency_key': 'batch-good'})
            assert batch.status_code == 200, batch.text
            batch_job_ids = batch.json()['job_ids']
            assert len(batch_job_ids) == 2, batch_job_ids
            for jid in batch_job_ids:
                _await_job(client, jid)
            # flaky_generate only fails its FIRST call, so it is already back to reliable
            # for the retry step below — no need to restore fake_provider.generate here.

            frame_v = client.get(f'/api/versions/{frame_version_id}').json()
            by_id = {s['id']: s for s in frame_v['shots']}
            batch_states = {sid: by_id[sid]['state'] for sid in (ordinary_ids[1], ordinary_ids[2])}
            assert sorted(batch_states.values()) == ['frame_failed', 'frame_review'], \
                batch_states
            failed_shot_id = next(sid for sid, state in batch_states.items()
                                  if state == 'frame_failed')
            # exactly one of the two delivered -> exactly one credit spent, the other refunded
            assert credits.balance(ws_id) == balance_before - 1, credits.balance(ws_id)

            # --- retry of the failed shot works -----------------------------------------------
            balance_before = credits.balance(ws_id)
            retry = client.post(f'/api/shots/{failed_shot_id}/frames',
                                data={'idempotency_key': 'f-retry'})
            assert retry.status_code == 200, retry.text
            retry_job = _await_job(client, retry.json()['job_id'])
            assert retry_job['status'] == 'succeeded', retry_job
            assert credits.balance(ws_id) == balance_before - 1, credits.balance(ws_id)
            frame_v = client.get(f'/api/versions/{frame_version_id}').json()
            retried_shot = next(s for s in frame_v['shots'] if s['id'] == failed_shot_id)
            assert retried_shot['state'] == 'frame_review', retried_shot

            print('ads_flow: frames ok')

            # --- a fork made while a frame job is running -> completion lands on EVERY ------
            #     shot row still waiting on that exact job, not just the version active
            #     when it finishes (storyboard.complete_generation). The pre-fork row is
            #     NOT "left untouched" — nothing else will ever complete it once its own
            #     version stops being the one anything points at, so it must heal too.
            fork_target_id = (ordinary_ids[2] if failed_shot_id == ordinary_ids[1]
                              else ordinary_ids[1])
            shot_key = by_id[fork_target_id]['shot_key']
            job_id = orchestrator.start_frame(ws_id, fork_target_id, 'f-fork-race',
                                              str(admin_account['id']))
            pre_fork_state = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                                      (fork_target_id,), one=True)['state']
            assert pre_fork_state == 'frame_generating', pre_fork_state

            # an unrelated edit on a different shot forks the whole version — the job's
            # own real row (status='queued') is what keeps this fork's copy of
            # fork_target_id from being reconciled away as abandoned (see
            # storyboard.reconcile_generating, run right after every fork)
            forked = client.patch(f'/api/shots/{ordinary_ids[0]}',
                                  json={'camera_angle': 'a different angle'}).json()
            new_version_id = forked['version_id']
            assert new_version_id != frame_version_id, 'the edit should have forked'
            forked_copy_state = db.query(
                """SELECT state FROM storyboard_shots
                    WHERE version_id = %s AND shot_key = %s""",
                (new_version_id, shot_key), one=True)['state']
            assert forked_copy_state == 'frame_generating', \
                'a job genuinely still queued must not be reconciled away by the fork'

            orchestrator.run_frame(job_id)

            new_version = client.get(f'/api/versions/{new_version_id}').json()
            active_shot = next(s for s in new_version['shots'] if s['shot_key'] == shot_key)
            assert active_shot['state'] == 'frame_review', active_shot
            assert active_shot['selected_frame_url'], active_shot

            healed_pre_fork = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                                       (fork_target_id,), one=True)
            assert healed_pre_fork['state'] == 'frame_review', \
                'the pre-fork row must heal too — its version is no longer the active ' \
                'one, so nothing else will ever complete it'

            print('ads_flow: fork-while-generating heals every waiting shot ok')
        finally:
            providers.get, orchestrator.check_frame_fidelity, hf.download = (
                real_provider_get, real_check_fidelity, real_hf_download)

        # --- 15) videos: clip generation, stubbed provider + fidelity --------------------
        # run_video_shot calls orchestrator.detect_cuts (real ffmpeg, never stubbed — see
        # the cut-fixture section below) then orchestrator.check_shot_clip (the paid
        # vision call, stubbed here exactly the way video.check_fidelity used to be — the
        # OLD single-clip flow, app.run_video, still calls the real video.check_fidelity
        # and is covered separately by video_flow_test.py, untouched by this change).
        ensure_fixture_mp4()
        ensure_fixture_frame()
        ensure_cut_fixture_mp4()
        fake_video_provider = FakeVideoProvider()
        real_video_get, real_video_generate, real_check_shot_clip, real_hf_fetch_bytes = (
            video.get, video.generate, orchestrator.check_shot_clip, hf._fetch_bytes)

        video_generate_calls = []
        video_control = {'fail_always': False}
        hf_fetch_control = {'use_cut_fixture': False}

        def fake_video_generate(still_path, prompt, negative, duration, provider,
                                on_progress=None):
            video_generate_calls.append(duration)
            if video_control['fail_always']:
                raise RuntimeError('provider exploded')
            return 'fake://clip.mp4'

        def fake_hf_fetch_bytes(url):
            if hf_fetch_control['use_cut_fixture']:
                return CUT_FIXTURE_MP4.read_bytes()
            return FIXTURE_MP4.read_bytes()

        fidelity_control = {'sequence': None}

        def fake_check_shot_clip(product_paths, frame_path, mp4_path, description='',
                                 has_person=False):
            if fidelity_control['sequence']:
                ok, reason = fidelity_control['sequence'].pop(0)
                return ok, reason, {}
            return True, 'looks right', {}

        video.get = lambda *a, **k: fake_video_provider
        video.generate = fake_video_generate
        hf._fetch_bytes = fake_hf_fetch_bytes
        orchestrator.check_shot_clip = fake_check_shot_clip

        try:
            video_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 5.5, 'title': 'Video test board'}, [
                    {'duration': 2.5, 'character_ids': [character['id']],
                     'product_ids': [product_row_id], 'spec': {'product_visibility': 'hero'}},
                    {'duration': 3, 'kind': 'end_card', 'spec': {'brand_text': 'ACME'}},
                ])
            video_version_id = video_created['version_id']
            video_shot_row = next(s for s in storyboard.get_version(ws_id, video_version_id)['shots']
                                  if s['kind'] == 'shot')
            video_shot_id = str(video_shot_row['id'])
            video_shot_key = video_shot_row['shot_key']

            storyboard.apply_event(ws_id, video_shot_id, 'approve_instructions')
            assert client.post(
                f'/api/versions/{video_version_id}/approve').status_code == 200

            # video refused before any frame is even approved (409, IllegalTransition)
            refused = client.post(f'/api/shots/{video_shot_id}/videos',
                                  data={'idempotency_key': 'v-refused'})
            assert refused.status_code == 409, refused.text

            # drive the shot to frame_approved by hand (a fake asset, no real generation —
            # the frame HTTP path is already exhaustively covered above; this section is
            # about the video endpoints only).
            fixture_frame_key = f'ads/{campaign_id}/{video_shot_key}/frame-fake.jpg'
            storage.put(FIXTURE_FRAME, fixture_frame_key)
            storyboard.apply_event(ws_id, video_shot_id, 'start_frame')
            storyboard.apply_event(ws_id, video_shot_id, 'frame_done')
            fake_frame = storyboard.add_asset(
                ws_id, campaign_id, video_created['storyboard_id'], video_version_id,
                'storyboard_image', fixture_frame_key, shot_id=video_shot_id,
                shot_key=video_shot_key)
            storyboard.select_asset(ws_id, video_shot_id, fake_frame['id'])
            approve_frame_resp = client.post(f'/api/shots/{video_shot_id}/approve-frame',
                                             data={})
            assert approve_frame_resp.status_code == 200, approve_frame_resp.text
            assert approve_frame_resp.json()['state'] == 'frame_approved', \
                approve_frame_resp.json()

            # clip seconds for a 2.5s shot on the Kling stub (durations 3-15) = 3
            expected_clip_seconds = orchestrator._clip_seconds(2.5, fake_video_provider)
            assert expected_clip_seconds == 3, expected_clip_seconds
            expected_price = video.credits_for(3, fake_video_provider)

            video_estimate = client.get(
                f'/api/versions/{video_version_id}/estimate',
                params={'stage': 'videos'}).json()
            assert video_estimate['shots'] == [video_shot_id], video_estimate
            assert video_estimate['per_shot'][video_shot_id] == expected_price, video_estimate

            # --- success path: video_review, asset selected, poster exists ---------------
            balance_before = credits.balance(ws_id)
            single_video = client.post(f'/api/shots/{video_shot_id}/videos',
                                       data={'idempotency_key': 'v-single'})
            assert single_video.status_code == 200, single_video.text
            single_video_job = _await_job(client, single_video.json()['job_id'])
            assert single_video_job['status'] == 'succeeded', single_video_job
            assert video_generate_calls[-1] == 3, video_generate_calls

            video_v = client.get(f'/api/versions/{video_version_id}').json()
            video_shot = next(s for s in video_v['shots'] if s['id'] == video_shot_id)
            assert video_shot['state'] == 'video_review', video_shot
            assert len(video_shot['video_variants']) == 1, video_shot['video_variants']
            v1 = video_shot['video_variants'][0]
            assert v1['clip_seconds'] == 3, v1
            assert v1['poster_url'], 'no poster was recorded for the clip'
            assert video_shot['selected_video_url'], 'the new clip was not auto-selected'
            assert credits.balance(ws_id) == balance_before - expected_price, \
                credits.balance(ws_id)

            # --- fidelity fail then pass -> 2 attempts, 1 charge --------------------------
            fidelity_control['sequence'] = [(False, 'metal colour shifted'),
                                            (True, 'now matches')]
            balance_before = credits.balance(ws_id)
            calls_before = len(video_generate_calls)
            regen = client.post(f'/api/shots/{video_shot_id}/videos',
                                data={'idempotency_key': 'v-regen'})
            assert regen.status_code == 200, regen.text
            regen_job = _await_job(client, regen.json()['job_id'])
            assert regen_job['status'] == 'succeeded', regen_job
            assert len(video_generate_calls) - calls_before == 2, \
                'a failed fidelity check must trigger exactly one free retry'
            assert credits.balance(ws_id) == balance_before - expected_price, \
                'only one charge for the whole attempt, retry included'
            video_v = client.get(f'/api/versions/{video_version_id}').json()
            video_shot = next(s for s in video_v['shots'] if s['id'] == video_shot_id)
            v2 = next(v for v in video_shot['video_variants'] if v['id'] != v1['id'])
            assert v2['fidelity'] == {'ok': True, 'reason': 'now matches', 'pieces': []}, v2

            # --- both attempts fail -> still delivered, fidelity ok False recorded -------
            fidelity_control['sequence'] = [(False, 'first attempt bad'),
                                            (False, 'second attempt bad')]
            balance_before = credits.balance(ws_id)
            both_fail = client.post(f'/api/shots/{video_shot_id}/videos',
                                    data={'idempotency_key': 'v-bothfail'})
            both_fail_job = _await_job(client, both_fail.json()['job_id'])
            assert both_fail_job['status'] == 'succeeded', both_fail_job   # delivered anyway
            assert credits.balance(ws_id) == balance_before - expected_price, \
                credits.balance(ws_id)
            video_v = client.get(f'/api/versions/{video_version_id}').json()
            video_shot = next(s for s in video_v['shots'] if s['id'] == video_shot_id)
            latest_variant = video_shot['video_variants'][-1]
            assert latest_variant['fidelity'] == \
                {'ok': False, 'reason': 'second attempt bad', 'pieces': []}, latest_variant
            fidelity_control['sequence'] = None

            # --- a clip with a real hard cut -> caught by the REAL detect_cuts (no LLM,
            #     never stubbed) before check_shot_clip is even asked -> both attempts use
            #     the same cut fixture -> still fails after the retry, 2 attempts, 1 charge.
            #     check_shot_clip stays stubbed to 'looks right' throughout, proving the
            #     failure came from the cut detector, not the (stubbed) vision check.
            hf_fetch_control['use_cut_fixture'] = True
            balance_before = credits.balance(ws_id)
            calls_before = len(video_generate_calls)
            cut_resp = client.post(f'/api/shots/{video_shot_id}/videos',
                                   data={'idempotency_key': 'v-cut'})
            assert cut_resp.status_code == 200, cut_resp.text
            cut_job = _await_job(client, cut_resp.json()['job_id'])
            assert cut_job['status'] == 'succeeded', cut_job   # delivered anyway
            assert len(video_generate_calls) - calls_before == 2, \
                'a detected cut must trigger exactly one free retry, same as a vision fail'
            assert credits.balance(ws_id) == balance_before - expected_price, \
                'only one charge for the whole attempt, retry included'
            hf_fetch_control['use_cut_fixture'] = False

            video_v = client.get(f'/api/versions/{video_version_id}').json()
            video_shot = next(s for s in video_v['shots'] if s['id'] == video_shot_id)
            cut_variant = video_shot['video_variants'][-1]
            assert cut_variant['fidelity']['ok'] is False, cut_variant
            assert 'different scene' in cut_variant['fidelity']['reason'], cut_variant

            print('ads_flow: videos cut detector (real ffmpeg, unstubbed) ok')

            print('ads_flow: videos success/fidelity ok')

            # --- provider raises -> video_failed + refund, others unaffected -------------
            other_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 3, 'title': 'Video failure isolation'},
                [{'duration': 3, 'character_ids': [character['id']],
                 'product_ids': [product_row_id], 'spec': {'product_visibility': 'hero'}}])
            other_version_id = other_created['version_id']
            other_shot_row = storyboard.get_version(ws_id, other_version_id)['shots'][0]
            other_shot_id = str(other_shot_row['id'])
            storyboard.apply_event(ws_id, other_shot_id, 'approve_instructions')
            storyboard.approve_version(ws_id, other_version_id)
            other_frame_key = f'ads/{campaign_id}/{other_shot_row["shot_key"]}/frame-fake.jpg'
            storage.put(FIXTURE_FRAME, other_frame_key)
            storyboard.apply_event(ws_id, other_shot_id, 'start_frame')
            storyboard.apply_event(ws_id, other_shot_id, 'frame_done')
            other_frame_asset = storyboard.add_asset(
                ws_id, campaign_id, other_created['storyboard_id'], other_version_id,
                'storyboard_image', other_frame_key, shot_id=other_shot_id,
                shot_key=other_shot_row['shot_key'])
            storyboard.select_asset(ws_id, other_shot_id, other_frame_asset['id'])
            storyboard.apply_event(ws_id, other_shot_id, 'approve_frame',
                                   asset_id=other_frame_asset['id'])

            video_control['fail_always'] = True
            balance_before_fail = credits.balance(ws_id)
            fail_job_id = orchestrator.start_video(ws_id, other_shot_id, 'v-fail-1',
                                                   str(admin_account['id']))
            orchestrator.run_video_shot(fail_job_id)
            video_control['fail_always'] = False
            fail_job_row = db.query('SELECT status FROM jobs WHERE id = %s',
                                    (fail_job_id,), one=True)
            assert fail_job_row['status'] == 'failed', fail_job_row
            assert credits.balance(ws_id) == balance_before_fail, \
                'a failed video job must be fully refunded'
            other_shot_after = next(s for s in storyboard.get_version(ws_id, other_version_id)['shots']
                                    if str(s['id']) == other_shot_id)
            assert other_shot_after['state'] == 'video_failed', other_shot_after

            # the unrelated, earlier shot is untouched by this failure
            unaffected = client.get(f'/api/versions/{video_version_id}').json()
            unaffected_shot = next(s for s in unaffected['shots'] if s['id'] == video_shot_id)
            assert unaffected_shot['state'] == 'video_review', unaffected_shot

            print('ads_flow: video provider failure isolation ok')

            # --- batch mismatch 409 -------------------------------------------------------
            batch_mismatch = client.post(
                f'/api/versions/{video_version_id}/videos',
                data={'confirm_credits': 999, 'idempotency_key': 'vbatch-bad'})
            assert batch_mismatch.status_code == 409, batch_mismatch.text

            # --- approve-video -> video_approved, ready_for_render true once every ordinary
            #     shot in the version is video_approved (the end_card is excluded) --------
            approve_video_resp = client.post(f'/api/shots/{video_shot_id}/approve-video',
                                             data={})
            assert approve_video_resp.status_code == 200, approve_video_resp.text
            assert approve_video_resp.json()['state'] == 'video_approved', \
                approve_video_resp.json()

            final_v = client.get(f'/api/versions/{video_version_id}').json()
            assert final_v['ready_for_render'] is True, final_v['ready_for_render']

            print('ads_flow: videos approve/ready_for_render ok')

            # --- PATCH a shot on an approved version with its OWN current values -> --------
            #     no-op: same version id back, version count unchanged, state untouched -----
            versions_before_noop = len(
                client.get(f'/api/versions/{video_version_id}').json()['versions'])
            current_video_shot = next(s for s in final_v['shots'] if s['id'] == video_shot_id)
            noop_patch = client.patch(f'/api/shots/{video_shot_id}', json={
                'duration': str(current_video_shot['duration']),   # numeric string == number
                'lighting': '',                                   # '' == a never-set key
            }).json()
            assert noop_patch['version_id'] == video_version_id, \
                'a no-op PATCH must not fork an approved version'
            assert noop_patch['state'] == 'video_approved', noop_patch
            versions_after_noop = len(
                client.get(f'/api/versions/{video_version_id}').json()['versions'])
            assert versions_after_noop == versions_before_noop, \
                'a no-op PATCH must not create a new version'
            print('ads_flow: no-op PATCH (unchanged values) does not fork or demote ok')

            # --- 16) THE REAL BUG, reproduced exactly: approved V2 -> start video on S ------
            #     -> (job still queued) split+delete another shot forks V2 -> V3, approve
            #     V3 -> job completes -> both V2:S and V3:S land on video_review with the
            #     clip selected -> re-approve V2 from the dropdown -> GET V2 shows
            #     video_review, never stuck in video_generating again. ------------------------
            bug_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 6, 'title': 'multi-version healing'},
                [{'duration': 3}, {'duration': 3}])
            bug_storyboard_id = bug_created['storyboard_id']
            version_2_id = bug_created['version_id']
            bug_shots = storyboard.get_version(ws_id, version_2_id)['shots']
            shot_s_id, shot_s_key = str(bug_shots[0]['id']), str(bug_shots[0]['shot_key'])
            shot_t_id = str(bug_shots[1]['id'])

            storyboard.apply_event(ws_id, shot_s_id, 'approve_instructions')
            storyboard.apply_event(ws_id, shot_t_id, 'approve_instructions')
            storyboard.approve_version(ws_id, version_2_id)   # "approved V2"

            # drive S to frame_approved by hand — frame generation itself is exhaustively
            # covered elsewhere in this file; this section is about job completion routing.
            bug_frame_key = f'ads/{campaign_id}/{shot_s_key}/frame-fake.jpg'
            storage.put(FIXTURE_FRAME, bug_frame_key)
            storyboard.apply_event(ws_id, shot_s_id, 'start_frame')
            storyboard.apply_event(ws_id, shot_s_id, 'frame_done')
            bug_frame_asset = storyboard.add_asset(
                ws_id, campaign_id, bug_storyboard_id, version_2_id, 'storyboard_image',
                bug_frame_key, shot_id=shot_s_id, shot_key=shot_s_key)
            storyboard.select_asset(ws_id, shot_s_id, bug_frame_asset['id'])
            storyboard.apply_event(ws_id, shot_s_id, 'approve_frame',
                                   asset_id=bug_frame_asset['id'])

            # start the video job on S — created (status='queued'), nothing has run it yet
            video_job_id = orchestrator.start_video(ws_id, shot_s_id, 'v-bug-repro',
                                                    str(admin_account['id']))
            mid_flight = db.query('SELECT state FROM storyboard_shots WHERE id = %s',
                                  (shot_s_id,), one=True)
            assert mid_flight['state'] == 'video_generating', mid_flight

            # while the job is still queued: split T, then delete the split-off half — any
            # structural edit on an approved version forks it (ensure_editable) -> V3
            split_resp = client.post(f'/api/shots/{shot_t_id}/split')
            assert split_resp.status_code == 200, split_resp.text
            version_3_id = split_resp.json()['version_id']
            assert version_3_id != version_2_id, 'the split must have forked the version'
            _t_half, split_off_id = split_resp.json()['shot_ids']
            delete_resp = client.post(f'/api/shots/{split_off_id}/delete')
            assert delete_resp.status_code == 200, delete_resp.text
            assert delete_resp.json()['version_id'] == version_3_id, \
                'V3 is already a draft — deleting from it must not fork again'

            shot_s_in_v3 = next(s for s in storyboard.get_version(ws_id, version_3_id)['shots']
                                if str(s['shot_key']) == shot_s_key)
            assert shot_s_in_v3['state'] == 'video_generating', \
                ('the fork must copy S mid-flight, and the job genuinely still queued '
                 'must stop reconcile_generating from healing it away as abandoned')

            storyboard.approve_version(ws_id, version_3_id)   # "approved V3" (V2 superseded)

            # the job finishes now -> complete_generation must heal EVERY row still
            # waiting on it: V2's original S (no longer active, soon superseded) AND
            # V3's forked copy (the one active when the job actually finishes)
            orchestrator.run_video_shot(video_job_id)

            for vid, label in ((version_2_id, 'V2 (pre-fork)'), (version_3_id, 'V3 (active)')):
                row = next(s for s in storyboard.get_version(ws_id, vid)['shots']
                          if str(s['shot_key']) == shot_s_key)
                assert row['state'] == 'video_review', (label, row)
                assert row['selected_video_asset_id'], (label, row)

            # the user re-approves the ORIGINAL version from the dropdown — the exact
            # real-world sequence. V2 becomes active/approved again; its copy of S must
            # show video_review, never the video_generating it was stuck in before this fix.
            storyboard.approve_version(ws_id, version_2_id)
            reapproved = client.get(f'/api/versions/{version_2_id}').json()
            reapproved_shot = next(s for s in reapproved['shots'] if s['shot_key'] == shot_s_key)
            assert reapproved_shot['state'] == 'video_review', reapproved_shot
            assert reapproved_shot['selected_video_url'], reapproved_shot

            print('ads_flow: multi-version job completion (the real-bug repro) ok')

            # --- the OLD single-clip flow's own offer is untouched by widening the ad
            #     video providers' own durations ------------------------------------------
            options = client.get('/api/video-options').json()
            assert sorted(options['durations']) == [5, 10], options['durations']
        finally:
            video.get, video.generate, orchestrator.check_shot_clip, hf._fetch_bytes = (
                real_video_get, real_video_generate, real_check_shot_clip,
                real_hf_fetch_bytes)

        # --- 17/18) music + final render: music.generate is stubbed (a real 20s sine
        #     mp3 — no fal call), but render.render() runs FOR REAL against real tiny
        #     ffmpeg testsrc clips stored through storage.put in local mode, so the
        #     actual ffmpeg pipeline (normalise, end card, join, mux) is genuinely
        #     exercised, not mocked away. ------------------------------------------------
        credits.grant(ws_id, 20, 'ads-music-render-fund')
        real_music_generate = music.generate
        music.generate = fake_music_generate
        try:
            mr_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 5.5, 'title': 'Music+render board',
                                     'aspect_ratio': '9:16'},
                [{'duration': 2.0, 'character_ids': [character['id']],
                 'product_ids': [product_row_id],
                 'spec': {'product_visibility': 'hero', 'transition_out': 'cut'}},
                 {'duration': 1.5, 'character_ids': [character['id']],
                 'spec': {'transition_out': 'cut'}},
                 {'duration': 2.0, 'kind': 'end_card',
                 'spec': {'brand_text': 'MEHTA JEWELLER', 'tagline': 'Since 1952'}}])
            mr_version_id = mr_created['version_id']
            mr_storyboard_id = mr_created['storyboard_id']
            mr_shots = storyboard.get_version(ws_id, mr_version_id)['shots']
            mr_shot_a, mr_shot_b = [s for s in mr_shots if s['kind'] == 'shot']
            mr_end_card = next(s for s in mr_shots if s['kind'] == 'end_card')

            for sid in (str(mr_shot_a['id']), str(mr_shot_b['id']), str(mr_end_card['id'])):
                storyboard.apply_event(ws_id, sid, 'approve_instructions')
            assert client.post(f'/api/versions/{mr_version_id}/approve').status_code == 200

            # render refused: no clip approved yet (422 + a problem naming a missing clip)
            early_render = client.post(f'/api/versions/{mr_version_id}/render',
                                       data={'idempotency_key': 'mr-early'})
            assert early_render.status_code == 422, early_render.text
            assert 'clip' in early_render.json()['detail'], early_render.json()

            # drive both ordinary shots to video_approved with REAL tiny ffmpeg clips —
            # one carries a 'probe' in its metadata (the run_video_shot-shaped case),
            # the other has none, so validate_for_render's fetch-and-probe fallback is
            # exercised too.
            mr_clip_ids = {}
            for shot, clip_seconds, with_probe in (
                    (mr_shot_a, 2.2, True), (mr_shot_b, 1.7, False)):
                shot_id, shot_key = str(shot['id']), shot['shot_key']
                frame_key = f'ads/{campaign_id}/{shot_key}/frame-fake.jpg'
                storage.put(FIXTURE_FRAME, frame_key)
                storyboard.apply_event(ws_id, shot_id, 'start_frame')
                storyboard.apply_event(ws_id, shot_id, 'frame_done')
                frame_asset = storyboard.add_asset(
                    ws_id, campaign_id, mr_storyboard_id, mr_version_id, 'storyboard_image',
                    frame_key, shot_id=shot_id, shot_key=shot_key)
                storyboard.select_asset(ws_id, shot_id, frame_asset['id'])
                storyboard.apply_event(ws_id, shot_id, 'approve_frame',
                                       asset_id=frame_asset['id'])

                clip_path = pathlib.Path(f'out/ads_test_fixtures/mr-clip-{shot_key}.mp4')
                render._make_clip(clip_path, (480, 854), clip_seconds)
                clip_key = f'ads/{campaign_id}/{shot_key}/clip-fake.mp4'
                storage.put(clip_path, clip_key)
                metadata = {'probe': video._probe(clip_path)} if with_probe else {}
                clip_asset = storyboard.add_asset(
                    ws_id, campaign_id, mr_storyboard_id, mr_version_id, 'video_clip',
                    clip_key, shot_id=shot_id, shot_key=shot_key, metadata=metadata)
                storyboard.select_asset(ws_id, shot_id, clip_asset['id'])
                storyboard.apply_event(ws_id, shot_id, 'start_video')
                storyboard.apply_event(ws_id, shot_id, 'video_done')
                storyboard.apply_event(ws_id, shot_id, 'approve_video',
                                       asset_id=clip_asset['id'])
                mr_clip_ids[shot_id] = clip_asset['id']

            # render still refused: every clip is approved, but no music decision yet
            no_music_render = client.post(f'/api/versions/{mr_version_id}/render',
                                          data={'idempotency_key': 'mr-nomusic'})
            assert no_music_render.status_code == 422, no_music_render.text
            assert 'music' in no_music_render.json()['detail'].lower(), no_music_render.json()

            # music: a stale confirm_credits is refused with a fresh estimate (409)
            music_estimate = client.get(f'/api/versions/{mr_version_id}/estimate',
                                        params={'stage': 'music'}).json()
            assert music_estimate['credits'] > 0, music_estimate
            music_mismatch = client.post(f'/api/versions/{mr_version_id}/music',
                                         data={'confirm_credits': 999,
                                              'idempotency_key': 'music-bad'})
            assert music_mismatch.status_code == 409, music_mismatch.text

            # music: generate for real (stubbed provider) -> succeeds, auto-selected
            balance_before_music = credits.balance(ws_id)
            music_resp = client.post(f'/api/versions/{mr_version_id}/music', data={
                'confirm_credits': str(music_estimate['credits']),
                'idempotency_key': 'music-1'})
            assert music_resp.status_code == 200, music_resp.text
            music_job = _await_job(client, music_resp.json()['job_id'])
            assert music_job['status'] == 'succeeded', music_job
            assert credits.balance(ws_id) == \
                balance_before_music - music_estimate['credits'], credits.balance(ws_id)

            mr_v = client.get(f'/api/versions/{mr_version_id}').json()
            assert len(mr_v['music']['variants']) == 1, mr_v['music']
            music_asset_id = mr_v['music']['variants'][0]['id']
            assert mr_v['music']['selected_id'] == music_asset_id, mr_v['music']
            assert mr_v['music']['approved'] is False, mr_v['music']

            # render still refused: music is selected but not yet approved
            unapproved = client.post(f'/api/versions/{mr_version_id}/render',
                                     data={'idempotency_key': 'mr-unapproved'})
            assert unapproved.status_code == 422, unapproved.text

            # approve the music -> ready_for_render/render_ready both flip true
            approve_music_resp = client.post(
                f'/api/versions/{mr_version_id}/music/approve', data={})
            assert approve_music_resp.status_code == 200, approve_music_resp.text
            mr_v = client.get(f'/api/versions/{mr_version_id}').json()
            assert mr_v['music']['approved'] is True, mr_v['music']
            assert mr_v['render_ready']['ready'] is True, mr_v['render_ready']

            print('ads_flow: music estimate/generate/select/approve ok')

            # --- render: the real thing --------------------------------------------------
            render_resp = client.post(f'/api/versions/{mr_version_id}/render',
                                      data={'idempotency_key': 'mr-render-1'})
            assert render_resp.status_code == 200, render_resp.text
            render_job = _await_job(client, render_resp.json()['job_id'], timeout_s=60)
            assert render_job['status'] == 'succeeded', render_job

            mr_v = client.get(f'/api/versions/{mr_version_id}').json()
            assert len(mr_v['renders']) == 1, mr_v['renders']
            render_row = mr_v['renders'][0]
            assert render_row['status'] == 'succeeded', render_row
            assert render_row['url'], render_row
            assert render_row['resolution'] == '1080x1920', render_row

            render_detail = client.get(f"/api/renders/{render_row['id']}").json()
            manifest = render_detail['manifest']
            expected_clip_ids = [mr_clip_ids[str(mr_shot_a['id'])],
                                mr_clip_ids[str(mr_shot_b['id'])]]
            assert manifest['clip_asset_ids'] == expected_clip_ids, manifest['clip_asset_ids']
            assert manifest['music_asset_id'] == music_asset_id, manifest
            assert manifest['shot_durations'] == [2.0, 1.5, 2.0], manifest['shot_durations']

            total_duration = 2.0 + 1.5 + 2.0    # all-cut transitions: no dissolve shrink
            assert abs(float(manifest['duration']) - total_duration) <= 0.15, \
                manifest['duration']
            assert (manifest['width'], manifest['height']) == (1080, 1920), manifest

            # GET /renders/{id}/download redirects to a presigned URL named
            # <campaign>-v<version_number>.mp4 — storage.presign only honours that
            # filename hint in S3 mode (local mode serves straight from /media/ with
            # no Content-Disposition, see storage.py), so in this local-mode test the
            # filename itself isn't inspectable; what's checked here is that the
            # redirect fires and the target URL genuinely serves the mp4 bytes.
            download_resp = client.get(f"/api/renders/{render_row['id']}/download",
                                       follow_redirects=False)
            assert download_resp.status_code == 307, download_resp.text
            followed = client.get(f"/api/renders/{render_row['id']}/download")
            assert followed.status_code == 200 and followed.content, \
                'the presigned download URL must actually serve the mp4 bytes'

            print('ads_flow: render download redirect (named <campaign>-v<n>.mp4) ok')

            completed_storyboard = db.query(
                'SELECT status FROM storyboards WHERE id = %s', (mr_storyboard_id,),
                one=True)
            assert completed_storyboard['status'] == 'completed', completed_storyboard

            asset_row = db.query(
                """SELECT ga.key FROM final_renders fr
                     JOIN generated_assets ga ON ga.id = fr.asset_id
                    WHERE fr.id = %s""", (render_row['id'],), one=True)
            local_render = pathlib.Path('out/ads_test_fixtures/mr-render.mp4')
            storage.fetch(asset_row['key'], local_render)
            render_streams = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of',
                 'csv=p=0', str(local_render)], capture_output=True, text=True,
                check=True).stdout.split()
            assert 'audio' in render_streams and 'video' in render_streams, render_streams

            print('ads_flow: final render (real ffmpeg, real music mux) ok')

            # --- silent path: skip music -> the render still carries an audio stream ----
            silent_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 4, 'title': 'Silent render board',
                                     'aspect_ratio': '9:16'},
                [{'duration': 2.0, 'spec': {'transition_out': 'cut'}},
                 {'duration': 2.0, 'kind': 'end_card', 'spec': {'brand_text': 'ACME'}}])
            silent_version_id = silent_created['version_id']
            silent_storyboard_id = silent_created['storyboard_id']
            silent_shots = storyboard.get_version(ws_id, silent_version_id)['shots']
            silent_shot = next(s for s in silent_shots if s['kind'] == 'shot')
            silent_shot_id = str(silent_shot['id'])
            silent_shot_key = silent_shot['shot_key']
            silent_end_card_id = str(next(s for s in silent_shots
                                          if s['kind'] == 'end_card')['id'])

            storyboard.apply_event(ws_id, silent_shot_id, 'approve_instructions')
            storyboard.apply_event(ws_id, silent_end_card_id, 'approve_instructions')
            storyboard.approve_version(ws_id, silent_version_id)

            silent_frame_key = f'ads/{campaign_id}/{silent_shot_key}/frame-fake.jpg'
            storage.put(FIXTURE_FRAME, silent_frame_key)
            storyboard.apply_event(ws_id, silent_shot_id, 'start_frame')
            storyboard.apply_event(ws_id, silent_shot_id, 'frame_done')
            silent_frame_asset = storyboard.add_asset(
                ws_id, campaign_id, silent_storyboard_id, silent_version_id,
                'storyboard_image', silent_frame_key, shot_id=silent_shot_id,
                shot_key=silent_shot_key)
            storyboard.select_asset(ws_id, silent_shot_id, silent_frame_asset['id'])
            storyboard.apply_event(ws_id, silent_shot_id, 'approve_frame',
                                   asset_id=silent_frame_asset['id'])

            silent_clip_path = pathlib.Path('out/ads_test_fixtures/silent-clip.mp4')
            render._make_clip(silent_clip_path, (480, 854), 2.2)
            silent_clip_key = f'ads/{campaign_id}/{silent_shot_key}/clip-fake.mp4'
            storage.put(silent_clip_path, silent_clip_key)
            silent_clip_asset = storyboard.add_asset(
                ws_id, campaign_id, silent_storyboard_id, silent_version_id, 'video_clip',
                silent_clip_key, shot_id=silent_shot_id, shot_key=silent_shot_key,
                metadata={'probe': video._probe(silent_clip_path)})
            storyboard.select_asset(ws_id, silent_shot_id, silent_clip_asset['id'])
            storyboard.apply_event(ws_id, silent_shot_id, 'start_video')
            storyboard.apply_event(ws_id, silent_shot_id, 'video_done')
            storyboard.apply_event(ws_id, silent_shot_id, 'approve_video',
                                   asset_id=silent_clip_asset['id'])

            skip_resp = client.post(f'/api/versions/{silent_version_id}/music/skip',
                                    data={'skipped': 'true'})
            assert skip_resp.status_code == 200, skip_resp.text
            silent_v = client.get(f'/api/versions/{silent_version_id}').json()
            assert silent_v['music']['skipped'] is True, silent_v['music']
            assert silent_v['render_ready']['ready'] is True, silent_v['render_ready']

            silent_render_resp = client.post(f'/api/versions/{silent_version_id}/render',
                                             data={'idempotency_key': 'mr-silent'})
            assert silent_render_resp.status_code == 200, silent_render_resp.text
            silent_job = _await_job(client, silent_render_resp.json()['job_id'],
                                    timeout_s=60)
            assert silent_job['status'] == 'succeeded', silent_job

            silent_v = client.get(f'/api/versions/{silent_version_id}').json()
            silent_render_row = silent_v['renders'][0]
            silent_asset_row = db.query(
                """SELECT ga.key FROM final_renders fr
                     JOIN generated_assets ga ON ga.id = fr.asset_id
                    WHERE fr.id = %s""", (silent_render_row['id'],), one=True)
            silent_local = pathlib.Path('out/ads_test_fixtures/silent-render.mp4')
            storage.fetch(silent_asset_row['key'], silent_local)
            silent_streams = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of',
                 'csv=p=0', str(silent_local)], capture_output=True, text=True,
                check=True).stdout.split()
            assert 'audio' in silent_streams, 'a silent render must still have an audio stream'

            print('ads_flow: silent render (no music, still has an audio stream) ok')

            # --- a render that blows up mid-job still records status='failed' -----------
            real_render_render = render.render

            def exploding_render(*_a, **_k):
                raise RuntimeError('render exploded')

            render.render = exploding_render
            try:
                fail_job_id = orchestrator.start_render(ws_id, mr_version_id, 'mr-fail-1',
                                                        str(admin_account['id']))
                orchestrator.run_render(fail_job_id)
            finally:
                render.render = real_render_render
            fail_job_row = db.query('SELECT status FROM jobs WHERE id = %s',
                                    (fail_job_id,), one=True)
            assert fail_job_row['status'] == 'failed', fail_job_row
            failed_render_row = db.query(
                "SELECT status FROM final_renders WHERE job_id = %s", (fail_job_id,),
                one=True)
            assert failed_render_row['status'] == 'failed', failed_render_row

            print('ads_flow: a render that raises mid-job still records status=failed ok')

            # --- end-card style: preview endpoint, non-forking PATCH, style in the
            #     render manifest, and two styles actually painting different pixels ----

            # 404 before any ordinary shot has a frame/clip -- a fresh end_card-only board.
            empty_created = storyboard.create_storyboard(
                ws_id, campaign_id, {'target_duration': 4, 'title': 'Style preview board'},
                [{'duration': 2.0}, {'duration': 2.0, 'kind': 'end_card',
                                     'spec': {'brand_text': 'Style Co'}}])
            empty_shots = storyboard.get_version(ws_id, empty_created['version_id'])['shots']
            empty_end_card_id = str(next(s for s in empty_shots if s['kind'] == 'end_card')['id'])
            no_source = client.get(f'/api/shots/{empty_end_card_id}/endcard-preview',
                                   params={'style': 'heritage'})
            assert no_source.status_code == 404, no_source.text

            # Reuse the fully-set-up silent-render board (frame AND clip both selected/
            # approved on its one ordinary shot) for the rest.
            previews = {}
            for style in ('heritage', 'modern', 'minimal'):
                resp = client.get(f'/api/shots/{silent_end_card_id}/endcard-preview',
                                  params={'style': style, 'brand_text': 'Style Co',
                                         'tagline': 'Look closer'})
                assert resp.status_code == 200, (style, resp.text)
                assert resp.headers['content-type'] == 'image/png', resp.headers
                assert len(resp.content) > 500, f'{style} preview is suspiciously small'
                previews[style] = resp.content
            assert previews['heritage'] != previews['modern'] != previews['minimal'], \
                'all three style previews rendered byte-identical images'
            print('ads_flow: endcard-preview PNG per style, 404 before a source frame exists ok')

            # An unknown style is a 400, not a 500 or a silent fallback.
            bad_style = client.get(f'/api/shots/{silent_end_card_id}/endcard-preview',
                                   params={'style': 'nope'})
            assert bad_style.status_code == 400, bad_style.text

            # --- PATCH style never forks / never demotes state, draft AND approved -------
            before_state = client.get(f'/api/versions/{silent_version_id}').json()
            before_card = next(s for s in before_state['shots'] if s['kind'] == 'end_card')
            assert before_card['state'] == 'video_approved', before_card
            style_resp = client.post(f'/api/shots/{silent_end_card_id}/end-card-style',
                                     data={'style': 'modern'})
            assert style_resp.status_code == 200, style_resp.text
            assert style_resp.json()['version_id'] == silent_version_id, \
                'set_end_card_style must never fork a draft version either'
            after_state = client.get(f'/api/versions/{silent_version_id}').json()
            after_card = next(s for s in after_state['shots'] if s['kind'] == 'end_card')
            assert after_card['spec']['end_card_style'] == 'modern', after_card['spec']
            assert after_card['state'] == 'video_approved', \
                'a style change must never demote production state'

            # silent_version_id is already 'approved' at this point (the silent render
            # above only succeeds against an approved version) -- prove the SAME call
            # still doesn't fork now that the version is frozen.
            version_row = db.query('SELECT status FROM storyboard_versions WHERE id = %s',
                                   (silent_version_id,), one=True)
            assert version_row['status'] == 'approved', version_row
            style_resp_2 = client.post(f'/api/shots/{silent_end_card_id}/end-card-style',
                                       data={'style': 'minimal'})
            assert style_resp_2.status_code == 200, style_resp_2.text
            assert style_resp_2.json()['version_id'] == silent_version_id, \
                'set_end_card_style must never fork an APPROVED version'
            print('ads_flow: PATCH end-card style never forks or demotes state ok')

            # --- keep_case: same non-forking shape, and it changes the preview's SPELLING
            #     case without altering spelling itself ------------------------------------
            upper_resp = client.get(f'/api/shots/{silent_end_card_id}/endcard-preview',
                                    params={'style': 'heritage', 'brand_text': 'Mehta Jeweller',
                                           'tagline': 'x', 'keep_case': 'false'})
            kept_resp = client.get(f'/api/shots/{silent_end_card_id}/endcard-preview',
                                   params={'style': 'heritage', 'brand_text': 'Mehta Jeweller',
                                          'tagline': 'x', 'keep_case': 'true'})
            assert upper_resp.status_code == 200 and kept_resp.status_code == 200
            assert upper_resp.content != kept_resp.content, \
                'keep_case=true/false must render visibly different (case) previews'

            keep_case_resp = client.post(f'/api/shots/{silent_end_card_id}/end-card-keep-case',
                                         data={'keep_case': 'true'})
            assert keep_case_resp.status_code == 200, keep_case_resp.text
            assert keep_case_resp.json()['version_id'] == silent_version_id, \
                'set_end_card_keep_case must never fork an approved version'
            kc_state = client.get(f'/api/versions/{silent_version_id}').json()
            kc_card = next(s for s in kc_state['shots'] if s['kind'] == 'end_card')
            assert kc_card['spec']['end_card_keep_case'] is True, kc_card['spec']
            assert kc_card['state'] == 'video_approved', \
                'a keep_case change must never demote production state'
            print('ads_flow: PATCH end-card keep_case never forks; changes rendered case only ok')

            # --- render with style=modern records it, and its pixels differ from minimal's
            client.post(f'/api/shots/{silent_end_card_id}/end-card-style',
                       data={'style': 'modern'})
            modern_render = client.post(f'/api/versions/{silent_version_id}/render',
                                        data={'idempotency_key': 'style-modern'})
            assert modern_render.status_code == 200, modern_render.text
            modern_job = _await_job(client, modern_render.json()['job_id'], timeout_s=60)
            assert modern_job['status'] == 'succeeded', modern_job
            modern_render_id = client.get(
                f'/api/versions/{silent_version_id}').json()['renders'][0]['id']
            modern_manifest = client.get(f'/api/renders/{modern_render_id}').json()['manifest']
            assert modern_manifest['end_card']['style'] == 'modern', modern_manifest['end_card']

            client.post(f'/api/shots/{silent_end_card_id}/end-card-style',
                       data={'style': 'minimal'})
            minimal_render = client.post(f'/api/versions/{silent_version_id}/render',
                                         data={'idempotency_key': 'style-minimal'})
            assert minimal_render.status_code == 200, minimal_render.text
            minimal_job = _await_job(client, minimal_render.json()['job_id'], timeout_s=60)
            assert minimal_job['status'] == 'succeeded', minimal_job
            minimal_render_id = client.get(
                f'/api/versions/{silent_version_id}').json()['renders'][0]['id']
            minimal_manifest = client.get(f'/api/renders/{minimal_render_id}').json()['manifest']
            assert minimal_manifest['end_card']['style'] == 'minimal', minimal_manifest['end_card']

            def _end_card_last_frame(render_id, out_path):
                asset_row = db.query(
                    """SELECT ga.key FROM final_renders fr
                         JOIN generated_assets ga ON ga.id = fr.asset_id
                        WHERE fr.id = %s""", (render_id,), one=True)
                local = pathlib.Path(f'out/ads_test_fixtures/{out_path}')
                storage.fetch(asset_row['key'], local)
                probe = video._probe(local)
                video._extract_frame(local, max(probe['duration'] - 0.1, 0),
                                     local.with_suffix('.png'))
                return local.with_suffix('.png')

            modern_frame = _end_card_last_frame(modern_render_id, 'style-modern.mp4')
            minimal_frame = _end_card_last_frame(minimal_render_id, 'style-minimal.mp4')
            from PIL import Image, ImageChops
            diff = ImageChops.difference(
                Image.open(modern_frame).convert('L'), Image.open(minimal_frame).convert('L'))
            bbox = diff.point(lambda p: 255 if p > 25 else 0).getbbox()
            assert bbox is not None, \
                'modern and minimal end cards rendered pixel-identical end frames'
            print('ads_flow: render(style=modern) vs render(style=minimal) -- manifest '
                 'records each style, and their end-card pixels actually differ ok')
        finally:
            music.generate = real_music_generate

        print('ads_flow ok')

    finally:
        for ws_id in made_workspaces:
            # final_renders.asset_id/.job_id both RESTRICT deleting the row they
            # reference, so it must go before generated_assets and jobs, not after —
            # this order used to run it last and every render this file's own music+
            # render section now creates hit final_renders_asset_id_fkey on cleanup.
            db.query("""DELETE FROM final_renders WHERE campaign_id IN
                        (SELECT id FROM campaigns WHERE workspace_id = %s)""", (ws_id,))
            db.query("DELETE FROM approvals WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM generated_assets WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM credit_ledger WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM jobs WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM campaigns WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM pieces WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM memberships WHERE workspace_id = %s", (ws_id,))
            db.query("DELETE FROM workspaces WHERE id = %s", (ws_id,))
        for user_id in made_users:
            db.query('DELETE FROM sessions WHERE user_id = %s', (user_id,))
            db.query('DELETE FROM users WHERE id = %s', (user_id,))
        db.close()


if __name__ == '__main__':
    main()
    sys.exit(0)
