"""Music bed generation for storyboard-driven video ads — mirrors video.py's provider
shape (a frozen-dataclass REGISTRY + get()) for the one backend/model verified so far.

Endpoint verified live against fal's own OpenAPI schema (2026-09-29):
    curl 'https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=elevenlabs/music/v2.5'
`elevenlabs/music/v2.5`, $0.60/minute. Two mutually exclusive input shapes:
  - prompt (<=4100 chars) + music_length_ms (3000-600000) + force_instrumental
  - composition_plan: {chunks: [...]}  (1-30 chunks, each 3000-120000ms)
`seed` and `composition_plan` go together; `force_instrumental` is documented "can only
be used with prompt" — so a composition_plan call never sends it, and relies on each
chunk's own negative_styles=["vocals","lyrics","speech"] to stay instrumental instead.
Output: {'audio': {'url': ..., 'content_type': 'audio/mpeg', ...}} (MP3).

    .venv/bin/python music.py --dry-run version.json     # no network, prints arguments
"""

import argparse
import dataclasses
import json
import math
import pathlib
import subprocess
import sys
from collections.abc import Callable

import credits

FFPROBE = 'ffprobe'        # bare name, resolved via PATH (see video.py)

# $0.60/minute, ElevenLabs music v2.5 list price on fal.
USD_PER_SECOND = 0.60 / 60

# fal's schema caps a composition plan at 30 chunks; ours never gets close (a handful of
# music_cue groups per ad) but the cap is enforced defensively in plan_chunks.
MAX_CHUNKS = 30
MIN_CHUNK_S = 3.0

# Generate the bed longer than the ad so the model's own ending (it always writes one,
# even when asked not to — see the fade-to-silence lesson) falls past the video's end and
# gets trimmed/faded in render, never heard. See skill-observations #112.
TAIL_S = 3.0

OUTPUT_FORMAT = 'mp3_44100_192'
NEGATIVE_STYLES = ('vocals', 'lyrics', 'speech')

# elevenlabs/music/v2.5 rejects any seed above signed-32-bit max with
# input_value_error: "ElevenLabs rejected the music request due to an input error" —
# measured live via one-variable-at-a-time fal calls, 2026-09-29 (seed 3676933754 -> FAIL,
# seed 12345 -> OK; style length, chunk count, context_adherence, durations ruled out).
# orchestrator._seed_for hands us an unsigned 32-bit hash, so ~half of all storyboards hit
# this — build_arguments normalises into range instead of pushing the fix onto the caller.
MAX_SEED = 2**31 - 1

# Cue vocabulary a shot's spec.music_cue (free text from the director model) is normalised
# into. Order matters only for CUE_TEXT/CUE_STYLES lookups below.
CUES = ('soft_open', 'build', 'peak', 'resolve')

CUE_STYLES = {
    'soft_open': 'soft sparse intro, gentle',
    'build': 'building, rising energy',
    'peak': 'full, emotional peak',
    'resolve': 'resolving, warm, gentle ending',
}
CUE_TEXT = {
    'soft_open': '[Intro]',
    'build': '[Build]',
    'peak': '[Peak]',
    'resolve': '[Outro]',
}

# Exactly what fal's MusicV25Input / MusicGenerationChunk schemas accept (verified live,
# see module docstring) — build_arguments must never emit a key outside these sets.
ALLOWED_TOP_KEYS = frozenset({'composition_plan', 'output_format', 'seed'})
ALLOWED_CHUNK_KEYS = frozenset(
    {'text', 'duration_ms', 'positive_styles', 'negative_styles', 'context_adherence'})


@dataclasses.dataclass(frozen=True)
class MusicProvider:
    backend: str
    model: str
    model_path: str
    usd_per_second: float
    submit: Callable        # (model_path, arguments, on_progress) -> result dict


def _fal_credentials() -> None:
    import os
    if not os.environ.get('FAL_KEY'):
        raise RuntimeError('FAL_KEY not set')


def _fal_submit(model_path: str, arguments: dict, on_progress=None) -> dict:
    import fal_client

    _fal_credentials()
    return fal_client.subscribe(model_path, arguments=arguments, with_logs=False,
                                on_queue_update=on_progress)


REGISTRY: dict[tuple[str, str], MusicProvider] = {
    ('fal', 'elevenlabs-music-2.5'): MusicProvider(
        backend='fal', model='elevenlabs-music-2.5', model_path='elevenlabs/music/v2.5',
        usd_per_second=USD_PER_SECOND, submit=_fal_submit,
    ),
}


def get(backend: str | None = None, model: str | None = None) -> MusicProvider:
    """Resolve a provider by (backend, model), or from MUSIC_BACKEND/MUSIC_MODEL."""
    import os

    backend = backend or os.environ.get('MUSIC_BACKEND', 'fal')
    model = model or os.environ.get('MUSIC_MODEL', 'elevenlabs-music-2.5')
    key = (backend, model)
    if key not in REGISTRY:
        raise KeyError(f'unknown backend/model {key!r}; have {sorted(REGISTRY)}')
    return REGISTRY[key]


def credits_for(seconds: float, provider: MusicProvider) -> int:
    """usd_per_second x seconds, in credits, rounded up (see video.credits_for)."""
    return math.ceil(provider.usd_per_second * seconds / credits.USD_PER_CREDIT)


# --- chunk planning ---------------------------------------------------------------------

def _normalise_cue(raw: str, previous: str | None) -> str:
    """Free text from a shot's spec.music_cue -> one of CUES. The director model is not
    forced into this vocabulary (music_cue is a free-text schema field, unlike
    shot_type/camera_move), so this is a best-effort keyword match, not a lookup:
    unrecognised or empty text inherits whatever cue came before it, and the very first
    shot (no `previous`) defaults to 'soft_open' — an ad without a specified cue opens
    quiet rather than guessing a random energy level."""
    text = (raw or '').strip().lower()
    if 'build' in text:
        return 'build'
    if 'peak' in text or 'climax' in text:
        return 'peak'
    if 'resolv' in text or 'outro' in text:
        return 'resolve'
    if 'soft' in text or 'open' in text or 'intro' in text:
        return 'soft_open'
    return previous or 'soft_open'


def plan_chunks(version: dict) -> list[dict]:
    """The version's shots -> a fal composition_plan `chunks` list (see module docstring
    for the exact field shapes). Groups consecutive shots by their normalised music_cue,
    merges any group under 3.0s into a neighbour (forward if there is one, else the
    previous group), folds the end card's duration into the final group, then adds
    TAIL_S to the final chunk so the generated bed outlasts the ad."""
    shots = sorted(version.get('shots') or [], key=lambda s: s.get('position', 0))
    storyboard = version.get('storyboard') or {}
    music_direction = (storyboard.get('music_direction') or '').strip()

    real_shots = [s for s in shots if s.get('kind') != 'end_card']
    end_cards = [s for s in shots if s.get('kind') == 'end_card']

    groups: list[dict] = []           # [{'cue': str, 'duration': float}]
    previous_cue = None
    for shot in real_shots:
        cue = _normalise_cue((shot.get('spec') or {}).get('music_cue', ''), previous_cue)
        previous_cue = cue
        duration = float(shot.get('duration') or 0)
        if groups and groups[-1]['cue'] == cue:
            groups[-1]['duration'] += duration
        else:
            groups.append({'cue': cue, 'duration': duration})

    if not groups:
        groups = [{'cue': 'soft_open', 'duration': 0.0}]

    # Merge every group shorter than MIN_CHUNK_S: forward when there's a next group to
    # take it, otherwise (the last group) backward into the previous one.
    merged = True
    while merged and len(groups) > 1:
        merged = False
        for i, group in enumerate(groups):
            if group['duration'] >= MIN_CHUNK_S:
                continue
            if i < len(groups) - 1:
                groups[i + 1]['duration'] += group['duration']
            else:
                groups[i - 1]['duration'] += group['duration']
            del groups[i]
            merged = True
            break

    # The end card has no music_cue of its own worth grouping on — it always rides the
    # final chunk, whatever cue that settled on (normally 'resolve').
    groups[-1]['duration'] += sum(float(s.get('duration') or 0) for s in end_cards)

    # Defensive: fal allows at most MAX_CHUNKS chunks. Fold the shortest group into its
    # shorter neighbour until under the cap (never hit in practice — a handful of cues
    # per ad — but a storyboard with many alternating cues shouldn't be able to 500 fal).
    while len(groups) > MAX_CHUNKS:
        i = min(range(len(groups)), key=lambda idx: groups[idx]['duration'])
        target = i + 1 if i < len(groups) - 1 else i - 1
        groups[target]['duration'] += groups[i]['duration']
        del groups[i]

    total_duration = float(version.get('total_duration')
                           or sum(float(s.get('duration') or 0) for s in shots))
    target_total_ms = round((total_duration + TAIL_S) * 1000)

    chunks = []
    running_ms = 0
    last_index = len(groups) - 1
    for index, group in enumerate(groups):
        if index == last_index:
            duration_ms = target_total_ms - running_ms      # absorbs rounding + TAIL_S
        else:
            duration_ms = round(group['duration'] * 1000)
            running_ms += duration_ms
        styles = ([music_direction] if music_direction else []) + [CUE_STYLES[group['cue']]]
        chunk = {
            'text': CUE_TEXT[group['cue']],
            'duration_ms': int(duration_ms),
            'positive_styles': styles,
            'negative_styles': list(NEGATIVE_STYLES),
        }
        if index > 0:
            chunk['context_adherence'] = 'high'
        chunks.append(chunk)
    return chunks


def seconds_for(version: dict) -> float:
    """Total generated length (the ad's own duration plus TAIL_S), for pricing."""
    total_duration = float(version.get('total_duration')
                           or sum(float(s.get('duration') or 0)
                                  for s in version.get('shots') or []))
    return total_duration + TAIL_S


def build_arguments(version: dict, seed: int | None = None) -> dict:
    """The fal call's `arguments` dict — composition_plan only, never force_instrumental
    (the schema documents that field as prompt-only) and never bare `prompt` (a
    composition_plan call always wins over a single prompt for a multi-cue ad)."""
    arguments = {
        'composition_plan': {'chunks': plan_chunks(version)},
        'output_format': OUTPUT_FORMAT,
    }
    if seed is not None:
        # Normalise into fal's accepted range (see MAX_SEED) — deterministic, always
        # in range; Python's modulo handles negative seeds too.
        arguments['seed'] = seed % (MAX_SEED + 1)
    return arguments


# --- generation (paid) ------------------------------------------------------------------

TERMINAL_FAILURE_STATUSES = ('failed', 'error')


def _extract_audio_url(result: dict) -> str | None:
    """Tolerant extractor, same shape as video._extract_video_url."""
    audio_field = result.get('audio')
    if isinstance(audio_field, dict) and audio_field.get('url'):
        return audio_field['url']

    def walk(node):
        if isinstance(node, str):
            if node.startswith('https://') and node.split('?')[0].endswith('.mp3'):
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


def _probe_duration(path: pathlib.Path) -> float:
    out = subprocess.run(
        [FFPROBE, '-v', 'error', '-print_format', 'json', '-show_format', str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    return float(data.get('format', {}).get('duration', 0))


def generate(version: dict, out_path, provider: MusicProvider | None = None,
             on_progress=None, seed: int | None = None) -> dict:
    """Submit one music generation and download the result. Paid — never call this from
    --dry-run."""
    import hf  # local import, same reason as video.py: only touched on a paid call

    provider = provider or get()
    arguments = build_arguments(version, seed=seed)
    try:
        result = provider.submit(provider.model_path, arguments, on_progress)
    except Exception as exc:
        detail = getattr(exc, 'message', None) or str(exc)
        error_type = getattr(exc, 'error_type', None)
        if error_type == 'input_value_error' or 'input_value_error' in str(exc):
            raise RuntimeError(
                'The music service rejected the request (input error) — '
                f'{detail[:200]}') from exc
        raise

    status = str(result.get('status', '')).lower()
    if status in TERMINAL_FAILURE_STATUSES:
        raise RuntimeError(f'generation ended in status {status!r}: {result!r}'[:400])

    url = _extract_audio_url(result)
    if not url:
        raise RuntimeError(f'no audio url found in result: {result!r}'[:400])

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(hf._fetch_bytes(url))

    return {
        'path': out_path,
        'duration': _probe_duration(out_path),
        'provider': f'{provider.backend}/{provider.model}',
        'model': provider.model_path,
        'arguments': arguments,
    }


# --- CLI ---------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', metavar='VERSION_JSON', required=True,
                        help='print the composition_plan arguments for a version JSON '
                             '(as storyboard.get_version returns); no network, no charge')
    parser.add_argument('--seed', type=int, default=None)
    args = parser.parse_args()

    version = json.loads(pathlib.Path(args.dry_run).read_text())
    arguments = build_arguments(version, seed=args.seed)
    provider = get()
    seconds = seconds_for(version)

    print(json.dumps(arguments, indent=2))
    print(f'\nseconds: {seconds:.1f}, credits: {credits_for(seconds, provider)} '
         f'({provider.backend}/{provider.model})', file=sys.stderr)


# Bare `python music.py` runs the offline self-check (house convention, see
# director.py/storyboard.py); `python music.py --dry-run version.json` runs the CLI.


def demo() -> None:
    """Self-check: chunk planning, pricing, schema-key restriction. No network."""
    import os

    # --- get(): registry resolution, env override, unknown key rejected -------------
    assert get().backend == 'fal' and get().model == 'elevenlabs-music-2.5'
    os.environ.pop('MUSIC_BACKEND', None)
    os.environ.pop('MUSIC_MODEL', None)
    assert get().model_path == 'elevenlabs/music/v2.5'
    try:
        get('fal', 'nope')
    except KeyError:
        pass
    else:
        raise AssertionError('an unknown model should be rejected')

    # --- credits_for: $0.01/s (0.60/60) at $0.15/credit, 28s -> ceil(0.28/0.15) = 2 ----
    provider = get()
    assert provider.usd_per_second == 0.01, provider.usd_per_second
    assert credits_for(28, provider) == 2 == math.ceil(0.01 * 28 / 0.15), \
        credits_for(28, provider)

    # --- a fake 8-shot version: durations 2,2,1.5,1.5,3,2,1,end_card(2.5) -------------
    # cues: soft_open,soft_open | build,build | resolve,resolve | peak(1s, alone, SHORT)
    # -> the 1s peak group is the LAST real-shot group, so it merges BACKWARD into the
    # preceding 'resolve' group (5s) rather than forward (there's nothing after it but
    # the end card, which never takes a cue of its own).
    def shot(duration, cue, kind='shot'):
        return {'kind': kind, 'duration': duration,
                'spec': ({'music_cue': cue} if cue else {})}

    real = [
        shot(2, 'soft, gentle open'),
        shot(2, 'soft open continues'),
        shot(1.5, 'building energy'),
        shot(1.5, 'build continues'),
        shot(3, 'resolving, warm'),
        shot(2, 'resolve continues'),
        shot(1, 'peak moment'),
    ]
    for position, s in enumerate(real):
        s['position'] = position
    end_card = shot(2.5, '', kind='end_card')
    end_card['position'] = len(real)
    shots = real + [end_card]
    total_duration = sum(s['duration'] for s in shots)
    assert total_duration == 15.5, total_duration

    version = {
        'storyboard': {'music_direction': 'soft strings, cinematic'},
        'shots': shots,
        'total_duration': total_duration,
    }

    chunks = plan_chunks(version)
    assert len(chunks) <= MAX_CHUNKS
    for c in chunks:
        assert c['duration_ms'] >= 3000, c
        assert set(c.keys()) <= ALLOWED_CHUNK_KEYS, c

    # 3 groups survive: soft_open(4s), build(3s), resolve(5s+1s peak merged+2.5s end
    # card+3s TAIL = 11.5s) -> ms: 4000, 3000, 11500 -- sums to (15.5+3)*1000 = 18500.
    assert [c['duration_ms'] for c in chunks] == [4000, 3000, 11500], \
        [c['duration_ms'] for c in chunks]
    assert sum(c['duration_ms'] for c in chunks) == round((total_duration + TAIL_S) * 1000)

    # the 1s peak shot got merged away — no chunk is a lone 'peak' text/style
    assert chunks[-1]['text'] == CUE_TEXT['resolve'], chunks[-1]
    assert CUE_STYLES['resolve'] in chunks[-1]['positive_styles'], chunks[-1]
    # the end card's duration is inside that same final ('resolve') chunk
    assert chunks[-1]['duration_ms'] == 11500

    # context_adherence: absent on the first chunk, 'high' on every later one
    assert 'context_adherence' not in chunks[0], chunks[0]
    assert all(c.get('context_adherence') == 'high' for c in chunks[1:]), chunks

    # music_direction (storyboard-level) leads every chunk's positive_styles
    assert all(chunk['positive_styles'][0] == 'soft strings, cinematic' for chunk in chunks)

    # --- a version with NO music_cue anywhere defaults every shot to soft_open --------
    bare_shots = [{'kind': 'shot', 'duration': 5, 'spec': {}, 'position': 0},
                 {'kind': 'shot', 'duration': 5, 'spec': {}, 'position': 1},
                 {'kind': 'end_card', 'duration': 4, 'spec': {}, 'position': 2}]
    bare_version = {'storyboard': {}, 'shots': bare_shots, 'total_duration': 14}
    bare_chunks = plan_chunks(bare_version)
    assert len(bare_chunks) == 1, bare_chunks     # one soft_open group, nothing to split
    assert bare_chunks[0]['text'] == CUE_TEXT['soft_open']
    assert bare_chunks[0]['duration_ms'] == round((14 + TAIL_S) * 1000)
    # no music_direction on this storyboard -> positive_styles is cue-style only
    assert bare_chunks[0]['positive_styles'] == [CUE_STYLES['soft_open']]

    # --- build_arguments: only schema keys, composition_plan + output_format (+seed) --
    arguments = build_arguments(version)
    assert set(arguments.keys()) <= ALLOWED_TOP_KEYS, arguments
    assert 'force_instrumental' not in arguments, \
        'force_instrumental is documented prompt-only; must never ship with composition_plan'
    assert arguments['output_format'] == 'mp3_44100_192'
    assert 'seed' not in arguments

    seeded = build_arguments(version, seed=42)
    assert seeded['seed'] == 42
    assert set(seeded.keys()) <= ALLOWED_TOP_KEYS, seeded

    # --- seed normalisation: fal rejects seed > MAX_SEED (measured 2026-09-29) --------
    over_seed = build_arguments(version, seed=3676933754)
    assert over_seed['seed'] <= MAX_SEED, over_seed
    assert over_seed['seed'] == 3676933754 % 2**31, over_seed
    assert build_arguments(version, seed=42)['seed'] == 42
    assert build_arguments(version, seed=0)['seed'] == 0

    # --- seconds_for: the ad's own duration plus TAIL_S -------------------------------
    assert seconds_for(version) == total_duration + TAIL_S == 18.5, seconds_for(version)

    # --- FFPROBE stays a bare name resolved via PATH (prod Docker image, see video.py) -
    assert '/' not in FFPROBE, 'FFPROBE must be a bare name resolved via PATH'

    print('music ok')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        main()
    else:
        demo()
