"""Compile an approved storyboard version into the final ad.

Pure function over local files -- no DB, no storage; the orchestrator fetches assets
(clips, logo, music) and calls `render()`. The end card is produced by endcard.animate()
-- a frozen last frame with a slow push-in and style-specific branding motion, NEVER a
gradient/scrim/box/vignette behind the text (see endcard.py for the placement/contrast/
glow rules). Reuses video.py's `_probe` / `_extract_frame` rather than re-implementing
either.

    .venv/bin/python render.py      # self-check, fully offline
"""

import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

import endcard
import video

FFMPEG = video.FFMPEG
FFPROBE = video.FFPROBE

# Reference pixel sizes at height=1920, scaled by height/1920 (and forced even) for any
# other `height` -- this reproduces the brief's four named sizes exactly at the default.
_REF_SIZES = {
    '9:16': (1080, 1920),
    '16:9': (1920, 1080),
    '1:1': (1080, 1080),
    '4:5': (1080, 1350),
}

# ponytail: test-only escape hatch (see demo's red/green proof) -- skips the branding
# overlay entirely so the composite check in scenario D has something real to catch.
_DEBUG_SKIP_OVERLAY = False


@dataclass(frozen=True)
class Segment:
    clip: Path
    duration: float
    transition_out: str = 'cut'          # 'cut' | 'dissolve' (into the next segment)


@dataclass(frozen=True)
class EndCard:
    duration: float
    brand_text: str
    tagline: str = ''
    logo: Path | None = None
    style: str = endcard.DEFAULT_STYLE   # one of endcard.STYLES ('heritage'/'modern'/'minimal')
    keep_case: bool = False              # keep brand_text's typed case (heritage/modern
                                         # otherwise display it uppercase; see endcard.py)


def _run(cmd: list) -> None:
    result = subprocess.run([str(part) for part in cmd], capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise RuntimeError(f'{cmd[0]} failed (exit {result.returncode}):\n'
                           f'{result.stderr[-2000:]}')


def _truncate_ms(seconds: float) -> float:
    """Floor to a whole millisecond. video._extract_frame formats its timestamp with
    `:.3f}`, which ROUNDS -- at duration - 1/fps that rounds 2.966667 up to 2.967,
    a hair past the last frame's own timestamp, and ffmpeg's `-ss` then finds nothing
    to seek to and writes an empty file. Truncating first keeps the printed value
    below the true timestamp instead of at its mercy."""
    return math.floor(seconds * 1000) / 1000


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _size(aspect: str, height: int) -> tuple:
    """Target (width, height) for `aspect`, derived from `height` (even dimensions)."""
    if aspect not in _REF_SIZES:
        raise ValueError(f'unknown aspect {aspect!r}; use one of {sorted(_REF_SIZES)}')
    ref_w, ref_h = _REF_SIZES[aspect]
    scale = height / 1920
    return _even(ref_w * scale), _even(ref_h * scale)


# --- 1. normalise a shot to the target frame --------------------------------------------

def _normalize(segment: Segment, width: int, height: int, fps: int, out_path: Path) -> None:
    """Trim to `segment.duration`, cover-scale + centre-crop to width x height, fps,
    yuv420p, sar 1:1, no audio. Errors if the source clip is too short to trim from."""
    probed = video._probe(segment.clip)
    if probed['duration'] < segment.duration - 0.05:
        raise ValueError(
            f'{segment.clip} is only {probed["duration"]:.2f}s, needs at least '
            f'{segment.duration:.2f}s')
    vf = (f'scale={width}:{height}:force_original_aspect_ratio=increase,'
          f'crop={width}:{height},fps={fps},setsar=1')
    _run([FFMPEG, '-y', '-i', segment.clip, '-t', f'{segment.duration:.3f}', '-vf', vf,
          '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt',
          'yuv420p', out_path])


# --- 2. end card: frozen last frame + endcard.animate()'s style-specific motion --------

def _render_end_card_unbranded(last_frame: Path, duration: float, width: int, height: int,
                               fps: int, out_path: Path) -> None:
    """The SAME push-in (endcard.push_in_scale) with NO branding at all -- an
    apples-to-apples baseline for the composite check's diff: without this, comparing an
    early frame against a late one from the real branded output would also pick up the
    push-in's own background motion and mistake it for (or let it mask) branding."""
    raw = Image.open(last_frame).convert('RGB')
    nframes = max(1, round(duration * fps))
    with tempfile.TemporaryDirectory() as tmp_str:
        frames_dir = Path(tmp_str)
        for i in range(nframes):
            t = i / fps
            s = endcard.push_in_scale(t, duration)
            nw, nh = round(width * s), round(height * s)
            big = raw.resize((nw, nh), Image.LANCZOS)
            left, top = (nw - width) // 2, (nh - height) // 2
            big.crop((left, top, left + width, top + height)).save(frames_dir / f'f{i:04d}.png')
        _run([FFMPEG, '-y', '-framerate', str(fps), '-i', str(frames_dir / 'f%04d.png'),
             '-frames:v', str(nframes), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
             '-pix_fmt', 'yuv420p', out_path])


def _render_end_card(last_frame: Path, card: EndCard, width: int, height: int, fps: int,
                     out_path: Path) -> None:
    """The end-card video segment for `card.style`: push-in + branding motion, NEVER a
    gradient/scrim/box/vignette (see endcard.py). `_DEBUG_SKIP_OVERLAY` renders the SAME
    push-in with no branding at all, so the composite check in the demo's scenario D has
    something real to catch (see the red/green proof there) without the push-in itself
    being mistaken for branding."""
    if _DEBUG_SKIP_OVERLAY:
        _render_end_card_unbranded(last_frame, card.duration, width, height, fps, out_path)
        return
    endcard.animate(last_frame, card.style, card.brand_text, card.tagline, card.duration,
                    out_path, fps=fps, size=(width, height), logo_path=card.logo,
                    keep_case=card.keep_case)


# --- 3. join: hard cuts via concat, dissolves via xfade ---------------------------------

def _join(clips: list, transitions: list, dissolve_seconds: float, tmp: Path,
         joined_path: Path) -> float:
    """Merge `clips` pairwise; `transitions[i]` ('cut'|'dissolve') governs the join
    between clips[i] and clips[i+1]. Done as N-1 pairwise ffmpeg calls rather than one
    global filter graph -- simpler to get the per-pair offset right, same result, and
    these ads are a handful of shots. Returns the exact expected total duration (each
    dissolve shrinks the runtime by dissolve_seconds, as documented in the brief)."""
    current = clips[0]
    expected = video._probe(current)['duration']
    for i, transition in enumerate(transitions):
        nxt = clips[i + 1]
        merged = tmp / f'join-{i}.mp4'
        next_duration = video._probe(nxt)['duration']
        if transition == 'dissolve':
            offset = max(expected - dissolve_seconds, 0)
            filt = (f'[0:v][1:v]xfade=transition=fade:duration={dissolve_seconds}:'
                   f'offset={offset:.3f}[outv]')
            expected += next_duration - dissolve_seconds
        else:
            filt = '[0:v][1:v]concat=n=2:v=1:a=0[outv]'
            expected += next_duration
        _run([FFMPEG, '-y', '-i', current, '-i', nxt, '-filter_complex', filt, '-map',
              '[outv]', '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
              '-pix_fmt', 'yuv420p', merged])
        current = merged
    joined_path.write_bytes(current.read_bytes())
    return expected


# --- 4. audio: the music bed, or a silent track for players that expect one -------------

def _build_audio(music, total_duration: float, end_card_duration: float,
                 loudness_lufs: float, tmp: Path) -> Path:
    out_path = tmp / 'audio.m4a'
    if music is None:
        _run([FFMPEG, '-y', '-f', 'lavfi',
              '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
              '-t', f'{total_duration:.3f}', '-c:a', 'aac', '-b:a', '192k', out_path])
        return out_path
    fade_dur = min(1.5, end_card_duration)
    fade_start = max(total_duration - fade_dur, 0)
    # Single-pass loudnorm: a two-pass measure+correct is more accurate for long-form
    # audio, but this is a few seconds of music bed -- the single-pass error is small
    # and not worth a second ffmpeg call per render.
    af = (f'afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f},'
         f'loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11')
    _run([FFMPEG, '-y', '-i', music, '-t', f'{total_duration:.3f}', '-af', af, '-ar',
          '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '192k', out_path])
    return out_path


# --- 5. final encode ---------------------------------------------------------------------

def _mux(joined_video: Path, audio_path: Path, out_path: Path) -> None:
    _run([FFMPEG, '-y', '-i', joined_video, '-i', audio_path, '-map', '0:v:0', '-map',
          '1:a:0', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-profile:v',
          'high', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
          '-ac', '2', '-shortest', '-movflags', '+faststart', out_path])


def _ffmpeg_version() -> str:
    out = subprocess.run([FFMPEG, '-version'], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


# --- orchestration ------------------------------------------------------------------------

def render(segments: list, end_card: EndCard, out_path: Path, aspect: str = '9:16',
          music=None, height: int = 1920, fps: int = 30, dissolve_seconds: float = 0.4,
          loudness_lufs: float = -14.0, on_progress=None) -> dict:
    if not segments:
        raise ValueError('render needs at least one segment')
    on_progress = on_progress or (lambda stage, fraction: None)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, out_height = _size(aspect, height)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        normalized = []
        for i, segment in enumerate(segments):
            seg_path = tmp / f'segment-{i}.mp4'
            _normalize(segment, width, out_height, fps, seg_path)
            normalized.append(seg_path)
            on_progress('normalising', (i + 1) / len(segments))

        # .png, not .jpg -- ffmpeg's mjpeg encoder refuses a frame this close to a
        # clip's EOF ("Non full-range YUV is non-standard"), a real quirk hit only at
        # this near-end timestamp; png has no such restriction and is lossless besides.
        last_frame = tmp / 'last-frame.png'
        near_end = _truncate_ms(max(segments[-1].duration - 1 / fps, 0))
        video._extract_frame(normalized[-1], near_end, last_frame)
        end_card_clip = tmp / 'end-card.mp4'
        _render_end_card(last_frame, end_card, width, out_height, fps, end_card_clip)
        on_progress('end_card', 1.0)

        transitions = [segment.transition_out for segment in segments]
        joined_path = tmp / 'joined.mp4'
        expected_duration = _join(normalized + [end_card_clip], transitions,
                                  dissolve_seconds, tmp, joined_path)
        on_progress('joining', 1.0)

        actual_duration = video._probe(joined_path)['duration']
        audio_path = _build_audio(music, actual_duration, end_card.duration,
                                  loudness_lufs, tmp)
        on_progress('audio', 1.0)

        _mux(joined_path, audio_path, out_path)
        on_progress('encoding', 1.0)

        poster_path = out_path.with_name(f'{out_path.stem}-poster.jpg')
        video._extract_frame(normalized[0], segments[0].duration / 2, poster_path)

    info = video._probe(out_path)
    return {
        'out_path': out_path,
        'poster_path': poster_path,
        'width': width,
        'height': out_height,
        'fps': fps,
        'duration': info['duration'],
        'expected_duration': expected_duration,
        'segments': [{'clip': str(s.clip), 'duration': s.duration,
                     'transition_out': s.transition_out} for s in segments],
        'end_card': {'duration': end_card.duration, 'brand_text': end_card.brand_text,
                    'tagline': end_card.tagline, 'style': end_card.style,
                    'keep_case': end_card.keep_case,
                    'logo': str(end_card.logo) if end_card.logo else None},
        'music': str(music) if music else None,
        'loudness_lufs': loudness_lufs,
        'ffmpeg_version': _ffmpeg_version(),
    }


# --- demo: fully offline self-check -------------------------------------------------------

SCRATCH_DIR = Path('/private/tmp/claude-501/-Volumes-Suyash2TB-07-Tech-Projects-Vox-Photo-Shoot'
                   '/01788ee8-70e3-46a8-8f8f-12b3970033f2/scratchpad/render')


def _make_clip(path: Path, size: tuple, duration: float, pattern: str = 'testsrc2',
               extra: str = '') -> None:
    _run([FFMPEG, '-y', '-f', 'lavfi',
          '-i', f'{pattern}=size={size[0]}x{size[1]}:rate=30:duration={duration}{extra}',
          '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast', path])


def _make_music(path: Path, duration: float = 25.0) -> None:
    _run([FFMPEG, '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duration}',
          '-f', 'lavfi', '-i', f'anoisesrc=color=pink:duration={duration}',
          '-filter_complex', 'amix=inputs=2:duration=longest', '-ar', '48000', '-ac', '2',
          '-c:a', 'pcm_s16le', path])


def _ffprobe_streams(path: Path) -> list:
    out = subprocess.run([FFPROBE, '-v', 'error', '-print_format', 'json', '-show_streams',
                         str(path)], capture_output=True, text=True, check=True)
    return [s['codec_type'] for s in json.loads(out.stdout)['streams']]


def _integrated_lufs(path: Path) -> float:
    out = subprocess.run([FFMPEG, '-i', str(path), '-af', 'ebur128=peak=true', '-f', 'null',
                         '-'], capture_output=True, text=True, check=False)
    match = re.search(r'^\s*I:\s*(-?\d+\.?\d*)\s*LUFS', out.stderr, re.MULTILINE)
    assert match, f'no ebur128 integrated loudness in ffmpeg output:\n{out.stderr[-800:]}'
    return float(match.group(1))


def _mean_volume(path: Path, start: float, dur: float) -> float:
    out = subprocess.run([FFMPEG, '-ss', f'{start:.3f}', '-t', f'{dur:.3f}', '-i', str(path),
                         '-af', 'volumedetect', '-f', 'null', '-'],
                        capture_output=True, text=True, check=False)
    match = re.search(r'mean_volume:\s*(-?\d+\.?\d*)\s*dB', out.stderr)
    assert match, f'no volumedetect output:\n{out.stderr[-800:]}'
    return float(match.group(1))


def _assert_branding_visible(raw_path: Path, branded_path: Path, width: int,
                             height: int) -> None:
    """The composite check: branding must have actually changed the frame, that change
    must stay inside the frame, and it must NOT look like a filled rectangle (a scrim/
    box/gradient/vignette) -- a real text+glyph change only lights up a modest fraction
    of its own bounding box, where a filled backdrop lights up nearly all of it. This is
    the check the brief calls out as needing to be a COMPOSITE check, not a per-element
    one; endcard.py's own demo() carries the full per-style placement/contrast/no-scrim
    suite (all three styles) -- this one just proves the real ffmpeg-encoded output
    matches what that module promises. Not a horizontal-centre check any more: modern's
    layout is deliberately corner-anchored, not centred."""
    raw = Image.open(raw_path).convert('L')
    branded = Image.open(branded_path).convert('L')
    mask = ImageChops.difference(raw, branded).point(lambda p: 255 if p > 25 else 0)
    bbox = mask.getbbox()
    assert bbox is not None, 'branding produced no visible difference from the raw frame'
    significant = mask.histogram()[255]         # mask is binary (0 or 255), no deprecated getdata
    assert significant > 0.003 * width * height, (
        f'change is only {significant} px, too small to be real branding')
    margin_x, margin_y = 0.02 * width, 0.02 * height
    assert bbox[0] >= margin_x and bbox[2] <= width - margin_x, 'branding runs off the sides'
    assert bbox[1] >= margin_y and bbox[3] <= height - margin_y, 'branding runs off top/bottom'
    bbox_area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    fill_ratio = significant / bbox_area
    assert fill_ratio < 0.5, (
        f'branding fills {fill_ratio:.0%} of its own bounding box -- looks like a filled '
        f'scrim/box, not text+glyphs (see endcard.py\'s no-scrim rule)')


def demo() -> None:
    """Self-check, fully offline: fixtures generated with ffmpeg, no network/paid calls."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        clip_a = tmp / 'a.mp4'; _make_clip(clip_a, (640, 480), 3.0, 'testsrc2')
        clip_b = tmp / 'b.mp4'; _make_clip(clip_b, (800, 600), 3.0, 'smptebars')
        # A flat colour, not testsrc2 -- the end card freezes this clip's last frame, and
        # testsrc2's fine diagonal/checkerboard detail is exactly the content H.264
        # compresses least predictably: two independent encodes of the SAME frame (the
        # composite check's branded vs. unbranded end card) picked up several dozen px
        # of pure quantisation noise on that content, which is not branding and was
        # tripping the composite check for the wrong reason.
        clip_c = tmp / 'c.mp4'; _make_clip(clip_c, (1072, 1928), 5.0, 'color', extra=':c=0x9c8060')
        music = tmp / 'music.wav'; _make_music(music)

        mark = Image.new('RGBA', (300, 100), (0, 0, 0, 0))
        ImageDraw.Draw(mark).ellipse((0, 0, 299, 99), fill=(255, 255, 255, 255))
        logo_path = tmp / 'logo.png'; mark.save(logo_path)

        card = EndCard(duration=2.5, brand_text='Kalyan Jewellers',
                      tagline='Timeless elegance', logo=logo_path)

        # --- A: three cuts, music, expect exactly 9.0s -----------------------------------
        segs_a = [Segment(clip_a, 2.0, 'cut'), Segment(clip_b, 1.5, 'cut'),
                 Segment(clip_c, 3.0, 'cut')]
        out_a = SCRATCH_DIR / 'scenario-a.mp4'
        result_a = render(segs_a, card, out_a, music=music)
        assert abs(result_a['duration'] - 9.0) <= 0.1, result_a['duration']
        assert abs(result_a['expected_duration'] - 9.0) <= 1e-6, result_a['expected_duration']
        assert (result_a['width'], result_a['height']) == (1080, 1920)
        streams_a = _ffprobe_streams(out_a)
        assert streams_a.count('video') == 1 and streams_a.count('audio') == 1, streams_a
        lufs = _integrated_lufs(out_a)
        assert -14 - 1.5 <= lufs <= -14 + 1.5, f'integrated loudness {lufs} LUFS'
        mid_vol = _mean_volume(out_a, 4.0, 0.5)
        end_vol = _mean_volume(out_a, result_a['duration'] - 0.3, 0.3)
        assert end_vol < mid_vol, f'end ({end_vol} dB) not quieter than middle ({mid_vol} dB)'

        # --- B: a dissolve after segment 1, expect 9.0 - 0.4 -----------------------------
        segs_b = [Segment(clip_a, 2.0, 'dissolve'), Segment(clip_b, 1.5, 'cut'),
                 Segment(clip_c, 3.0, 'cut')]
        out_b = SCRATCH_DIR / 'scenario-b.mp4'
        result_b = render(segs_b, card, out_b, music=music)
        assert abs(result_b['duration'] - 8.6) <= 0.1, result_b['duration']
        assert abs(result_b['expected_duration'] - 8.6) <= 1e-6, result_b['expected_duration']

        # --- C: no music -> a silent audio stream must still be present -----------------
        out_c = SCRATCH_DIR / 'scenario-c.mp4'
        result_c = render(segs_a, card, out_c, music=None)
        assert result_c['music'] is None
        streams_c = _ffprobe_streams(out_c)
        assert 'audio' in streams_c, streams_c

        # --- D: composite check of the end card ------------------------------------------
        # The push-in itself changes the WHOLE background continuously, so comparing an
        # early vs. a late frame of the SAME branded output (the old check) would also
        # register the zoom and call it "branding". Isolate branding instead by rendering
        # the SAME frozen last-frame through _render_end_card (real path) and through
        # _render_end_card_unbranded (same push-in, no text) and diffing at the SAME local
        # timestamp -- only the branding differs between the two. (Re-deriving last_frame
        # a second time from the clip independently, instead of sharing this one, was
        # tried first and failed: testsrc2's pattern MOVES every frame, so two separate
        # ffmpeg re-encodes landing even one frame apart hands back two different pictures
        # entirely -- a false "branding" difference with nothing to do with branding.)
        normalized_c = tmp / 'seg-c-normalized.mp4'
        _normalize(segs_a[-1], result_a['width'], result_a['height'], 30, normalized_c)
        last_frame = tmp / 'last-frame.png'
        near_end = _truncate_ms(max(segs_a[-1].duration - 1 / 30, 0))
        video._extract_frame(normalized_c, near_end, last_frame)

        branded_card = tmp / 'end-card-branded.mp4'
        _render_end_card(last_frame, card, result_a['width'], result_a['height'], 30, branded_card)
        unbranded_card = tmp / 'end-card-unbranded.mp4'
        _render_end_card_unbranded(last_frame, card.duration, result_a['width'],
                                   result_a['height'], 30, unbranded_card)
        t_local = card.duration - 0.5     # same instant, shared by both

        unbranded_frame = SCRATCH_DIR / 'end-card-unbranded.png'
        branded_frame = SCRATCH_DIR / 'end-card-branded.png'
        video._extract_frame(unbranded_card, t_local, unbranded_frame)
        video._extract_frame(branded_card, t_local, branded_frame)
        _assert_branding_visible(unbranded_frame, branded_frame, result_a['width'],
                                 result_a['height'])
        mid_frame = SCRATCH_DIR / 'mid-shot.png'
        video._extract_frame(out_a, 3.0, mid_frame)
        print(f'view for visual confirmation: {branded_frame} and {mid_frame}')

        # --- E: a clip too short for its requested duration must raise clearly ----------
        try:
            render([Segment(clip_a, 3.5, 'cut')], card, tmp / 'never.mp4')
        except ValueError as error:
            assert 'needs at least' in str(error), error
        else:
            raise AssertionError('a too-short clip should have raised ValueError')

        # --- prove the composite check can actually fail ---------------------------------
        # Two frames of the SAME unbranded (push-in only, no text) clip: no branding
        # exists anywhere, so the check must correctly report RED.
        early_unbranded_frame = SCRATCH_DIR / 'end-card-unbranded-early.png'
        video._extract_frame(unbranded_card, 0.05, early_unbranded_frame)
        try:
            _assert_branding_visible(early_unbranded_frame, unbranded_frame,
                                     result_a['width'], result_a['height'])
        except AssertionError as red:
            print(f'RED (expected, no branding present in either frame): {red}')
        else:
            raise AssertionError('check should have failed with no branding present')
        # Re-run for real: unbranded vs. the actual branded output, must be GREEN again.
        _assert_branding_visible(unbranded_frame, branded_frame, result_a['width'],
                                 result_a['height'])
        print('GREEN (real branding vs. the unbranded baseline): composite check passes again')

    print('render ok')


if __name__ == '__main__':
    demo()
