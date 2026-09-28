"""End-to-end check of a REAL, PAID video-ad run: real Higgsfield/fal generation calls
and real Anthropic director/fidelity calls, against a throwaway local Postgres.

Nothing here is stubbed — this is the one script in the project that is allowed to
spend real provider money, which is exactly why it refuses to run against anything but
a local database first.

    docker run -d --name vox-e2e-pg -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=donna \\
        -p 55435:5432 postgres:16
    DATABASE_URL='postgresql://postgres:pg@localhost:55435/donna?sslmode=disable' \\
        .venv/bin/python video_e2e.py --dry-run     # quote only, no paid call
    DATABASE_URL='...' .venv/bin/python video_e2e.py             # the real thing
"""

import argparse
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
import time
import uuid

from fastapi.testclient import TestClient   # noqa: E402
from PIL import Image                       # noqa: E402

import admin      # noqa: E402
import app as app_module   # noqa: E402
import auth       # noqa: E402
import credits    # noqa: E402
import db         # noqa: E402
import jobs       # noqa: E402
import montage    # noqa: E402
import storage    # noqa: E402
import video      # noqa: E402

SOURCE_STILL = pathlib.Path(
    'out/video-spike/sources/812d3d2c-8f96-4af9-be01-710d7e9b5da9-hero.png')
E2E_OUT = pathlib.Path('out/videos/e2e')

POLL_TIMEOUT_S = 15 * 60
POLL_EVERY_S = 20

TARGET_RATIO_9X16 = 9 / 16


def _extract_frame(mp4_path, fraction: float, out_path: pathlib.Path) -> pathlib.Path:
    """One frame at `fraction` through the clip. Reuses video.py's own ffprobe/ffmpeg
    plumbing rather than re-implementing it."""
    info = video._probe(mp4_path)
    # Pull the very last frame a hair early — asking ffmpeg for the exact end timestamp
    # of a clip frequently seeks past the last decodable frame and returns nothing.
    timestamp = max(0.0, info['duration'] * fraction - (0.05 if fraction >= 1 else 0))
    video._extract_frame(mp4_path, timestamp, out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='provision and quote only; stop before the paid POST')
    args = parser.parse_args()

    assert SOURCE_STILL.exists(), f'source still missing: {SOURCE_STILL}'
    db.migrate()

    ws = admin.create_workspace(f'video-e2e-{uuid.uuid4().hex[:8]}')
    ws_id = str(ws['id'])
    account = admin.create_account(f'e2e-{uuid.uuid4().hex[:8]}@test', ws_id, 'owner')
    user_id = str(account['id'])
    credits.grant(ws_id, 20, 'e2e-fund')
    print(f'workspace {ws_id}, user {user_id}, balance {credits.balance(ws_id)}')

    client = TestClient(app_module.app)
    token = auth.start_session(user_id, ws_id)
    client.cookies.set(auth.COOKIE, token)

    try:
        uploads = pathlib.Path('out/uploads')
        uploads.mkdir(parents=True, exist_ok=True)
        piece_id = f'e2e{uuid.uuid4().hex[:10]}'
        still_key = f'shoots/{piece_id}/hero-1.png'
        storage.put(SOURCE_STILL, still_key)          # local storage, S3_BUCKET unset

        with db.tx() as conn:
            shoot = jobs.create(
                ws_id, user_id, 'shoot', f'e2e-shoot-{uuid.uuid4()}',
                {'category': 'necklace',
                 'description': 'rose gold circular pendant necklace with diamond pave '
                                'and chain',
                 'location': 'pondicherry', 'framing': 'hero'},
                piece_id=piece_id, reserved_credits=1, conn=conn)
            credits.reserve(conn, ws_id, str(shoot['id']), 1)
        shoot_id = str(shoot['id'])
        assert jobs.claim(shoot_id)
        jobs.add_image(shoot_id, shoot_id, 'hero', 1, still_key, 1)
        jobs.finish(shoot_id, 'succeeded', settled_credits=1)
        credits.settle(shoot_id, delivered=1)

        quote = client.get('/api/videos/quote', params={
            'source_job_id': shoot_id, 'framing': 'hero', 'attempt': '1',
            'aspect': '9:16', 'duration': '5'}).json()
        print(f'quote: {quote}')
        assert quote['needs_reframe'] is True, quote
        assert quote['price'] == 5, quote          # 4 (5s Kling) + 1 (reframe)

        if args.dry_run:
            print('--dry-run: stopping before the paid POST /api/videos.')
            return

        balance_before = credits.balance(ws_id)
        resp = client.post('/api/videos', data={
            'job_id': shoot_id, 'framing': 'hero', 'attempt': '1', 'aspect': '9:16',
            'duration': '5', 'motion': '', 'mood': '', 'note': '',
            'idempotency_key': f'e2e-video-{uuid.uuid4()}'})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        video_job_id = body['job_id']
        assert body['expected'] == 5, body
        print(f'video job {video_job_id} started, expected price {body["expected"]}')

        deadline = time.monotonic() + POLL_TIMEOUT_S
        last_print = 0.0
        detail = client.get(f'/api/videos/{video_job_id}').json()
        while detail['status'] in ('queued', 'running'):
            if time.monotonic() > deadline:
                raise TimeoutError(f'video {video_job_id} did not finish within '
                                   f'{POLL_TIMEOUT_S}s: {detail}')
            if time.monotonic() - last_print >= POLL_EVERY_S:
                print(f'  status: {detail["status"]}')
                last_print = time.monotonic()
            time.sleep(2)
            detail = client.get(f'/api/videos/{video_job_id}').json()

        print(f'final status: {detail["status"]}')
        assert detail['status'] == 'succeeded', detail

        assert credits.balance(ws_id) == balance_before - 5, \
            (credits.balance(ws_id), balance_before)
        total, tail = credits.reconcile(ws_id)
        assert total == tail, (total, tail)
        print(f'balance dropped by exactly 5; ledger reconciles ({total} == {tail})')

        row = db.query('SELECT * FROM job_videos WHERE job_id = %s', (video_job_id,),
                       one=True)
        assert row is not None, 'no job_videos row for the finished video'

        E2E_OUT.mkdir(parents=True, exist_ok=True)
        clean_path = E2E_OUT / 'clean.mp4'
        storage.fetch(row['key'], clean_path)
        info = video._probe(clean_path)
        ratio = info['width'] / info['height']
        assert abs(ratio - TARGET_RATIO_9X16) / TARGET_RATIO_9X16 <= 0.02, (info, ratio)
        assert 4.9 <= info['duration'] <= 5.2, info
        print(f'clean mp4: {info}, aspect {ratio:.4f} (target {TARGET_RATIO_9X16:.4f})')

        reframe_row = jobs.image_at(shoot_id, 'reframe-9x16-hero-1', 1)
        assert reframe_row is not None, 'no reframe job_images row'
        reframe_path = E2E_OUT / 'reframe.png'
        storage.fetch(reframe_row['s3_key'], reframe_path)
        with Image.open(reframe_path) as reframed:
            reframed_size = reframed.size
        r_ratio = reframed_size[0] / reframed_size[1]
        assert abs(r_ratio - TARGET_RATIO_9X16) / TARGET_RATIO_9X16 <= 0.02, \
            (reframed_size, r_ratio)
        print(f'reframe still: {reframed_size}, aspect {r_ratio:.4f}')

        job_row = db.query('SELECT params FROM jobs WHERE id = %s', (video_job_id,),
                           one=True)
        print(f'fidelity verdicts: {job_row["params"].get("fidelity")}')
        print(f'prompt: {job_row["params"].get("prompt")!r}')

        items = []
        for pct in (0, 50, 95):
            frame_path = E2E_OUT / f'clean-{pct}.jpg'
            _extract_frame(clean_path, pct / 100, frame_path)
            items.append((f'clean {pct}%', frame_path))
        sheet = montage.build(items, E2E_OUT / 'contact-sheet.jpg', columns=3)
        print(f'contact sheet: {sheet}')

        print('video_e2e ok')

    finally:
        db.query('DELETE FROM credit_ledger WHERE workspace_id = %s', (ws_id,))
        db.query('DELETE FROM jobs WHERE workspace_id = %s', (ws_id,))
        db.query('DELETE FROM memberships WHERE workspace_id = %s', (ws_id,))
        db.query('DELETE FROM workspaces WHERE id = %s', (ws_id,))
        db.query('DELETE FROM sessions WHERE user_id = %s', (user_id,))
        db.query('DELETE FROM users WHERE id = %s', (user_id,))
        db.close()


if __name__ == '__main__':
    main()
