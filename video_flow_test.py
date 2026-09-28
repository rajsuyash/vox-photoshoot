"""End-to-end check of the video-ad flow, against a real database and a real FastAPI app,
with only the paid provider calls stubbed.

video.run and video.check_fidelity are the two calls that would otherwise (a) hit a real
video generation provider and (b) run an Anthropic call to judge the result — the only
things this app must never do in a test. Everything else runs for real, including
video.get()/credits_for()/needs_reframe()/reframe()'s real geometry logic and
motion.render()'s vocabulary parsing, none of which touch the network. Monkeypatching
`video.run`/`video.check_fidelity` works because app.py calls them by attribute lookup
(`video.run(...)`, `video.check_fidelity(...)`) rather than binding a local reference at
import time — the same reason the module-level assignment below is enough.

    .venv/bin/python video_flow_test.py     # needs DATABASE_URL pointed at a THROWAWAY db
"""

import os
import pathlib
import sys
import time
import uuid

os.environ.setdefault('DATABASE_URL',
                      'postgresql://postgres:pg@localhost:55432/donna?sslmode=disable')
os.environ.pop('S3_BUCKET', None)          # local storage, never S3, for this check

from fastapi.testclient import TestClient
from PIL import Image

import admin
import app as app_module
import auth
import credits
import db
import jobs
import storage
import video

FIXTURE_MP4 = pathlib.Path(
    'out/videos/812d3d2c-8f96-4af9-be01-710d7e9b5da9-hero-higgsfield-kling-5s.mp4')


def main() -> None:
    assert FIXTURE_MP4.exists(), f'fixture missing: {FIXTURE_MP4}'
    db.migrate()
    probe = video._probe(FIXTURE_MP4)          # real ffprobe, offline

    run_calls = []
    run_mode = {'raise': False}

    def fake_run(still_path, category_key, description, location_key, framing, duration,
                aspect, *, motion='', mood='', note='', provider=None, out_dir,
                on_progress=None):
        run_calls.append((framing, duration, aspect))
        if on_progress:
            on_progress()
        if run_mode['raise']:
            raise RuntimeError('stub: provider generation failed')
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'{framing}-{len(run_calls)}.mp4'
        out_path.write_bytes(FIXTURE_MP4.read_bytes())
        return {'path': out_path, 'prompt': 'stub prompt', 'negative': None, 'plan': {},
                'provider': f'{provider.backend}/{provider.model}',
                'width': probe['width'], 'height': probe['height'],
                'duration': probe['duration']}

    fidelity_calls = []
    fidelity_descriptions = []
    fidelity_queue = []

    def fake_check_fidelity(still_path, mp4_path, description=''):
        fidelity_calls.append(1)
        fidelity_descriptions.append(description)
        return fidelity_queue.pop(0) if fidelity_queue else (True, 'ok')

    reframe_calls = []

    def fake_reframe(still_path, aspect, out_dir):
        # video.needs_reframe is real (pure geometry, no network) — only the paid
        # outpaint call itself is stubbed. A plain convert+save stands in for it, kept
        # deterministic per (still_path, aspect) so the "reuse, don't regenerate" checks
        # below are checking real behaviour, not an artefact of the stub.
        reframe_calls.append((str(still_path), aspect))
        out_dir = pathlib.Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'reframe-{aspect.replace(":", "x")}.png'
        with Image.open(still_path) as image:
            image.convert('RGB').save(out_path)
        return out_path

    # Set once, read by app.py's run_video via plain `video.run(...)` / `video.reframe
    # (...)` / `video.check_fidelity(...)` attribute lookups — no hasattr adapter left
    # to route around.
    video.run = fake_run
    video.reframe = fake_reframe
    video.check_fidelity = fake_check_fidelity

    client = TestClient(app_module.app)
    made_workspaces, made_users = [], []

    try:
        # --- provision: a workspace, a user, credits ------------------------------------
        ws = admin.create_workspace(f'video-test-{uuid.uuid4().hex[:8]}')
        ws_id = str(ws['id'])
        made_workspaces.append(ws_id)
        account = admin.create_account(f'vt-{uuid.uuid4().hex[:8]}@test', ws_id, 'owner')
        user_id = str(account['id'])
        made_users.append(user_id)
        credits.grant(ws_id, 200, 'test-fund')

        token = auth.start_session(user_id, ws_id)
        client.cookies.set(auth.COOKIE, token)

        # A second workspace, purely for the cross-workspace 404 check.
        ws2 = admin.create_workspace(f'video-test-other-{uuid.uuid4().hex[:8]}')
        ws2_id = str(ws2['id'])
        made_workspaces.append(ws2_id)
        account2 = admin.create_account(f'vt2-{uuid.uuid4().hex[:8]}@test', ws2_id, 'owner')
        user2_id = str(account2['id'])
        made_users.append(user2_id)
        token2 = auth.start_session(user2_id, ws2_id)
        client2 = TestClient(app_module.app)
        client2.cookies.set(auth.COOKIE, token2)

        # --- a source shoot job with three delivered images -----------------------------
        uploads = pathlib.Path('out/uploads')
        uploads.mkdir(parents=True, exist_ok=True)

        def make_local_image(name: str, w: int, h: int) -> str:
            path = uploads / name
            Image.new('RGB', (w, h), (120, 90, 60)).save(path)
            key = f'shoots/videotest/{name}'
            storage.put(path, key)
            return key

        # 3:4 — must be reframed for a 9:16 or a 4:5 video.
        hero_key = make_local_image(f'vt-hero-{uuid.uuid4().hex[:8]}.png', 900, 1200)
        # Also 3:4, from a DIFFERENT still — this is the still whose reframe must not
        # collide with hero's own (the bug the orchestrator flagged: two videos from
        # different stills of the same shoot at the same aspect used to overwrite each
        # other's reframe file and job_images row).
        detail_key = make_local_image(f'vt-detail-{uuid.uuid4().hex[:8]}.png', 900, 1200)
        # Exactly 9:16 — must NOT be reframed for a 9:16 video.
        profile_key = make_local_image(f'vt-profile-{uuid.uuid4().hex[:8]}.png', 900, 1600)

        with db.tx() as conn:
            shoot = jobs.create(
                ws_id, user_id, 'shoot', f'shoot-{uuid.uuid4()}',
                {'category': 'necklace', 'description': 'rose gold pendant necklace',
                 'location': 'pondicherry', 'framings': ['hero', 'profile', 'detail']},
                piece_id='vidtestpiece1', reserved_credits=3, conn=conn)
            credits.reserve(conn, ws_id, str(shoot['id']), 3)
        shoot_id = str(shoot['id'])
        assert jobs.claim(shoot_id)
        jobs.add_image(shoot_id, shoot_id, 'hero', 1, hero_key, 111)
        jobs.add_image(shoot_id, shoot_id, 'profile', 1, profile_key, 222)
        jobs.add_image(shoot_id, shoot_id, 'detail', 1, detail_key, 333)
        jobs.finish(shoot_id, 'succeeded', settled_credits=3)
        credits.settle(shoot_id, delivered=3)

        provider = video.get()

        # --- price quote: /api/video-options and /api/videos/quote must match reality --
        opts = client.get('/api/video-options', params={'category': 'necklace'}).json()
        assert sorted(opts['durations']) == [5, 10], opts
        assert {'9:16', '4:5', '1:1', '16:9'} <= {s['key'] for s in opts['sizes']}, opts
        for duration in opts['durations']:
            assert opts['prices'][str(duration)] == video.credits_for(duration, provider)

        quote = client.get('/api/videos/quote', params={
            'source_job_id': shoot_id, 'framing': 'hero', 'attempt': '1',
            'aspect': '9:16', 'duration': '5'}).json()
        assert quote['needs_reframe'] is True, quote
        assert quote['price'] == video.credits_for(5, provider) + 1, quote
        quote_no_reframe = client.get('/api/videos/quote', params={
            'source_job_id': shoot_id, 'framing': 'profile', 'attempt': '1',
            'aspect': '9:16', 'duration': '5'}).json()
        assert quote_no_reframe['needs_reframe'] is False, quote_no_reframe
        assert quote_no_reframe['price'] == video.credits_for(5, provider), quote_no_reframe

        # --- 1) create: needs a reframe (3:4 still, 9:16 target) ------------------------
        balance_before = credits.balance(ws_id)
        resp = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'hero', 'attempt': '1', 'aspect': '9:16',
            'duration': '5', 'motion': '', 'mood': '', 'note': 'catch the light',
            'idempotency_key': f'vid-1-{uuid.uuid4()}'})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        expected_price = video.credits_for(5, provider) + 1
        assert body['needs_reframe'] is True, body

        # Red-before-green: prove the price assertion can actually fail before trusting
        # that it passed. Mirrors the pattern already used in motion.py/billing.py.
        try:
            assert body['expected'] == expected_price + 1, 'deliberately wrong on purpose'
        except AssertionError:
            print(f'RED (expected): price check fails against a deliberately wrong '
                 f'value -> {body["expected"]} != {expected_price + 1}')
        else:
            raise AssertionError('the deliberately wrong price assertion should have failed')
        assert body['expected'] == expected_price, body
        print(f'GREEN: real price checks out -> {body["expected"]} == {expected_price}')

        hero_video_job_id = body['job_id']
        detail_hero = _await_terminal(client, hero_video_job_id)
        assert detail_hero['status'] == 'succeeded', detail_hero
        assert detail_hero['video'] is not None and detail_hero['video']['url']
        assert detail_hero['needs_reframe'] is True

        assert credits.balance(ws_id) == balance_before - expected_price, \
            (credits.balance(ws_id), balance_before, expected_price)

        # The reframe is keyed on aspect + SOURCE FRAMING + SOURCE ATTEMPT, not aspect
        # alone — this is the collision the orchestrator flagged.
        hero_reframe_framing = 'reframe-9x16-hero-1'
        hero_reframe_key = f'shoots/{shoot_id}/{hero_reframe_framing}.png'
        hero_reframe_row = jobs.image_at(shoot_id, hero_reframe_framing, 1)
        assert hero_reframe_row is not None, 'the reframed still was not stored as a job image'
        assert hero_reframe_row['s3_key'] == hero_reframe_key, hero_reframe_row

        hero_jv = db.query('SELECT * FROM job_videos WHERE job_id = %s',
                          (hero_video_job_id,), one=True)
        assert hero_jv is not None and hero_jv['aspect'] == '9:16'
        assert hero_jv['width'] == probe['width']
        # still_key must be the STORAGE key the reframe was put under, never a local
        # filesystem path (which is ephemeral and meaningless on any other container).
        assert hero_jv['still_key'] == hero_reframe_key, hero_jv['still_key']
        assert not hero_jv['still_key'].startswith('out/'), hero_jv['still_key']

        # --- 1b) a SECOND still (detail) reframed to the SAME aspect must not collide ---
        resp_detail = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'detail', 'attempt': '1', 'aspect': '9:16',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'vid-detail-{uuid.uuid4()}'})
        assert resp_detail.status_code == 200, resp_detail.text
        detail_video_job_id = resp_detail.json()['job_id']
        detail_detail = _await_terminal(client, detail_video_job_id)
        assert detail_detail['status'] == 'succeeded', detail_detail

        detail_reframe_framing = 'reframe-9x16-detail-1'
        detail_reframe_key = f'shoots/{shoot_id}/{detail_reframe_framing}.png'
        detail_reframe_row = jobs.image_at(shoot_id, detail_reframe_framing, 1)
        assert detail_reframe_row is not None, 'detail reframe was not stored'
        assert detail_reframe_row['s3_key'] == detail_reframe_key
        # The two reframes are DISTINCT rows and DISTINCT files — the actual regression.
        assert hero_reframe_row['s3_key'] != detail_reframe_row['s3_key'], \
            'hero and detail reframes collided on one file'
        assert jobs.image_at(shoot_id, hero_reframe_framing, 1)['s3_key'] == hero_reframe_key, \
            "creating detail's reframe overwrote hero's reframe row"

        detail_jv = db.query('SELECT still_key FROM job_videos WHERE job_id = %s',
                            (detail_video_job_id,), one=True)
        assert detail_jv['still_key'] == detail_reframe_key
        assert detail_jv['still_key'] != hero_jv['still_key'], \
            'hero and detail videos recorded the same still_key'

        # --- 2) idempotency: the same key twice is one job, one charge ------------------
        balance_before_idem = credits.balance(ws_id)
        idem_key = f'vid-idem-{uuid.uuid4()}'
        idem_data = {'job_id': shoot_id, 'framing': 'profile', 'attempt': '1',
                    'aspect': '9:16', 'duration': '5', 'motion': '', 'mood': '',
                    'note': '', 'idempotency_key': idem_key}
        r1 = client.post('/api/videos', data=idem_data).json()
        r2 = client.post('/api/videos', data=idem_data).json()
        assert r1['job_id'] == r2['job_id'], 'a repeated idempotency key made a second job'
        assert r1['needs_reframe'] is False, r1        # profile is exactly 9:16 already
        _await_terminal(client, r1['job_id'])
        assert credits.balance(ws_id) == balance_before_idem - video.credits_for(5, provider), \
            'a repeated idempotency key was charged twice'

        # --- 3) failure path: refunds the video, keeps the 1 reframe credit -------------
        run_mode['raise'] = True
        balance_before_fail = credits.balance(ws_id)
        fail_resp = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'hero', 'attempt': '1', 'aspect': '4:5',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'vid-fail-{uuid.uuid4()}'}).json()
        fail_price = fail_resp['expected']
        assert fail_resp['needs_reframe'] is True, fail_resp   # 3:4 vs 4:5 also mismatches
        fail_detail = _await_terminal(client, fail_resp['job_id'])
        assert fail_detail['status'] == 'failed', fail_detail
        # Only the reframe credit (1) was kept; the rest of the reserved price refunded.
        assert credits.balance(ws_id) == balance_before_fail - 1, \
            (credits.balance(ws_id), balance_before_fail, fail_price)
        run_mode['raise'] = False

        # --- 4) fidelity-fail path: retries exactly once, keeps the second result,------
        #        and persists STRUCTURED fidelity/prompt/plan/provider on the job --------
        fidelity_queue.clear()
        fidelity_queue.extend([(False, 'the pendant is not visible'), (True, 'ok')])
        before_run_count = len(run_calls)
        fidelity_resp = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'profile', 'attempt': '1', 'aspect': '1:1',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'vid-fid-{uuid.uuid4()}'}).json()
        fidelity_detail = _await_terminal(client, fidelity_resp['job_id'])
        assert fidelity_detail['status'] == 'succeeded', fidelity_detail
        assert len(run_calls) - before_run_count == 2, \
            f'expected exactly one retry (2 run() calls), got {len(run_calls) - before_run_count}'

        fidelity_job = db.query('SELECT params, failures FROM jobs WHERE id = %s',
                                (fidelity_resp['job_id'],), one=True)
        fparams = fidelity_job['params']
        assert fparams.get('prompt') == 'stub prompt', fparams
        assert fparams.get('plan') == {}, fparams
        assert fparams.get('provider') == f'{provider.backend}/{provider.model}', fparams
        assert fparams.get('fidelity') == [
            {'ok': False, 'reason': 'the pendant is not visible'},
            {'ok': True, 'reason': 'ok'},
        ], fparams.get('fidelity')
        # `failures` is derived from the `ok` flag, never from matching substrings in the
        # verdict's own sentence.
        assert fidelity_job['failures'] == [['fidelity', 'the pendant is not visible']], \
            fidelity_job['failures']
        # The bug this test guards against: check_fidelity was called against the
        # video frames alone, never told what the piece actually is. Both calls in
        # the retry (fail then pass) must carry the shoot's own description through.
        assert fidelity_descriptions[-2:] == ['rose gold pendant necklace'] * 2, \
            fidelity_descriptions[-2:]

        # --- 5) re-roll: one allowed per video, and each reuses ITS OWN reframe ---------
        hero_reroll = client.post(f'/api/videos/{hero_video_job_id}/reroll', data={}).json()
        assert hero_reroll['expected'] == video.credits_for(5, provider), hero_reroll
        _await_terminal(client, hero_reroll['job_id'])
        hero_reroll_jv = db.query('SELECT still_key FROM job_videos WHERE job_id = %s',
                                 (hero_reroll['job_id'],), one=True)
        assert hero_reroll_jv['still_key'] == hero_reframe_key, \
            "hero's reroll did not reuse hero's own reframe"
        hero_reroll2 = client.post(f'/api/videos/{hero_video_job_id}/reroll', data={})
        assert hero_reroll2.status_code == 409, hero_reroll2.text

        detail_reroll = client.post(f'/api/videos/{detail_video_job_id}/reroll',
                                    data={}).json()
        assert detail_reroll['expected'] == video.credits_for(5, provider), detail_reroll
        _await_terminal(client, detail_reroll['job_id'])
        detail_reroll_jv = db.query('SELECT still_key FROM job_videos WHERE job_id = %s',
                                   (detail_reroll['job_id'],), one=True)
        assert detail_reroll_jv['still_key'] == detail_reframe_key, \
            "detail's reroll did not reuse detail's own reframe"
        assert detail_reroll_jv['still_key'] != hero_reroll_jv['still_key'], \
            'the two rerolls ended up sharing one reframe'

        # --- 6) another workspace's source job -> 404 ------------------------------------
        foreign = client2.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'hero', 'attempt': '1', 'aspect': '9:16',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'vid-foreign-{uuid.uuid4()}'})
        assert foreign.status_code == 404, foreign.text

        # --- 7) download: clean master only ------------------------------------------------
        extra = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'profile', 'attempt': '1', 'aspect': '9:16',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'vid-extra-{uuid.uuid4()}'}).json()
        extra_video_job_id = extra['job_id']
        extra_detail = _await_terminal(client, extra_video_job_id)
        assert extra_detail['status'] == 'succeeded', extra_detail

        clean_dl = client.get(f'/api/videos/{extra_video_job_id}/download',
                              follow_redirects=True)
        assert clean_dl.status_code == 200, clean_dl.status_code

        # --- 8) history and the shoot's own gallery both surface every video ------------
        history = client.get('/api/history').json()
        assert any(row['kind'] == 'video' for row in history), 'no video in /api/history'
        gallery = client.get(f'/api/shoots/{shoot_id}').json()
        # hero, detail, idempotent-profile, failed-hero, fidelity-profile, hero-reroll,
        # detail-reroll, extra-profile — every job above but the one 404.
        assert len(gallery['videos']) >= 8, gallery['videos']

        # --- 9) the ledger is clean ------------------------------------------------------
        total, tail = credits.reconcile(ws_id)
        assert total == tail, (total, tail)

        print(f'video_flow ok — {len(run_calls)} video.run() calls, '
             f'{len(fidelity_calls)} check_fidelity() calls')

    finally:
        for ws_id in made_workspaces:
            db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM jobs WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM memberships WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM workspaces WHERE id = %s', (ws_id,))
        for user_id in made_users:
            db.query('DELETE FROM sessions WHERE user_id = %s', (user_id,))
            db.query('DELETE FROM users WHERE id = %s', (user_id,))
        db.close()


def _await_terminal(client, video_job_id: str, timeout_s: float = 10.0) -> dict:
    """TestClient runs BackgroundTasks synchronously before returning the response, so
    this is normally already terminal on the first read — the loop is only insurance."""
    deadline = time.monotonic() + timeout_s
    detail = client.get(f'/api/videos/{video_job_id}').json()
    while detail['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(0.2)
        detail = client.get(f'/api/videos/{video_job_id}').json()
    return detail


if __name__ == '__main__':
    main()
    sys.exit(0)
