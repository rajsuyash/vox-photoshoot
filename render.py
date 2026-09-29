"""Compile an approved storyboard version into the final ad.

Pure function over local files -- no DB, no storage; the orchestrator fetches assets
(clips, logo, music) and calls `render()`. All end-card text is drawn with Pillow and
composited with ffmpeg's `overlay`/`fade` -- never `drawtext`, since this machine's
ffmpeg has no libfreetype. Reuses branding.py's font loading (Latin + Devanagari) and
per-element halo/layout helpers for the end-card text, and video.py's `_probe` /
`_extract_frame` rather than re-implementing either.

    .venv/bin/python render.py      # self-check, fully offline
"""

import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import branding
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

# End-card layout, as fractions of the frame so a 1080-wide and a 2160-wide card get
# branding of the same visual weight.
_GAP_FRACTION = 0.02
_BRAND_HEIGHT_FRACTION = 0.05
_TAGLINE_HEIGHT_FRACTION = 0.03
_TEXT_MAX_WIDTH_FRACTION = 0.62         # leaves real margin once the scrim pad + blur add to it
_SCRIM_PAD_X_FRACTION = 0.05
_SCRIM_PAD_Y_FRACTION = 0.04

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


# --- 2. end card: frozen last frame + a Pillow branding block ---------------------------

def _fit(text: str, size: int, max_width: int):
    """Shrink-to-fit sizing on top of branding.font_for -- its own _fit_text is tuned
    for a small corner watermark, this needs a much bigger centred headline."""
    font = branding.font_for(text, size)
    while size > 14 and font.getlength(text) > max_width:
        size -= 1
        font = branding.font_for(text, size)
    box = font.getbbox(text)
    return font, box[2] - box[0], box[3] - box[1]


def _draw_scrim(layer: Image.Image, box: tuple, width: int, height: int) -> None:
    """A soft dark backdrop behind the branding block, so white text stays legible
    over any footage. Softened with a blur, the same halo technique branding.py uses."""
    pad_x, pad_y = int(width * _SCRIM_PAD_X_FRACTION), int(height * _SCRIM_PAD_Y_FRACTION)
    padded = (max(0, box[0] - pad_x), max(0, box[1] - pad_y),
             min(width, box[2] + pad_x), min(height, box[3] + pad_y))
    scrim = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    radius = max(4, min(padded[2] - padded[0], padded[3] - padded[1]) // 6)
    ImageDraw.Draw(scrim).rounded_rectangle(padded, radius=radius, fill=(10, 10, 10, 165))
    layer.alpha_composite(scrim.filter(ImageFilter.GaussianBlur(max(4, pad_x // 6))))


def _branding_block(width: int, height: int, card: EndCard) -> Image.Image:
    """One centred group -- optional logo, brand text, tagline -- on a soft scrim, as a
    transparent WxH PNG. Composited (and faded in) by ffmpeg, drawn entirely with Pillow,
    reusing branding.py's font/glyph handling and its logo + per-element halo helpers."""
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gap = int(height * _GAP_FRACTION)
    max_width = int(width * _TEXT_MAX_WIDTH_FRACTION)

    logo_bytes = card.logo.read_bytes() if card.logo else None
    logo = branding._prepare_logo(logo_bytes, width)
    brand_font, brand_w, brand_h = _fit(card.brand_text, int(height * _BRAND_HEIGHT_FRACTION),
                                       max_width)
    tag_font = tag_w = tag_h = None
    if card.tagline:
        tag_font, tag_w, tag_h = _fit(card.tagline, int(height * _TAGLINE_HEIGHT_FRACTION),
                                      max_width)

    block_width = max(logo.width if logo else 0, brand_w, tag_w or 0)
    block_height = (logo.height + gap if logo else 0) + brand_h
    if card.tagline:
        block_height += gap + tag_h

    left = (width - block_width) // 2
    top = (height - block_height) // 2
    _draw_scrim(layer, (left, top, left + block_width, top + block_height), width, height)

    ink, halo = (255, 255, 255, 255), (0, 0, 0, 190)     # the scrim guarantees a dark bed
    cursor = top
    if logo is not None:
        cursor = branding._draw_logo_block(layer, logo, left, block_width, cursor, gap)
    branding._draw_text_block(layer, card.brand_text, brand_font, left, block_width,
                              brand_w, cursor, ink, halo)
    cursor += brand_h + gap
    if card.tagline:
        branding._draw_text_block(layer, card.tagline, tag_font, left, block_width,
                                  tag_w, cursor, ink, halo)
    return layer


def _render_end_card(last_frame: Path, card: EndCard, width: int, height: int, fps: int,
                     out_path: Path, tmp: Path) -> None:
    """Freeze `last_frame` for card.duration; overlay the branding block, fading in
    over 0.5s starting 0.2s into the card."""
    duration = f'{card.duration:.3f}'
    cmd = [FFMPEG, '-y', '-loop', '1', '-t', duration, '-i', last_frame]

    if _DEBUG_SKIP_OVERLAY:
        filter_complex = f'[0:v]fps={fps}[outv]'
    else:
        block_path = tmp / 'end-card-block.png'
        _branding_block(width, height, card).save(block_path)
        cmd += ['-loop', '1', '-t', duration, '-i', block_path]
        filter_complex = (
            f'[1:v]fps={fps},format=rgba,fade=t=in:st=0.2:d=0.5:alpha=1[ov];'
            f'[0:v][ov]overlay=0:0:format=auto,fps={fps}[outv]'
        )

    cmd += ['-filter_complex', filter_complex, '-map', '[outv]', '-t', duration, '-an',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
           out_path]
    _run(cmd)


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
        _render_end_card(last_frame, end_card, width, out_height, fps, end_card_clip, tmp)
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
                    'tagline': end_card.tagline,
                    'logo': str(end_card.logo) if end_card.logo else None},
        'music': str(music) if music else None,
        'loudness_lufs': loudness_lufs,
        'ffmpeg_version': _ffmpeg_version(),
    }


# --- demo: fully offline self-check -------------------------------------------------------

SCRATCH_DIR = Path('/private/tmp/claude-501/-Volumes-Suyash2TB-07-Tech-Projects-Vox-Photo-Shoot'
                   '/01788ee8-70e3-46a8-8f8f-12b3970033f2/scratchpad/render')


def _make_clip(path: Path, size: tuple, duration: float, pattern: str = 'testsrc2') -> None:
    _run([FFMPEG, '-y', '-f', 'lavfi',
          '-i', f'{pattern}=size={size[0]}x{size[1]}:rate=30:duration={duration}',
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
    """The composite check: branding must have actually changed the frame, and the
    changed region must be centred and fully inside the frame. This is the check the
    brief calls out as needing to be a COMPOSITE check, not a per-element one."""
    raw = Image.open(raw_path).convert('L')
    branded = Image.open(branded_path).convert('L')
    mask = ImageChops.difference(raw, branded).point(lambda p: 255 if p > 25 else 0)
    bbox = mask.getbbox()
    assert bbox is not None, 'branding produced no visible difference from the raw frame'
    significant = mask.histogram()[255]         # mask is binary (0 or 255), no deprecated getdata
    assert significant > 0.01 * width * height, (
        f'change is only {significant} px, too small to be real branding')
    cx = (bbox[0] + bbox[2]) / 2
    assert abs(cx - width / 2) <= 0.05 * width, 'branding block not horizontally centred'
    margin_x, margin_y = 0.05 * width, 0.05 * height
    assert bbox[0] >= margin_x and bbox[2] <= width - margin_x, 'branding runs off the sides'
    assert bbox[1] >= margin_y and bbox[3] <= height - margin_y, 'branding runs off top/bottom'


def demo() -> None:
    """Self-check, fully offline: fixtures generated with ffmpeg, no network/paid calls."""
    global _DEBUG_SKIP_OVERLAY
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        clip_a = tmp / 'a.mp4'; _make_clip(clip_a, (640, 480), 3.0, 'testsrc2')
        clip_b = tmp / 'b.mp4'; _make_clip(clip_b, (800, 600), 3.0, 'smptebars')
        clip_c = tmp / 'c.mp4'; _make_clip(clip_c, (1072, 1928), 5.0, 'testsrc2')
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
        card_start = result_a['duration'] - card.duration
        raw_frame = SCRATCH_DIR / 'end-card-raw.png'
        branded_frame = SCRATCH_DIR / 'end-card-branded.png'
        video._extract_frame(out_a, card_start + 0.05, raw_frame)     # before the fade starts
        video._extract_frame(out_a, result_a['duration'] - 0.5, branded_frame)
        _assert_branding_visible(raw_frame, branded_frame, result_a['width'],
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
        _DEBUG_SKIP_OVERLAY = True
        try:
            # Re-render scenario A's end card with the overlay skipped, and confirm the
            # composite check now correctly reports RED.
            broken_out = tmp / 'scenario-a-broken.mp4'
            render(segs_a, card, broken_out, music=music)
            broken_frame = tmp / 'broken-frame.png'
            video._extract_frame(broken_out, result_a['duration'] - 0.5, broken_frame)
            try:
                _assert_branding_visible(raw_frame, broken_frame, result_a['width'],
                                         result_a['height'])
            except AssertionError as red:
                print(f'RED (expected, overlay skipped): {red}')
            else:
                raise AssertionError('check should have failed with the overlay skipped')
        finally:
            _DEBUG_SKIP_OVERLAY = False
        # Re-run for real: same scenario, overlay back on, must be GREEN again.
        _assert_branding_visible(raw_frame, branded_frame, result_a['width'],
                                 result_a['height'])
        print('GREEN (overlay restored): composite check passes again')

    print('render ok')


if __name__ == '__main__':
    demo()
