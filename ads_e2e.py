"""End-to-end check of REAL, PAID reference-frame generation: a real fal call per frame and
(for a hero/medium-visibility shot) a real Anthropic fidelity check, against a throwaway
local Postgres. No LLM director call — the storyboard is built directly through
storyboard.py, the same way director.py's own output would land once persisted.

Nothing here is stubbed in real mode — this is the one script allowed to spend real
provider money for the ad-frame stage, which is exactly why it refuses to run against
anything but a local database first (guard copied from video_e2e.py verbatim).

    DATABASE_URL='postgresql://postgres:pg@localhost:55432/donna?sslmode=disable' \\
        .venv/bin/python ads_e2e.py --dry-run       # prompts + estimate, no paid call
    DATABASE_URL='...' .venv/bin/python ads_e2e.py --max-frames 1     # the real thing
    DATABASE_URL='...' .venv/bin/python ads_e2e.py --dry-run --render   # music plan + estimate only
    DATABASE_URL='...' .venv/bin/python ads_e2e.py --render            # videos -> music -> render, real
"""

import argparse
import json
import os
import sys
import urllib.parse

# HARD GUARD, first thing, before any other import: this script provisions data, spends
# real provider credits on success, and its cleanup runs DELETE statements scoped by
# workspace id — never acceptable against anything but a local throwaway database.
_DATABASE_URL = os.environ.get('DATABASE_URL', '')
_HOST = urllib.parse.urlparse(_DATABASE_URL).hostname
if _HOST not in ('localhost', '127.0.0.1'):
    sys.exit(f'refusing to run: DATABASE_URL host is {_HOST!r}, not localhost/127.0.0.1 '
             f'— this script makes real paid provider calls and deletes its own rows by '
             f'workspace id; point it at a local, throwaway Postgres only')

os.environ.pop('S3_BUCKET', None)          # local storage only — never S3 for this check

import pathlib               # noqa: E402 - import order is the point of the guard above
import uuid                  # noqa: E402

import admin          # noqa: E402
import credits         # noqa: E402
import db              # noqa: E402
import music             # noqa: E402
import orchestrator     # noqa: E402
import shoot            # noqa: E402
import storage          # noqa: E402
import storyboard        # noqa: E402
import video             # noqa: E402

PRODUCT_PHOTO = pathlib.Path('out/prod-ring-hero.png')
CAST_KEY = 'aditi'                  # a house cast entry — no talent/portrait job needed
E2E_OUT = pathlib.Path('out/ads/e2e')

MAX_VIDEOS = 2      # hard cap on --videos — this script is the only one allowed to spend
                    # real provider money, so a typo (--videos 20) must not be able to

# The first two durations are 2.5s and 3s on purpose (not round numbers): --videos exercises
# orchestrator._clip_seconds' rounding (2.5s -> a 3s Kling clip) against a real provider,
# not just its own unit-level arithmetic.
SHOTS = [
    {'duration': 2.5, 'spec': {
        'shot_type': 'medium', 'product_visibility': 'small', 'camera_angle': 'eye level',
        'lighting': 'warm golden hour', 'environment': 'a marble courtyard',
        'scene_description': 'she walks slowly through dappled afternoon light',
        'character_action': 'she walks slowly through dappled afternoon light',
        'camera_move': 'slow_push', 'motion_intensity': 'low'}},
    {'duration': 3, 'spec': {
        'shot_type': 'close', 'product_visibility': 'medium',
        'facial_expression': 'a soft, contented smile', 'lighting': 'warm golden hour',
        'character_action': 'she smiles softly, eyes lowering', 'camera_move': 'orbit',
        'motion_intensity': 'medium'}},
    {'duration': 2, 'spec': {
        'shot_type': 'macro', 'product_visibility': 'hero', 'camera_move': 'static',
        'product_interaction': 'she turns her wrist toward the camera',
        'lighting': 'warm golden hour', 'motion_intensity': 'low'}},
]
END_CARD = {'duration': 3, 'kind': 'end_card',
           'spec': {'brand_text': 'AURUM', 'tagline': 'Forever radiant'}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='build prompts + estimate only; stop before any paid call')
    parser.add_argument('--max-frames', type=int, default=1,
                        help='how many shots to actually generate in real mode (default 1)')
    parser.add_argument('--videos', type=int, default=0,
                        help=f'how many shots to also generate a clip for in real mode '
                             f'(default 0, hard cap {MAX_VIDEOS})')
    parser.add_argument('--render', action='store_true',
                        help='go all the way to a final render: video every ordinary '
                             'shot (overrides --max-frames/--videos to cover all of '
                             'them — a render needs every shot approved), generate one '
                             'music take, approve it, then render. In --dry-run, prints '
                             'the music composition plan + estimate and stops there.')
    args = parser.parse_args()
    args.videos = max(0, min(args.videos, MAX_VIDEOS))

    assert PRODUCT_PHOTO.exists(), f'product photo missing: {PRODUCT_PHOTO}'
    db.migrate()

    ws = admin.create_workspace(f'ads-e2e-{uuid.uuid4().hex[:8]}')
    ws_id = str(ws['id'])
    account = admin.create_account(f'ads-e2e-{uuid.uuid4().hex[:8]}@test', ws_id, 'owner')
    user_id = str(account['id'])
    credits.grant(ws_id, 20, 'ads-e2e-fund')
    print(f'workspace {ws_id}, user {user_id}, balance {credits.balance(ws_id)}')

    try:
        piece_id = f'e2ead{uuid.uuid4().hex[:10]}'
        piece_key = f'uploads/{piece_id}.png'
        storage.put(PRODUCT_PHOTO, piece_key)
        db.query(
            """INSERT INTO pieces (id, workspace_id, user_id, s3_key, category, description)
               VALUES (%s, %s, %s, %s, 'ring', %s)""",
            (piece_id, ws_id, user_id, piece_key,
             'a gold solitaire ring with a round brilliant diamond'))

        assert CAST_KEY in shoot.load_cast(), f'house cast key {CAST_KEY!r} not found'

        campaign = storyboard.create_campaign(
            ws_id, 'Ads e2e check', brief={'goal': 'awareness'},
            brand_style='warm, editorial', campaign_style='festive, intimate')
        campaign_id = str(campaign['id'])
        storyboard.add_product(ws_id, campaign_id, piece_id,
                               'keep the stone-setting and band width exact')
        character = storyboard.add_character(ws_id, campaign_id, 'Aditi', role='lead',
                                             cast_key=CAST_KEY)
        character_id = str(character['id'])

        # product_ids on a shot are campaign_products.id (the join row storyboard.add_product
        # just inserted), not the piece id itself.
        product_row_id = str(db.query(
            'SELECT id FROM campaign_products WHERE campaign_id = %s',
            (campaign_id,), one=True)['id'])
        shots = [{**s, 'character_ids': [character_id], 'product_ids': [product_row_id]}
                for s in SHOTS] + [END_CARD]

        created = storyboard.create_storyboard(
            ws_id, campaign_id,
            {'target_duration': sum(s['duration'] for s in shots), 'title': 'Ads e2e ad',
             'aspect_ratio': '9:16', 'visual_style': 'golden hour glow, filmic grain',
             'palette': 'amber and warm gold'},
            shots)
        version_id = created['version_id']

        version = storyboard.get_version(ws_id, version_id)
        ordinary_ids = [str(s['id']) for s in version['shots'] if s['kind'] == 'shot']
        for shot_id in ordinary_ids:
            storyboard.apply_event(ws_id, shot_id, 'approve_instructions')
        storyboard.approve_version(ws_id, version_id)
        print(f'storyboard {created["storyboard_id"]}, version {version_id}, '
             f'{len(ordinary_ids)} ordinary shots')

        ctx = orchestrator.build_context(ws_id, version_id)
        # --videos alone (no explicit --max-frames) must still get frames for as many
        # shots as it needs a clip for — a video can only animate a shot that already has
        # an approved frame. --render needs EVERY ordinary shot video_approved (that's
        # what validate_for_render checks), so it overrides both counts to cover all of
        # them rather than whatever --max-frames/--videos happened to say.
        if args.render:
            target_ids = list(ordinary_ids)
            video_target_ids = list(ordinary_ids)
        else:
            target_ids = ordinary_ids[:max(1, args.max_frames, args.videos)]
            video_target_ids = target_ids[:args.videos] if args.videos else []

        est = orchestrator.estimate(ws_id, version_id, 'frames')
        print(f'estimate: {est}')

        for shot_id in target_ids:
            shot = next(s for s in ctx['shots'] if str(s['id']) == shot_id)
            prompt = orchestrator.compose_frame_prompt(ctx, shot)
            refs = orchestrator.reference_images(ctx, shot)
            print(f'\n--- shot {shot_id} ---')
            print(f'references ({len(refs)}): {refs}')
            print(f'prompt: {prompt}')

        if args.videos or args.render:
            provider = video.get()
            print(f'\n--- video stage ({len(video_target_ids)} shot(s), '
                 f'{provider.backend}/{provider.model}) ---')
            video_est = orchestrator.estimate(ws_id, version_id, 'videos')
            print(f'video estimate (before any frame is approved — 0 eligible yet): '
                 f'{video_est}')
            for shot_id in video_target_ids:
                shot = next(s for s in ctx['shots'] if str(s['id']) == shot_id)
                motion_prompt, negative = orchestrator.compose_motion_prompt(ctx, shot)
                clip_seconds = orchestrator._clip_seconds(float(shot['duration']), provider)
                price = video.credits_for(clip_seconds, provider)
                print(f'\nshot {shot_id} (duration {shot["duration"]}s):')
                print(f'  clip_seconds: {clip_seconds}, credits: {price}')
                print(f'  motion prompt: {motion_prompt}')
                print(f'  negative: {negative}')

        if args.render:
            music_version = storyboard.get_version(ws_id, version_id)
            music_provider = music.get()
            plan = music.plan_chunks(music_version)
            music_seconds = music.seconds_for(music_version)
            music_price = music.credits_for(music_seconds, music_provider)
            print(f'\n--- music stage ({music_provider.backend}/{music_provider.model}) ---')
            print(f'composition plan ({len(plan)} chunk(s)):')
            for chunk in plan:
                print(f'  {chunk}')
            print(f'seconds: {music_seconds:.1f}, credits: {music_price}')

        if args.dry_run:
            print('\n--dry-run: stopping before any paid call.')
            return

        E2E_OUT.mkdir(parents=True, exist_ok=True)
        balance_before = credits.balance(ws_id)
        for shot_id in target_ids:
            job_id = orchestrator.start_frame(ws_id, shot_id, f'e2e-frame-{uuid.uuid4()}',
                                              user_id)
            print(f'shot {shot_id}: job {job_id} started')
            orchestrator.run_frame(job_id)          # synchronous — this script IS the worker

            job_row = db.query('SELECT status, error FROM jobs WHERE id = %s', (job_id,),
                               one=True)
            print(f'  status: {job_row["status"]}' + (f' ({job_row["error"]})'
                  if job_row['error'] else ''))
            assert job_row['status'] == 'succeeded', job_row

            refreshed = next(s for s in storyboard.get_version(ws_id, version_id)['shots']
                            if str(s['id']) == shot_id)
            assets = storyboard.list_assets(ws_id, refreshed['shot_key'], 'storyboard_image')
            latest = assets[-1]
            out_path = E2E_OUT / f'{shot_id}-v{latest["variant"]}.png'
            storage.fetch(latest['key'], out_path)
            prompt_path = E2E_OUT / f'{shot_id}-v{latest["variant"]}.prompt.txt'
            prompt_path.write_text(latest['prompt'])
            print(f'  saved {out_path}')
            print(f'  fidelity: {(latest["metadata"] or {}).get("fidelity")}')

            if shot_id in video_target_ids:
                storyboard.select_asset(ws_id, shot_id, latest['id'])
                storyboard.apply_event(ws_id, shot_id, 'approve_frame', asset_id=latest['id'])

        for shot_id in video_target_ids:
            video_job_id = orchestrator.start_video(ws_id, shot_id, f'e2e-video-{uuid.uuid4()}',
                                                     user_id)
            print(f'\nshot {shot_id}: video job {video_job_id} started')
            orchestrator.run_video_shot(video_job_id)   # synchronous — this script IS the worker

            vjob_row = db.query('SELECT status, error, reserved_credits, settled_credits '
                                'FROM jobs WHERE id = %s', (video_job_id,), one=True)
            print(f'  status: {vjob_row["status"]}' + (f' ({vjob_row["error"]})'
                  if vjob_row['error'] else ''))
            assert vjob_row['status'] == 'succeeded', vjob_row

            refreshed = next(s for s in storyboard.get_version(ws_id, version_id)['shots']
                            if str(s['id']) == shot_id)
            clips = storyboard.list_assets(ws_id, refreshed['shot_key'], 'video_clip')
            latest_clip = clips[-1]
            clip_settings = latest_clip['settings'] or {}
            clip_out = E2E_OUT / f'{shot_id}-clip-v{latest_clip["variant"]}.mp4'
            storage.fetch(latest_clip['key'], clip_out)
            print(f'  saved clip {clip_out}')
            print(f'  clip_seconds: {clip_settings.get("clip_seconds")}, '
                 f'credits charged: {vjob_row["settled_credits"]}')
            print(f'  fidelity: {(latest_clip["metadata"] or {}).get("fidelity")}')

            if args.render:
                # A render needs every ordinary shot at video_approved, not just
                # video_review — approve the clip run_video_shot just auto-selected.
                storyboard.apply_event(ws_id, shot_id, 'approve_video',
                                       asset_id=latest_clip['id'])

        if args.render:
            print('\n--- music + render stage (real) ---')
            music_est = orchestrator.estimate(ws_id, version_id, 'music')
            music_job_id = orchestrator.start_music(
                ws_id, version_id, music_est['credits'], f'e2e-music-{uuid.uuid4()}', user_id)
            print(f'music job {music_job_id} started, {music_est["credits"]} credit(s)')
            orchestrator.run_music(music_job_id)          # synchronous, cap 1 — one take
            music_job_row = db.query('SELECT status, error FROM jobs WHERE id = %s',
                                     (music_job_id,), one=True)
            print(f'  status: {music_job_row["status"]}' + (f' ({music_job_row["error"]})'
                  if music_job_row['error'] else ''))
            assert music_job_row['status'] == 'succeeded', music_job_row

            music_after = storyboard.music_state(ws_id, version_id)
            assert music_after['selected_id'], 'the take must have been auto-selected'
            storyboard.approve_music(ws_id, version_id, music_after['selected_id'])
            print(f'  music approved: asset {music_after["selected_id"]}')

            problems = orchestrator.validate_for_render(ws_id, version_id)
            print(f'validate_for_render: {problems or "ready"}')
            assert not problems, problems

            render_job_id = orchestrator.start_render(
                ws_id, version_id, f'e2e-render-{uuid.uuid4()}', user_id)
            print(f'render job {render_job_id} started')
            orchestrator.run_render(render_job_id)         # synchronous — real ffmpeg
            render_job_row = db.query('SELECT status, error FROM jobs WHERE id = %s',
                                      (render_job_id,), one=True)
            print(f'  status: {render_job_row["status"]}' + (f' ({render_job_row["error"]})'
                  if render_job_row['error'] else ''))
            assert render_job_row['status'] == 'succeeded', render_job_row

            final_row = db.query('SELECT * FROM final_renders WHERE job_id = %s',
                                 (render_job_id,), one=True)
            manifest = final_row['manifest'] or {}
            asset_row = db.query('SELECT key FROM generated_assets WHERE id = %s',
                                 (final_row['asset_id'],), one=True)
            final_local = E2E_OUT / f'{version_id}-final.mp4'
            storage.fetch(asset_row['key'], final_local)
            print(f'\nfinal render saved: {final_local}')
            print(f'  duration: {manifest.get("duration")}s, size: '
                 f'{final_local.stat().st_size} bytes')
            print(f'  resolution: {final_row["resolution"]}, aspect: '
                 f'{final_row["aspect_ratio"]}')
            print(f'  manifest: {json.dumps(manifest, indent=2, default=str)}')

        total, tail = credits.reconcile(ws_id)
        assert total == tail, (total, tail)
        print(f'\nbalance {balance_before} -> {credits.balance(ws_id)}; ledger reconciles '
             f'({total} == {tail})')
        print(f'outputs saved under {E2E_OUT}/')
        print('ads_e2e ok')

    finally:
        # Deletion order matters: credit_ledger.job_id and final_renders.job_id both
        # RESTRICT deleting a job they still reference, so both must go before jobs; a
        # real run once hit `credit_ledger_job_id_fkey` here because credit_ledger was
        # deleted AFTER jobs. Wrapped in its own try/finally so a future ordering mistake
        # still closes the pool (below) instead of leaking its worker thread on exit —
        # that leak is exactly the "couldn't stop thread 'pool-1-worker-0'" warning a real
        # run also hit, when this exception left db.close() unreached.
        try:
            db.query("""DELETE FROM final_renders WHERE campaign_id IN
                        (SELECT id FROM campaigns WHERE workspace_id = %s)""", (ws_id,))
            db.query('DELETE FROM approvals WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM generated_assets WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM jobs WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM campaigns WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM pieces WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM memberships WHERE workspace_id = %s', (ws_id,))
            db.query('DELETE FROM workspaces WHERE id = %s', (ws_id,))
            db.query('DELETE FROM sessions WHERE user_id = %s', (user_id,))
            db.query('DELETE FROM users WHERE id = %s', (user_id,))
        finally:
            db.close()


if __name__ == '__main__':
    main()
