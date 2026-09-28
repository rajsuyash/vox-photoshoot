"""Image-to-video backends, behind one interface — mirrors providers.py's shape.

Higgsfield and fal both expose Kling 3(.0) Pro image-to-video, with different argument
shapes (verified live, 2026-09-27): Higgsfield wants `sound` as the STRING 'off', a
plain int `duration`; fal wants `generate_audio` as a bool and `duration` as a STRING
enum. Getting either wrong is a paid call that fails after the queue has already
started, so the shapes are pinned here exactly as measured rather than guessed from
the docs.

    .venv/bin/python video.py out/some-still.png --category necklace \\
        --description "..." --location pondicherry --dry-run
"""

import argparse
import dataclasses
import json
import logging
import math
import pathlib
import subprocess
import sys
import tempfile
from typing import Callable

import credits
import hf
import locations
import motion as motion_module
import product

FFPROBE = 'ffprobe'        # on PATH in both dev (Homebrew) and prod (apt) images
FFMPEG = 'ffmpeg'          # on PATH in both dev (Homebrew) and prod (apt) images
OUT_DIR = pathlib.Path('out/videos')

DURATIONS = frozenset({5, 10})

# The still-to-video contract exposed to the rest of the app (jobs.py/app.py):
# supported output aspects and clip lengths, independent of any one provider's own
# vocabulary (a provider's `durations` on VideoProvider may be a subset of this).
VIDEO_ASPECTS = ('9:16', '4:5', '1:1', '16:9')
VIDEO_DURATIONS = (5, 10)


@dataclasses.dataclass(frozen=True)
class VideoProvider:
    backend: str
    model: str
    model_path: str
    usd_per_second: float       # list price
    durations: frozenset
    build_args: Callable        # (image_url, prompt, negative, duration) -> dict
    upload: Callable            # (path) -> url
    submit: Callable            # (model_path, arguments, on_progress) -> result dict


# --- argument builders, one per (backend, model), each shape verified live -------------

def _hf_kling_args(image_url: str, prompt: str, negative: str | None, duration: int) -> dict:
    return {
        'image_url': image_url,
        'prompt': prompt,
        'negative_prompt': negative or '',
        'duration': duration,
        'sound': 'off',           # a string, not a bool — verified against this account
        'cfg_scale': 0.5,
    }


def _hf_seedance_args(image_url: str, prompt: str, _negative: str | None,
                      duration: int) -> dict:
    return {
        'image_url': image_url,
        'prompt': prompt,
        'duration': duration,
        'resolution': '720p',
        'generate_audio': False,
    }


def _fal_kling_args(image_url: str, prompt: str, negative: str | None,
                    duration: int) -> dict:
    return {
        'start_image_url': image_url,
        'prompt': prompt,
        'negative_prompt': negative or '',
        'duration': str(duration),   # fal's schema has this as a string enum, not an int
        'generate_audio': False,
        'cfg_scale': 0.5,
    }


# --- upload / submit, one pair per backend ---------------------------------------------

def _fal_credentials() -> None:
    import os
    if not os.environ.get('FAL_KEY'):
        raise RuntimeError('FAL_KEY not set')


def _fal_upload(path) -> str:
    import fal_client

    _fal_credentials()
    return fal_client.upload_file(str(path))


def _hf_submit(model_path: str, arguments: dict, on_progress=None) -> dict:
    import higgsfield_client

    return higgsfield_client.subscribe(model_path, arguments=arguments,
                                       on_queue_update=on_progress)


def _fal_submit(model_path: str, arguments: dict, on_progress=None) -> dict:
    import fal_client

    _fal_credentials()
    return fal_client.subscribe(model_path, arguments=arguments, with_logs=False,
                                on_queue_update=on_progress)


# List prices, USD/second, sound/audio off. Kling 3.0 Pro is $0.112/s on both backends —
# Higgsfield's current $0.0616/s is a promo ending 2026-10-01, never priced from.
# Seedance 2.5 720p ($0.4622/s) is kept here for the price table only; it is not a
# default model and fal has no verified Seedance argument shape yet, so only the
# Higgsfield entry is registered.
KLING_USD_PER_SECOND = 0.112
SEEDANCE_USD_PER_SECOND = 0.4622

REGISTRY: dict[tuple[str, str], VideoProvider] = {
    ('higgsfield', 'kling'): VideoProvider(
        backend='higgsfield', model='kling',
        model_path='kling-video/v3.0/pro/image-to-video',
        usd_per_second=KLING_USD_PER_SECOND, durations=DURATIONS,
        build_args=_hf_kling_args, upload=hf.upload, submit=_hf_submit,
    ),
    ('higgsfield', 'seedance'): VideoProvider(
        backend='higgsfield', model='seedance',
        model_path='bytedance/seedance-2.5/image-to-video',
        usd_per_second=SEEDANCE_USD_PER_SECOND, durations=DURATIONS,
        build_args=_hf_seedance_args, upload=hf.upload, submit=_hf_submit,
    ),
    ('fal', 'kling'): VideoProvider(
        backend='fal', model='kling',
        model_path='fal-ai/kling-video/v3/pro/image-to-video',
        usd_per_second=KLING_USD_PER_SECOND, durations=DURATIONS,
        build_args=_fal_kling_args, upload=_fal_upload, submit=_fal_submit,
    ),
}


def get(backend: str | None = None, model: str | None = None) -> VideoProvider:
    """Resolve a provider by (backend, model), or from VIDEO_BACKEND/VIDEO_MODEL."""
    import os

    backend = backend or os.environ.get('VIDEO_BACKEND', 'higgsfield')
    model = model or os.environ.get('VIDEO_MODEL', 'kling')
    key = (backend, model)
    if key not in REGISTRY:
        raise KeyError(f'unknown backend/model {key!r}; have {sorted(REGISTRY)}')
    return REGISTRY[key]


def credits_for(seconds: int, provider: VideoProvider) -> int:
    """usd_per_second x seconds, in credits, rounded up — a partial credit is still a
    full credit of provider cost. Kling 5s -> 4, 10s -> 8 at credits.USD_PER_CREDIT."""
    return math.ceil(provider.usd_per_second * seconds / credits.USD_PER_CREDIT)


# --- reframe: fit a still to the output aspect before any video call -------------------

REFRAME_PROMPT = (
    'Extend this exact photograph to fill a {aspect} frame. Keep everything already '
    'visible identical: the same woman, face, pose, clothing, jewellery, lighting and '
    'background. Only add plausible continuation of the background and clothing at the '
    'new edges. No text.'
)


def needs_reframe(still_path, aspect: str) -> bool:
    """Whether `still_path` needs a paid reframe call to fill `aspect`.

    False when the still's own shape (nearest supported ratio, same as retouching
    uses) already equals `aspect` — a video call never needs to pay for a no-op edit.
    """
    from PIL import Image

    import providers

    if aspect not in VIDEO_ASPECTS:
        raise ValueError(f'unknown aspect {aspect!r}; use one of {VIDEO_ASPECTS}')
    with Image.open(still_path) as image:
        width, height = image.size
    return providers.get().nearest_aspect(width, height) != aspect


def reframe(still_path, aspect: str, out_dir) -> pathlib.Path:
    """Extend a still to `aspect` with one paid Nano Banana Pro edit call, or hand
    `still_path` straight back when it already fits — never pay to reframe a no-op.

    A video call needs the source still in the SAME aspect it will render, and a
    retouched product photo carries whatever shape the client shot it in.
    """
    still_path = pathlib.Path(still_path)
    if aspect not in VIDEO_ASPECTS:
        raise ValueError(f'unknown aspect {aspect!r}; use one of {VIDEO_ASPECTS}')
    if not needs_reframe(still_path, aspect):
        return still_path

    import providers

    provider = providers.get()
    image_url = provider.upload(still_path)
    urls = provider.generate(REFRAME_PROMPT.format(aspect=aspect), image_urls=[image_url],
                             aspect_ratio=aspect, quality='high')
    prefix = f'{still_path.stem}-reframe-{aspect.replace(":", "x")}'
    saved = hf.download(urls[:1], out_dir, prefix=prefix)
    return saved[0]


# --- director: one Anthropic call per still, forced into our own vocabulary -----------

def _director_schema(category_key: str) -> dict:
    motions = motion_module.MOTIONS.get(
        category_key, motion_module.MOTIONS[motion_module.FALLBACK_CATEGORY])
    return {
        'type': 'object',
        'properties': {
            'motion': {'type': 'string', 'enum': list(motions)},
            'camera': {'type': 'string', 'enum': list(motion_module.CAMERAS)},
            'mood': {'type': 'string', 'enum': list(motion_module.MOODS)},
            'pace': {'type': 'string', 'enum': list(motion_module.PACE)},
            'action': {'type': 'string', 'description': (
                'ONE third-person, present-tense sentence about her, e.g. "she turns '
                'her wrist toward the light" — never an instruction addressed to her '
                '(never "turn...", "lift..."). '
                f'{motion_module.MAX_ACTION_WORDS} words or fewer. It must add '
                "something the chosen motion's own prose does not already say — often "
                'what the light does on the piece — not restate the motion.'
            )},
            'piece_visible': {'type': 'string',
                              'enum': list(motion_module.PIECE_VISIBLE)},
        },
        'required': ['motion', 'camera', 'mood', 'pace', 'action', 'piece_visible'],
        'additionalProperties': False,
    }


MAX_NOTE_CHARS = 140


def _ask_director(still_path, category_key: str, description: str, location_key: str,
                  framing: str, note: str) -> dict:
    """One Anthropic call, same pattern as app._ask_for_composition and
    product.identify: a fixed JSON schema, so the model can only answer in our
    vocabulary. `note` is the jeweller's own instruction for the video and is used
    ONLY here, in the director's own prompt — it never reaches motion.render()."""
    import anthropic

    place = locations.ALL.get(location_key)
    scene = f'{place.scene} Lighting: {place.light}.' if place else 'a studio setting'
    note_clause = f' The jeweller left this note for the video: {note!r}.' if note else ''
    reply = anthropic.Anthropic().messages.create(
        model=product.VISION_MODEL,
        max_tokens=300,
        system=(
            'You are directing a short video ad animating one still jewellery '
            'photograph. First judge from the image itself whether the piece already '
            "faces the camera and is fully visible ('clear'), only partly visible "
            "('partial'), or not visible at all ('hidden') — this decides whether the "
            'motion must turn it into view by the end. Then choose the motion, camera, '
            'mood and pace that keep the piece visible, her whole face in frame, and '
            'in focus by the end. For action, write ONE third-person, present-tense '
            'sentence about her — "she turns...", never an instruction addressed to '
            f'her ("turn...", "lift..."), {motion_module.MAX_ACTION_WORDS} words or '
            'fewer — describing something the chosen motion does not already say, '
            'often what the light does on the piece.'
        ),
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image',
                 'source': {'type': 'base64', 'media_type': product.VISION_MEDIA_TYPE,
                            'data': product.encode(still_path)}},
                {'type': 'text', 'text': (
                    f'This is the {framing} still for a {category_key} jewellery video '
                    f'ad: {description or "unspecified"}. Location: {scene}'
                    f'{note_clause}')},
            ],
        }],
        output_config={'format': {'type': 'json_schema',
                                  'schema': _director_schema(category_key)}},
    )
    text = next(block.text for block in reply.content if block.type == 'text')
    return json.loads(text)


def direct(still_path, category_key: str, description: str, location_key: str,
          framing: str, duration: int, motion: str = '', mood: str = '',
          note: str = '') -> motion_module.Plan:
    """Direct one still into a motion.Plan. Never raises: a director call that fails
    for any reason (network, malformed JSON, a missing key) falls back to a default
    Plan with the reveal rule forced — on a failure we genuinely do not know whether
    the piece is visible, so 'hidden' is the honest default, not 'clear'.

    `motion` / `mood`, if given, are the jeweller's own choice and short-circuit the
    director for just that field — the shot they picked is not up for a model to
    override, the same way composition.py lets a client's own pose win. A client
    choice also opts out of the auto-reveal safety net (that net exists for the
    director's own uncertain guess, not for a shot the jeweller deliberately picked).
    """
    note = (note or '').strip()[:MAX_NOTE_CHARS]
    try:
        raw = _ask_director(still_path, category_key, description, location_key,
                            framing, note)
    except Exception as error:                      # noqa: BLE001 - a director call is optional
        print(f'video.direct director call failed ({error!r}); falling back to a '
             f'default plan for {category_key}', flush=True)
        raw = {'piece_visible': 'hidden'}
    if motion:
        raw['motion'] = motion
        raw['piece_visible'] = 'clear'
    if mood:
        raw['mood'] = mood
    return motion_module.parse(raw, category_key)


# --- generation (paid) ------------------------------------------------------------------

def _extract_video_url(result: dict) -> str | None:
    """Tolerant extractor: tries the known shapes, then walks the dict for an mp4 URL."""
    video_field = result.get('video')
    if isinstance(video_field, dict) and video_field.get('url'):
        return video_field['url']
    videos = result.get('videos')
    if isinstance(videos, list) and videos and isinstance(videos[0], dict):
        if videos[0].get('url'):
            return videos[0]['url']

    def walk(node):
        if isinstance(node, str):
            if node.startswith('https://') and node.split('?')[0].endswith('.mp4'):
                return node
            return None
        if isinstance(node, dict):
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found:
                    return found
        return None

    return walk(result)


TERMINAL_FAILURE_STATUSES = ('failed', 'error', 'nsfw')


def generate(still_path, prompt: str, negative: str | None, duration: int,
            provider: VideoProvider, on_progress=None) -> str:
    """Submit one video generation and return the output URL. Paid — never call this
    from --dry-run."""
    if duration not in provider.durations:
        raise ValueError(f'{provider.backend}/{provider.model} does not support '
                         f'duration {duration}; has {sorted(provider.durations)}')

    image_url = provider.upload(still_path)
    arguments = provider.build_args(image_url, prompt, negative, duration)
    result = provider.submit(provider.model_path, arguments, on_progress)

    status = str(result.get('status', '')).lower()
    if status in TERMINAL_FAILURE_STATUSES:
        raise RuntimeError(f'generation ended in status {status!r}: {result!r}'[:400])

    url = _extract_video_url(result)
    if not url:
        raise RuntimeError(f'no video url found in result: {result!r}'[:400])
    return url


def _probe(path: pathlib.Path) -> dict:
    out = subprocess.run(
        [FFPROBE, '-v', 'error', '-print_format', 'json', '-show_format',
         '-show_streams', str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), {})
    return {
        'width': video_stream.get('width'),
        'height': video_stream.get('height'),
        'duration': float(data.get('format', {}).get('duration', 0)),
    }


# --- fidelity check: did the render actually keep its promises? ------------------------

def _extract_frame(mp4_path, timestamp: float, out_path: pathlib.Path) -> None:
    subprocess.run(
        [FFMPEG, '-y', '-ss', f'{max(timestamp, 0.0):.3f}', '-i', str(mp4_path),
         '-frames:v', '1', str(out_path)],
        capture_output=True, check=True)


FIDELITY_SCHEMA = {
    'type': 'object',
    'properties': {
        'piece_in_still': {'type': 'string',
                           'description': ('Shape/outline, metal colour and stone '
                                          'layout of the piece as seen in Image 1.')},
        'piece_at_end': {'type': 'string',
                         'description': ('Same, but as seen in Image 3 (the clip\'s '
                                        'final frame).')},
        'same_design': {'type': 'boolean'},
        'piece_visible_at_end': {'type': 'boolean'},
        'face_in_frame_at_end': {'type': 'boolean'},
        'reason': {'type': 'string'},
    },
    'required': ['piece_in_still', 'piece_at_end', 'same_design', 'piece_visible_at_end',
                'face_in_frame_at_end', 'reason'],
    'additionalProperties': False,
}

# Measured 2026-09-27 against 5 labelled cases (3 known-bad: round pendant rendered
# as heart x2, face cropped at end; 2 known-good), 2 runs each: claude-haiku-4-5 and
# claude-sonnet-5 gave IDENTICAL same_design/ok verdicts on all 10 calls — the bigger
# model buys no extra accuracy here, so the cheaper one is used. 4/5 cases matched
# their expected label exactly both runs; the 5th (a "yellow gold" earring pair) was
# flagged false by both models, both runs, over a metal-colour shift a manual crop
# confirmed is real (the still's mount reads yellow gold, the clip's final frame
# reads white/rose-silver) — see this change's own report for detail; that is not
# evidence against either model, since neither model disagreed with the other.
FIDELITY_MODEL = 'claude-haiku-4-5'


def check_fidelity(still_path, mp4_path, description: str = '') -> tuple[bool, str]:
    """Did the rendered clip keep motion.FIDELITY_LOCK's promises? One cheap Anthropic
    call comparing the source still against two frames pulled from the clip (its
    midpoint, and its last moment) — catches a clip that swapped the piece (e.g. a
    round pendant rendered as a heart), hid it again by the end, or cropped her face
    out of frame (the 2026-09-27 spike's own failures).

    The three images are explicitly labelled and anchored against Image 1 (the
    approved still) rather than compared only against each other — a clip that is
    internally consistent frame-to-frame but drifted away from the still (or from the
    piece's own text description) is exactly the false pass this replaced.

    Never raises: this runs after a paid generation has already succeeded, so a
    checker outage (ffmpeg missing, the API down, a malformed reply) must never fail
    that job. It degrades to (True, 'fidelity check unavailable: ...') and a logged
    warning instead.
    """
    try:
        info = _probe(pathlib.Path(mp4_path))
        duration = info['duration'] or 0.0
        midpoint = duration / 2
        near_end = max(duration - 0.1, 0.0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = pathlib.Path(tmp_dir)
            mid_frame = tmp_dir / 'mid.jpg'
            end_frame = tmp_dir / 'end.jpg'
            _extract_frame(mp4_path, midpoint, mid_frame)
            _extract_frame(mp4_path, near_end, end_frame)

            import anthropic

            def image_block(path):
                return {'type': 'image',
                       'source': {'type': 'base64', 'media_type': product.VISION_MEDIA_TYPE,
                                  'data': product.encode(path)}}

            piece_clause = f'The piece is: {description}.' if description else ''
            reply = anthropic.Anthropic().messages.create(
                model=FIDELITY_MODEL,
                max_tokens=400,
                system=(
                    'You are a strict jewellery QC inspector. Your job is to catch a '
                    'video render that silently changed the piece — a different '
                    'silhouette (e.g. round became heart-shaped), a different metal, '
                    'or a different stone layout — even if the video looks smooth and '
                    'internally consistent frame to frame. When unsure whether the '
                    'final frame shows the same piece as the approved still, answer '
                    'same_design=false: a false pass ships a wrong ad, a false fail '
                    'only costs one retry.'
                ),
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': (
                            'Image 1 — the APPROVED STILL (ground truth).'
                        )},
                        image_block(still_path),
                        {'type': 'text', 'text': "Image 2 — the video's middle frame."},
                        image_block(mid_frame),
                        {'type': 'text', 'text': "Image 3 — the video's FINAL frame."},
                        image_block(end_frame),
                        {'type': 'text', 'text': (
                            f'{piece_clause} Describe the piece\'s shape/outline, metal '
                            'colour and stone layout as seen in Image 1 (piece_in_still) '
                            'and as seen in Image 3 (piece_at_end). same_design: does '
                            "Image 3's piece have the same outline/shape, metal colour "
                            'and stone layout as Image 1, AND does it match the text '
                            'description above? A changed silhouette (e.g. round became '
                            'heart-shaped) is false even if the video is internally '
                            'consistent frame-to-frame. piece_visible_at_end: is the '
                            'piece clearly visible and in focus in Image 3? '
                            'face_in_frame_at_end: is her whole face inside the frame '
                            'in Image 3, not cropped out? Give a one-sentence reason.'
                        )},
                    ],
                }],
                output_config={'format': {'type': 'json_schema',
                                          'schema': FIDELITY_SCHEMA}},
            )
            text = next(block.text for block in reply.content if block.type == 'text')
            answer = json.loads(text)

        ok = bool(answer['same_design'] and answer['piece_visible_at_end']
                  and answer['face_in_frame_at_end'])
        return ok, answer['reason']
    except Exception as error:                      # noqa: BLE001 - never fail a paid job
        logging.warning('video.check_fidelity unavailable: %r', error)
        return True, f'fidelity check unavailable: {error!r}'


# --- run: the full still -> video orchestration, one call per shot ---------------------

def run(still_path, category_key: str, description: str, location_key: str,
       framing: str, duration: int, aspect: str, *, motion: str = '', mood: str = '',
       note: str = '', provider: VideoProvider | None = None, out_dir,
       on_progress=None) -> dict:
    """Direct a still into a Plan, render its prompt, generate, and download the mp4.

    `still_path` is assumed already reframed to `aspect` (the caller reframes first,
    via `reframe` — this never reframes and never runs `check_fidelity`, both of
    which are the caller's decision, not this function's).
    """
    provider = provider or get()
    if duration not in provider.durations:
        raise ValueError(f'{provider.backend}/{provider.model} does not support '
                         f'duration {duration}; has {sorted(provider.durations)}')
    if aspect not in VIDEO_ASPECTS:
        raise ValueError(f'unknown aspect {aspect!r}; use one of {VIDEO_ASPECTS}')

    plan = direct(still_path, category_key, description, location_key, framing, duration,
                 motion=motion, mood=mood, note=note)
    prompt, negative = motion_module.render(plan, provider.model, duration, description,
                                           location_key)
    url = generate(still_path, prompt, negative, duration, provider,
                  on_progress=on_progress)

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(still_path).stem
    out_path = out_dir / f'{stem}-{provider.backend}-{provider.model}-{duration}s.mp4'
    out_path.write_bytes(hf._fetch_bytes(url))

    info = _probe(out_path)
    return {
        'path': out_path,
        'prompt': prompt,
        'negative': negative,
        'plan': dataclasses.asdict(plan),
        'provider': f'{provider.backend}/{provider.model}',
        'width': info['width'],
        'height': info['height'],
        'duration': info['duration'],
    }


# --- CLI ---------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('still')
    parser.add_argument('--category', required=True, choices=sorted(product.CATEGORIES))
    parser.add_argument('--description', default='')
    parser.add_argument('--location', required=True)
    parser.add_argument('--framing', default='hero')
    parser.add_argument('--duration', type=int, default=5)
    parser.add_argument('--backend', default=None)
    parser.add_argument('--model', default=None)
    parser.add_argument('--note', default='')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    provider = get(args.backend, args.model)
    if args.duration not in provider.durations:
        sys.exit(f'{provider.backend}/{provider.model} does not support duration '
                 f'{args.duration}; has {sorted(provider.durations)}')

    plan = direct(args.still, args.category, args.description, args.location,
                 args.framing, args.duration, note=args.note)
    prompt, negative = motion_module.render(plan, provider.model, args.duration,
                                           args.description, args.location)
    price = credits_for(args.duration, provider)

    print(f'plan: {plan}')
    print(f'\nprompt ({len(prompt)} chars):\n{prompt}')
    print(f'\nnegative: {negative}' if negative else '\nnegative: (none)')
    print(f'\ncredits: {price} ({provider.backend}/{provider.model}, '
         f'${provider.usd_per_second:.4f}/s x {args.duration}s)')

    if args.dry_run:
        if provider.backend == 'higgsfield':
            image_url = provider.upload(args.still)           # free upload
            hf_args = provider.build_args(image_url, prompt, negative, args.duration)
            estimate = hf.estimate(f'/{provider.model_path}', hf_args)
            print(f'\nhf.estimate: {estimate}')
        print('\n--dry-run: stopping before any paid call.')
        return

    url = generate(args.still, prompt, negative, args.duration, provider,
                   on_progress=lambda status: print(f'  status: {status}',
                                                     file=sys.stderr))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(args.still).stem
    out_path = OUT_DIR / f'{stem}-{provider.backend}-{provider.model}-{args.duration}s.mp4'
    out_path.write_bytes(hf._fetch_bytes(url))
    print(f'\nsaved {out_path}: {_probe(out_path)}')


def demo() -> None:
    """Self-check: registry resolution, argument shapes, credit pricing. No network."""
    import os

    assert get('higgsfield', 'kling').model_path == 'kling-video/v3.0/pro/image-to-video'
    assert get('fal', 'kling').model_path == 'fal-ai/kling-video/v3/pro/image-to-video'

    # Env-driven resolution, defaults to higgsfield/kling.
    os.environ.pop('VIDEO_BACKEND', None)
    os.environ.pop('VIDEO_MODEL', None)
    assert get().backend == 'higgsfield' and get().model == 'kling'
    os.environ['VIDEO_BACKEND'] = 'fal'
    assert get().backend == 'fal' and get().model == 'kling'
    del os.environ['VIDEO_BACKEND']

    for bad in (('nope', 'kling'), ('fal', 'seedance'), ('higgsfield', 'nope')):
        try:
            get(*bad)
        except KeyError:
            pass
        else:
            raise AssertionError(f'{bad} should be rejected')

    # Argument shapes, exactly as measured live (2026-09-27) — the one place a wrong
    # type here is a paid call failing mid-queue rather than a local error.
    hf_kling = get('higgsfield', 'kling').build_args('https://x/img.png', 'p', 'n', 5)
    assert hf_kling['sound'] == 'off' and isinstance(hf_kling['sound'], str)
    assert hf_kling['duration'] == 5 and isinstance(hf_kling['duration'], int)
    assert hf_kling['cfg_scale'] == 0.5

    fal_kling = get('fal', 'kling').build_args('https://x/img.png', 'p', 'n', 5)
    assert fal_kling['duration'] == '5' and isinstance(fal_kling['duration'], str)
    assert fal_kling['generate_audio'] is False
    assert 'sound' not in fal_kling, 'fal uses generate_audio, not sound'

    hf_seedance = get('higgsfield', 'seedance').build_args('https://x/img.png', 'p',
                                                           None, 10)
    assert hf_seedance['resolution'] == '720p'
    assert hf_seedance['generate_audio'] is False

    # Bare binary names only — a hardcoded path (e.g. /opt/homebrew/...) would work on
    # macOS dev but not the prod Docker image, which resolves these via PATH instead.
    assert '/' not in FFPROBE, 'FFPROBE must be a bare name resolved via PATH'
    assert '/' not in FFMPEG, 'FFMPEG must be a bare name resolved via PATH'

    # credits_for: Kling 5s -> 4, 10s -> 8 at $0.112/s and $0.15/credit.
    kling = get('higgsfield', 'kling')
    assert credits_for(5, kling) == 4, credits_for(5, kling)
    assert credits_for(10, kling) == 8, credits_for(10, kling)
    # A cheap-enough fractional case must still round UP, never down.
    assert credits_for(1, kling) == math.ceil(0.112 / credits.USD_PER_CREDIT)

    # generate() must reject an unsupported duration before uploading anything.
    try:
        generate('nonexistent.png', 'p', 'n', 7, kling)
    except ValueError as error:
        assert '7' in str(error)
    else:
        raise AssertionError('an unsupported duration should be rejected')

    # direct() must never raise, even when the director call is guaranteed to fail
    # (no ANTHROPIC_API_KEY reachable / a bogus path) — it must fall back to a Plan
    # with the reveal rule forced, since a failure means the piece's visibility is
    # genuinely unknown.
    plan = direct('nonexistent-still.png', 'necklace', 'a necklace', 'pondicherry',
                  'hero', 5)
    assert motion_module.MOTIONS['necklace'][plan.motion].reveals, plan

    # A jeweller's own motion/mood choice overrides the (failed) director's.
    plan2 = direct('nonexistent-still.png', 'ring', 'a ring', 'taj-mahal', 'hero', 5,
                   motion='fabric-brush', mood='festive')
    assert plan2.motion == 'fabric-brush' and plan2.mood == 'festive'

    # --- needs_reframe / reframe: no network -----------------------------------------
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = pathlib.Path(tmp_dir)
        still_3x4 = tmp_dir / 'still-3x4.png'
        still_9x16 = tmp_dir / 'still-9x16.png'
        Image.new('RGB', (900, 1200), 'red').save(still_3x4)    # 3:4
        Image.new('RGB', (900, 1600), 'red').save(still_9x16)   # 9:16

        # A still already shaped like the target aspect needs no reframe call; one
        # that isn't does.
        assert needs_reframe(still_3x4, '9:16') is True
        assert needs_reframe(still_9x16, '9:16') is False

        for bad_aspect in ('3:4', '21:9', ''):
            try:
                needs_reframe(still_3x4, bad_aspect)
            except ValueError as error:
                assert bad_aspect in str(error) or not bad_aspect
            else:
                raise AssertionError(f'{bad_aspect!r} should be rejected')

        # reframe() validates the aspect before ever touching the network.
        try:
            reframe(still_3x4, 'bad-aspect', tmp_dir)
        except ValueError as error:
            assert 'bad-aspect' in str(error)
        else:
            raise AssertionError('an unsupported aspect should be rejected')

        # A still that already fits is handed straight back — no paid call.
        assert reframe(still_9x16, '9:16', tmp_dir) == still_9x16

    # --- check_fidelity: a checker outage (here, a missing clip) must degrade, ------
    # never raise, since it runs after a paid generation has already succeeded.
    ok, reason = check_fidelity('nonexistent-still.png', 'nonexistent.mp4')
    assert ok is True
    assert 'fidelity check unavailable' in reason, reason

    print('video ok')


if __name__ == '__main__':
    main()
