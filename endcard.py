"""End-card styles: three user-selectable looks (heritage, modern, minimal), each placed
on the calmest region of the frame with NO gradient/scrim/box/vignette behind the text —
legibility comes only from (a) placement away from the product and the subject's face,
(b) adaptive text colour (contrast >= 3.0 WCAG-large against the region), (c) a tight
glow hugging the letterforms.

Ported from the approved mockup (see the brief's $S/render.py, $S/motion.py,
variance_report.json) and generalised: the mockup's fixed pixel boxes -> fractions of
frame size (any aspect/resolution); its per-image hand-picked gold/face crops -> a
whole-frame gold-hue scan plus a documented upper-middle face heuristic (see
_FACE_HEURISTIC below — no face detector is bundled on this box).

Brand/tagline SPELLING is drawn EXACTLY as given — never altered, never AI-painted.
Letter CASE is a separate, purely typographic decision: heritage and modern display the
brand in uppercase (tracking a lowercase word looks broken, and it is what the approved
mockup showed) unless `keep_case=True`, via `str.upper()` — a true no-op on scripts
without case (Devanagari verified in endcard's own demo). The tagline always keeps its
typed case in every style, and minimal never transforms its brand at all. Devanagari text
(Cormorant has no Devanagari) automatically falls back to branding.py's Noto Sans
Devanagari.

    .venv/bin/python endcard.py     # self-check, fully offline (synthetic fixtures)
"""

import colorsys
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

import branding
import video

FFMPEG = video.FFMPEG

ASSETS = Path('assets/fonts')
CG = ASSETS / 'CormorantGaramond-Variable.ttf'
CG_IT = ASSETS / 'CormorantGaramond-Italic-Variable.ttf'
CINZEL = ASSETS / 'Cinzel-Variable.ttf'
JOST = ASSETS / 'Jost-Variable.ttf'

STYLES = ('heritage', 'modern', 'minimal')
DEFAULT_STYLE = 'heritage'

IVORY = (243, 235, 221)
CHARCOAL_INK = (28, 26, 24)             # modern/minimal's dark-ink alternate on a light bg
SHADOW_WARM = (20, 14, 8)               # glow colour behind light/gold text
LIGHT_GLOW = (250, 245, 232)            # glow colour behind dark ink (on a light bg)

# Fallback gold, used only when the frame has no gold-hued pixels to sample (e.g. a
# fixture, or a shot with no jewellery in view) — taken from the approved mockup.
DEFAULT_GOLD_LIGHT = (231, 197, 153)
DEFAULT_GOLD_MID = (216, 172, 123)
DEFAULT_GOLD_DEEP = (201, 147, 93)

# Cap-height floors, as a fraction of FRAME height (not the placement box) — the user
# asked for brand text a bit larger/heavier than the mockup, since pale gold read faint
# at phone size.
_BRAND_CAP_FRACTION = {'heritage': 0.032, 'modern': 0.026, 'minimal': 0.026}
# Of the brand's OWN cap height -- the brand must stay visually dominant (hairline in
# between, monogram smallest of all). 0.85 used to make the tagline read LARGER than the
# brand on a real photo (a real bug, not just an aesthetic call) because Pillow's ascent+
# descent envelope for an italic/lowercase-heavy string is taller than the ratio of cap
# heights alone suggests; 0.55 keeps it subordinate with real margin.
_TAGLINE_CAP_FRACTION = 0.55

_MIN_CONTRAST = 3.0                     # WCAG large-text minimum
_MIN_CONTRAST_SMALL = 4.5               # stricter floor for small text (taglines) — small
                                         # glyphs have less area for the glow to help with
_GLOW_BLUR_REF = 6                      # px @ 1080w, per the brief's ceiling
_FRAME_MARGIN_FRACTION = 0.04            # every glyph must clear this margin from the
                                         # frame's own edges (not just its region box)

# Candidate placement regions, as fractions of (width, height) — ported from the mockup's
# hand-tuned pixel boxes at 1080x1920 (see variance_report.json's surviving candidates),
# so any render size (9:16, 16:9, 1:1, 4:5) gets the same relative placement. analyse()
# picks whichever of a style's candidates is calmest AND clear of the product/face.
_CANDIDATES = {
    'heritage': [(0.278, 0.651, 0.704, 0.844), (0.0, 0.677, 0.694, 0.859),
                 (0.278, 0.760, 0.704, 0.953)],
    'modern':   [(0.648, 0.021, 1.0, 0.146), (0.0, 0.0, 0.352, 0.135)],
    'minimal':  [(0.278, 0.833, 0.685, 0.896), (0.0, 0.86, 1.0, 0.92)],
}

# ponytail: no face detector is bundled or guaranteed installed on this box, so — per the
# brief — a face is assumed to sit where a bust-frame portrait conventionally puts it:
# upper-middle, wider than tall. That keeps end-card text off a subject's face without a
# model dependency. Upgrade path: swap in a real cheap detector (e.g. mediapipe) if this
# heuristic ever actually misses in production.
_FACE_HEURISTIC = (0.28, 0.16, 0.72, 0.46)

_GOLD_HUE_LO, _GOLD_HUE_HI = 25, 55     # degrees; matches the mockup's step3_sample_gold.py
_GOLD_SAT_MIN, _GOLD_VAL_MIN = 70, 60   # 0-255 scale (PIL's HSV)


# --- geometry / measurement --------------------------------------------------------------

def _overlaps(a: tuple, b: tuple) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def _frac_box(frac: tuple, w: int, h: int) -> tuple:
    x0, y0, x1, y1 = frac
    return (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))


def _local_variance(gray: Image.Image, box: tuple, tile: int = 20) -> float:
    x0, y0, x1, y1 = box
    stds = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            cell = gray.crop((x, y, min(x + tile, x1), min(y + tile, y1)))
            if cell.width and cell.height:
                stds.append(ImageStat.Stat(cell).stddev[0])
            x += tile
        y += tile
    return sum(stds) / len(stds) if stds else 0.0


def _gold_bbox(rgb: Image.Image) -> tuple | None:
    """Bounding box of gold-hued pixels across the WHOLE frame (the jewellery/product
    exclusion), or None if nothing gold-hued is found. Generalises the mockup's
    hand-picked necklace crop into a full-frame scan. Sampled on a grid, not every pixel —
    this only needs a bounding box, and a full-res scan of a 1080x1920 frame in pure
    Python is not worth the seconds."""
    w, h = rgb.size
    step = max(1, min(w, h) // 200)
    hsv = rgb.convert('HSV')
    hp = hsv.load()
    xs, ys = [], []
    for y in range(0, h, step):
        for x in range(0, w, step):
            hue, sat, val = hp[x, y]
            deg = hue * 360 / 255
            if _GOLD_HUE_LO <= deg <= _GOLD_HUE_HI and sat > _GOLD_SAT_MIN and val > _GOLD_VAL_MIN:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), min(w, max(xs) + step), min(h, max(ys) + step))


def _sample_gold(rgb: Image.Image, gold_box: tuple | None) -> tuple:
    """(light, mid, deep) gold RGB sampled from the frame's own jewellery, or the mockup's
    default gold if none was found."""
    if gold_box is None:
        return DEFAULT_GOLD_LIGHT, DEFAULT_GOLD_MID, DEFAULT_GOLD_DEEP
    x0, y0, x1, y1 = gold_box
    hsv = rgb.convert('HSV')
    px, hp = rgb.load(), hsv.load()
    golds = []
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            hue, sat, val = hp[x, y]
            deg = hue * 360 / 255
            if _GOLD_HUE_LO <= deg <= _GOLD_HUE_HI and sat > _GOLD_SAT_MIN and val > _GOLD_VAL_MIN:
                golds.append(px[x, y])
    if len(golds) < 5:
        return DEFAULT_GOLD_LIGHT, DEFAULT_GOLD_MID, DEFAULT_GOLD_DEEP
    golds.sort(key=lambda p: 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2])
    n = len(golds)

    def avg(lst):
        m = len(lst)
        return tuple(round(sum(p[i] for p in lst) / m) for i in range(3))
    return avg(golds[int(n * 0.85):]), avg(golds), avg(golds[:int(n * 0.35)])


def analyse(frame: Image.Image) -> dict:
    """Per style: {'box', 'mean_luminance', 'local_variance', 'gold_light', 'gold_mid',
    'gold_deep'} — the calmest of that style's candidate regions that clears both the
    product (gold-hue mask) and the face heuristic, plus gold sampled from the frame's own
    jewellery (shared across styles — there is only one piece in the shot)."""
    rgb = frame.convert('RGB')
    gray = rgb.convert('L')
    w, h = rgb.size
    gold_box = _gold_bbox(rgb)
    gold_light, gold_mid, gold_deep = _sample_gold(rgb, gold_box)
    face_box = _frac_box(_FACE_HEURISTIC, w, h)

    out = {}
    for style, candidates in _CANDIDATES.items():
        boxes = [_frac_box(f, w, h) for f in candidates]
        clear = [b for b in boxes if not _overlaps(b, face_box)
                and (gold_box is None or not _overlaps(b, gold_box))]
        pool = clear or boxes           # every candidate collides -- fall back to calmest anyway
        box = min(pool, key=lambda b: _local_variance(gray, b))
        out[style] = {
            'box': box,
            'mean_luminance': ImageStat.Stat(gray.crop(box)).mean[0],
            'local_variance': _local_variance(gray, box),
            'gold_light': gold_light, 'gold_mid': gold_mid, 'gold_deep': gold_deep,
            'frame_gray': gray,          # same shared Image, for a per-element re-check —
                                         # a whole-box average can hide a bright pocket
                                         # right under one specific element (see modern).
        }
    return out


# --- adaptive colour (WCAG contrast) ------------------------------------------------------

def _srgb_to_linear(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple) -> float:
    r, g, b = rgb
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast_ratio(fg: tuple, bg_luminance_0_255: float) -> float:
    """WCAG contrast ratio of `fg` against a FLAT background of mean luminance
    `bg_luminance_0_255` — an approximation (the real backdrop is photographic, not
    flat), which is exactly why the tight glow (c) exists on top of this, not instead of
    it."""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance((bg_luminance_0_255,) * 3)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _darken(rgb: tuple, r_f: float, g_f: float, b_f: float) -> tuple:
    r, g, b = rgb
    return (round(r * r_f), round(g * g_f), round(b * b_f))


def _lighten(rgb: tuple, amount: float) -> tuple:
    return tuple(min(255, round(c + (255 - c) * amount)) for c in rgb)


def _ensure_contrast(rgb: tuple, bg_luminance: float, min_ratio: float = None) -> tuple:
    """`rgb` nudged toward white or black in HSV space (hue/saturation held, only
    brightness moves, so it still reads as the same colour family) until its contrast
    against a flat `bg_luminance` backdrop clears `min_ratio` -- or already does, unchanged.
    A hand-picked darken/lighten factor (the first version of this file used one) only
    verifies for the ONE luminance it was tuned against; this solves it for any."""
    min_ratio = _MIN_CONTRAST if min_ratio is None else min_ratio
    if contrast_ratio(rgb, bg_luminance) >= min_ratio:
        return rgb
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    for direction in (1, -1):                    # try lighter first, then darker
        vv = v
        for _ in range(50):
            vv = max(0.0, min(1.0, vv + direction * 0.02))
            rr, gg, bb = colorsys.hsv_to_rgb(h, s, vv)
            candidate = (round(rr * 255), round(gg * 255), round(bb * 255))
            if contrast_ratio(candidate, bg_luminance) >= min_ratio:
                return candidate
            if vv in (0.0, 1.0):
                break
    # Neither direction reached the target (a mid-grey backdrop close to this hue can be
    # low-contrast against every shade of it) -- fall back to whichever true extreme wins.
    white, black = (255, 255, 255), (0, 0, 0)
    return white if contrast_ratio(white, bg_luminance) >= contrast_ratio(black, bg_luminance) \
        else black


def _adaptive_gold(gold_light: tuple, gold_mid: tuple, gold_deep: tuple,
                   bg_luminance: float) -> tuple:
    """(light, mid, deep), each independently pushed to >= 3.0 contrast against the
    region if the sampled gold's own mid-tone doesn't already clear it. Fixing only
    gold_mid and leaving gold_light/gold_deep at their ORIGINAL (failing) values was a
    real bug this file shipped with — the still-dark gold_deep end of the brand text's
    gradient stayed unreadable even after gold_mid alone passed, exactly the "pale gold
    is faint at phone size" complaint the cap-height floor was meant to fix. Caught by
    viewing the actual PNG against the user's real photo, not by any unit assertion."""
    if contrast_ratio(gold_mid, bg_luminance) >= _MIN_CONTRAST:
        return gold_light, gold_mid, gold_deep
    return (_ensure_contrast(gold_light, bg_luminance),
            _ensure_contrast(gold_mid, bg_luminance),
            _ensure_contrast(gold_deep, bg_luminance))


def _glow_for(ink: tuple) -> tuple:
    """The glow colour opposite the ink itself (per the brief's (c)) -- a dark glow
    behind light/gold ink, a light glow behind dark ink."""
    return SHADOW_WARM if sum(ink) > 380 else LIGHT_GLOW


def _adaptive_ivory(bg_luminance: float, min_ratio: float = _MIN_CONTRAST) -> tuple:
    if contrast_ratio(IVORY, bg_luminance) >= min_ratio:
        return IVORY
    alt = CHARCOAL_INK if bg_luminance >= 128 else IVORY
    return _ensure_contrast(alt, bg_luminance, min_ratio)


def _local_luminance(gray: Image.Image, bbox: tuple) -> float | None:
    """Mean luminance under `bbox` (an element's actual rendered rectangle), clamped to
    `gray`'s bounds. None if the box is degenerate. A whole-region average (analyse()'s
    mean_luminance) can hide a bright pocket right where one specific element lands --
    real bug, caught by viewing an actual render, not by the synthetic fixture suite."""
    x0 = max(0, int(bbox[0])); y0 = max(0, int(bbox[1]))
    x1 = min(gray.width, int(bbox[2])); y1 = min(gray.height, int(bbox[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return ImageStat.Stat(gray.crop((x0, y0, x1, y1))).mean[0]


def _retint(layer: Image.Image, rgb: tuple) -> Image.Image:
    """`layer` with its RGB replaced by a flat `rgb`, alpha (glyph shape) untouched --
    cheap re-colouring of an already-laid-out SOLID-fill text layer, so a local-contrast
    fix doesn't need to re-run font shaping/tracking."""
    out = Image.new('RGBA', layer.size, rgb + (0,))
    out.putalpha(layer.split()[-1])
    return out


# --- fonts / glyph layout (ported from the mockup's render.py) --------------------------

def _has_devanagari(text: str) -> bool:
    return any(ord(c) in branding.DEVANAGARI_RANGE for c in text)


def _font(path: Path, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:                             # noqa: BLE001 - a static face, fine as-is
            pass
    return f


def _text_font(style: str, text: str, size: int, role: str) -> ImageFont.FreeTypeFont:
    """role: 'brand' | 'tagline'. Devanagari always wins (Cormorant/Cinzel/Jost have none)."""
    if _has_devanagari(text) and branding.DEVANAGARI_FONT.exists():
        return ImageFont.truetype(str(branding.DEVANAGARI_FONT), size)
    if style == 'heritage':
        return _font(CG, size, 600) if role == 'brand' else _font(CG_IT, size, 500)
    if style == 'modern':
        return _font(JOST, size, 300 if role == 'brand' else 400)
    return _font(CG, size, 500)                        # minimal: brand only


def _size_for_cap_height(font_path: Path, weight: int | None, target_cap_px: float) -> int:
    """The font size whose rendered cap height ('M') hits `target_cap_px` — cap height,
    not nominal font size, is what the brief's floor is measured in."""
    probe = _font(font_path, 200, weight)
    bbox = probe.getbbox('M')
    cap200 = bbox[3] - bbox[1]
    if cap200 <= 0:
        return max(14, round(target_cap_px))
    return max(14, round(200 * target_cap_px / cap200))


def _tracked_layer(text: str, fnt: ImageFont.FreeTypeFont, tracking_px: float,
                   fill_top: tuple, fill_bottom: tuple | None = None, pad: int = 30):
    """Render `text` with letter-spacing onto a tightly-cropped RGBA layer. A vertical
    gradient fill_top -> fill_bottom, or solid if they match."""
    if fill_bottom is None:
        fill_bottom = fill_top
    widths = [fnt.getlength(ch) for ch in text]
    total_w = sum(widths) + tracking_px * max(0, len(text) - 1)
    ascent, descent = fnt.getmetrics()
    W = int(total_w) + pad * 2
    H = ascent + descent + pad * 2
    mask = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(mask)
    x = float(pad)
    for ch, w in zip(text, widths):
        d.text((x, pad), ch, font=fnt, fill=255)
        x += w + tracking_px
    bbox = mask.getbbox() or (0, 0, W, H)
    bx0, by0 = max(0, bbox[0] - 12), max(0, bbox[1] - 12)
    bx1, by1 = min(W, bbox[2] + 12), min(H, bbox[3] + 12)
    tight_bbox = bbox                                   # the INK-only box, before the +12 pad
    mask = mask.crop((bx0, by0, bx1, by1))
    cw, ch_ = mask.size
    grad = Image.new('RGB', (cw, ch_))
    gd = ImageDraw.Draw(grad)
    for yy in range(ch_):
        t = yy / max(1, ch_ - 1)
        col = tuple(round(fill_top[i] * (1 - t) + fill_bottom[i] * t) for i in range(3))
        gd.line([(0, yy), (cw, yy)], fill=col)
    layer = Image.new('RGBA', (cw, ch_))
    layer.paste(grad, (0, 0))
    layer.putalpha(mask)
    ink_size = (tight_bbox[2] - tight_bbox[0], tight_bbox[3] - tight_bbox[1])
    return layer, int(round(total_w)), ink_size


def _glow_blur_px(width: int) -> int:
    return max(2, min(_GLOW_BLUR_REF, round(_GLOW_BLUR_REF * width / 1080)))


def _paste_with_glow(base: Image.Image, layer: Image.Image, anchor: str, xy: tuple,
                     glow_blur: int, glow_opacity: int, glow_color: tuple) -> tuple:
    """Composite a tight glow (blurred silhouette) behind the sharp text/graphic layer.
    Returns the tight ink bbox actually touched, in `base`'s coordinates (for the
    no-scrim self-check — the glow's own extent is allowed to spill a little, the RAW
    ink must not)."""
    alpha = layer.split()[-1]
    glow_alpha = alpha.filter(ImageFilter.GaussianBlur(glow_blur))
    glow_alpha = glow_alpha.point(lambda a: min(255, int(a * (glow_opacity / 255))))
    glow_rgba = Image.new('RGBA', layer.size, glow_color + (0,))
    glow_rgba.putalpha(glow_alpha)

    w, h = layer.size
    if anchor == 'center':
        x, y = int(xy[0] - w / 2), int(xy[1] - h / 2)
    elif anchor == 'right':
        x, y = int(xy[0] - w), int(xy[1])
    else:
        x, y = int(xy[0]), int(xy[1])
    base.alpha_composite(glow_rgba, (x, y))
    base.alpha_composite(layer, (x, y))
    return (x, y, x + w, y + h)


def _draw_hairline(base: Image.Image, cx: float, cy: float, width_px: float,
                   color: tuple) -> tuple:
    d = ImageDraw.Draw(base)
    half, gap = width_px / 2, 16
    d.line([(cx - half, cy), (cx - gap, cy)], fill=color + (210,), width=2)
    d.line([(cx + gap, cy), (cx + half, cy)], fill=color + (210,), width=2)
    r = 5
    d.regular_polygon((cx, cy, r), n_sides=4, rotation=45, fill=color + (230,))
    return (round(cx - half - r), round(cy - r * 2), round(cx + half + r), round(cy + r * 2))


def _draw_monogram(base: Image.Image, cx: float, cy: float, diameter: float,
                   mono_font: ImageFont.FreeTypeFont, initials: str, color: tuple) -> tuple:
    r = diameter / 2
    d = ImageDraw.Draw(base)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (200,), width=2)
    layer, _w, _ink = _tracked_layer(initials, mono_font, 2, color)
    _paste_with_glow(base, layer, 'center', (cx, cy - 2), glow_blur=3, glow_opacity=70,
                     glow_color=_glow_for(color))
    return (round(cx - r), round(cy - r), round(cx + r), round(cy + r))


def _initials(brand: str) -> str:
    """Up to 3 initials for the optional monogram — skipped for Devanagari (a Latin
    convention) and for a single-word brand (one letter in a circle reads as a typo, not
    a mark)."""
    if _has_devanagari(brand):
        return ''
    words = [w for w in brand.split() if w]
    if len(words) < 2:
        return ''
    return ''.join(w[0].upper() for w in words[:3])


# --- per-style settled layout --------------------------------------------------------

def _fit_size(font_path: Path, weight: int | None, text: str, tracking_frac: float,
             max_width: float, target_cap_px: float, min_size: int = 16) -> int:
    """The font size for `target_cap_px` cap height, shrunk (never below `min_size`)
    until `text` at `tracking_frac` (em) tracking fits `max_width`. Sized at the
    SETTLED tracking regardless of any animation easing elsewhere — only the tracking
    itself breathes during the intro, never the underlying font size (matches the
    mockup: one fixed font object per element, animated tracking_px only)."""
    size = _size_for_cap_height(font_path, weight, target_cap_px)
    devanagari = _has_devanagari(text)
    while size > min_size:
        font = (ImageFont.truetype(str(branding.DEVANAGARI_FONT), size) if devanagari
               else _font(font_path, size, weight))
        tracking = size * tracking_frac
        w = sum(font.getlength(c) for c in text) + tracking * max(0, len(text) - 1)
        if w <= max_width:
            break
        size -= 1
    return size


# Text width cap, as a fraction of FRAME width (not the placement box — several of the
# ported candidate boxes are deliberately narrow "calm strips", and the brief's bigger
# cap-height floor needs more room than that to fit a real brand name without shrinking
# straight back down to mockup size). modern stays narrower: it is a deliberate corner
# block, not a centred banner.
_MAX_WIDTH_FRACTION = {'heritage': 0.82, 'modern': 0.58, 'minimal': 0.82}


def _retint_local(spec: tuple, frame_gray: Image.Image, min_ratio: float) -> tuple:
    """Re-choose a 'layer' spec's ink from the luminance actually under ITS OWN final
    bbox rather than the whole region's average -- a bright pocket under one element
    (e.g. a sunlit curtain fold right where the tagline lands) can defeat a whole-box
    average even when the average itself says "keep the light ink". No-op for anything
    that isn't a plain text layer."""
    kind, payload, anchor_xy, glow_blur, glow_opacity, glow_color, extra = spec
    if kind != 'layer':
        return spec
    anchor, xy = anchor_xy
    w, h = payload.size
    if anchor == 'right':
        bx0, by0 = xy[0] - w, xy[1]
    elif anchor == 'center':
        bx0, by0 = xy[0] - w / 2, xy[1] - h / 2
    else:
        bx0, by0 = xy
    local_lum = _local_luminance(frame_gray, (bx0, by0, bx0 + w, by0 + h))
    if local_lum is None:
        return spec
    ink = _adaptive_ivory(local_lum, min_ratio)
    return ('layer', _retint(payload, ink), anchor_xy, glow_blur, glow_opacity,
           _glow_for(ink), extra)


def _elements(style: str, info: dict, brand: str, tagline: str, width: int, height: int,
             ease: float = 1.0, keep_case: bool = False) -> tuple:
    """(elements, stack_top): elements = [(group, spec)] for `style`'s layout, vertically
    stacked and centred inside info['box'] — spec is (kind, payload, anchor, glow_blur,
    glow_opacity, glow_color, extra), consumed by _draw_element(). group 'A' = brand
    (fades first, per the brief); group 'B' = everything that follows ~0.4s later
    (heritage's monogram+hairline+tagline; modern/minimal's tagline). `ease` in [0, 1] is
    group A's OWN progress -- only heritage's brand tracking eases with it (0.35em ->
    0.25em); every other element is drawn at its settled geometry regardless (animate()
    fades/shifts the whole element as a unit instead). stack_top is the y where the
    layout begins, for compose()'s optional logo above it.

    SPELLING is never touched. CASE is: heritage/modern display the brand in uppercase
    (str.upper() -- a no-op on scripts without case, e.g. Devanagari) unless
    `keep_case=True`; the tagline always keeps its typed case; minimal never transforms
    its brand. Hierarchy (brand > hairline > tagline > monogram) is enforced by
    _TAGLINE_CAP_FRACTION < 1 plus a heavier brand weight, not by drawing order."""
    box = info['box']
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    max_width = width * _MAX_WIDTH_FRACTION[style]
    glow_blur = _glow_blur_px(width)
    bg_lum = info['mean_luminance']
    gold_light, gold_mid, gold_deep = _adaptive_gold(
        info['gold_light'], info['gold_mid'], info['gold_deep'], bg_lum)
    ivory = _adaptive_ivory(bg_lum)
    glow_for = _glow_for

    display_brand = brand.upper() if style in ('heritage', 'modern') and not keep_case else brand

    brand_cap = height * _BRAND_CAP_FRACTION[style]
    tag_cap = brand_cap * _TAGLINE_CAP_FRACTION

    blocks = []     # (height_px, builder(y_center) -> (group, layer, anchor, xy, blur, op, color))

    if style == 'heritage':
        initials = _initials(brand)
        if initials:
            mono_f = _font(CINZEL, max(16, round(brand_cap * 0.9)), 500)
            mono_layer, _mono_w, _mono_ink = _tracked_layer(initials, mono_f, 2, gold_mid)
            diameter = mono_layer.size[1] * 1.7
            mono_glow = glow_for(gold_mid)
            blocks.append(('A', diameter, lambda yc, d=diameter, f=mono_f, i=initials,
                          ink=gold_mid, glow=mono_glow:
                          ('monogram', (f, i, ink), ('center', (cx, yc)), glow_blur, 70, glow, d)))

        # 700 (bold axis), not 600 -- a heavier stroke measurably helps contrast at the
        # same size, and the brand must stay the visually dominant element.
        brand_size = _fit_size(CG, 700, display_brand, 0.25, max_width, brand_cap)
        brand_f = _font(CG, brand_size, 700)
        tracking = brand_size * (0.35 - 0.10 * ease)
        brand_layer, _bw, _bink = _tracked_layer(display_brand, brand_f, tracking,
                                                 gold_light, gold_deep)
        blocks.append(('A', brand_layer.height,
                      lambda yc, ly=brand_layer: ('layer', ly, ('center', (cx, yc)),
                                                  glow_blur, 100, SHADOW_WARM, None)))

        hairline_w = min(0.35 * (x1 - x0), 220)
        blocks.append(('B', 24, lambda yc, w=hairline_w: ('hairline', gold_mid,
                                                          ('center', (cx, yc)), None, None, None, w)))

        if tagline:
            tag_size = _fit_size(CG_IT, 500, tagline, 0.0, max_width, tag_cap)
            tag_f = _text_font('heritage', tagline, tag_size, 'tagline')
            tag_layer, _tw, _tink = _tracked_layer(tagline, tag_f, 0, ivory)
            blocks.append(('B', tag_layer.height,
                          lambda yc, ly=tag_layer: ('layer', ly, ('center', (cx, yc)),
                                                    max(2, glow_blur - 1), 80,
                                                    glow_for(ivory), None)))

    elif style == 'modern':
        # Anchored inside the frame's own right margin (>= _FRAME_MARGIN_FRACTION),
        # capped by the region box's own edge -- min() rather than a fraction of the
        # box alone, or a NARROW box (the top-left fallback candidate) would anchor
        # text far out past its own right edge chasing a frame-relative margin that
        # made no sense for it.
        right_x = min(x1 - (x1 - x0) * 0.06, width * (1 - _FRAME_MARGIN_FRACTION - 0.02))
        brand_size = _fit_size(JOST, 300, display_brand, 0.35, max_width, brand_cap)
        brand_f = _font(JOST, brand_size, 300)
        tracking = brand_size * 0.35
        brand_layer, _bw, _ink = _tracked_layer(display_brand, brand_f, tracking, IVORY)
        blocks.append(('A', brand_layer.height,
                      lambda yc, ly=brand_layer: ('layer', ly, ('right', (right_x, yc - ly.height / 2)),
                                                  glow_blur, 90, glow_for(IVORY), None)))
        if tagline:
            tag_size = _fit_size(JOST, 400, tagline, 0.2, max_width, tag_cap)
            tag_f = _text_font('modern', tagline, tag_size, 'tagline')
            tag_layer, _tw, _tink = _tracked_layer(tagline, tag_f, tag_size * 0.2, IVORY)
            blocks.append(('B', tag_layer.height,
                          lambda yc, ly=tag_layer: ('layer', ly, ('right', (right_x, yc - ly.height / 2)),
                                                    max(2, glow_blur - 1), 90, glow_for(IVORY), None)))

    else:                                               # minimal: brand only, never cased
        brand_size = _fit_size(CG, 500, brand, 0.0, max_width, brand_cap)
        brand_f = _font(CG, brand_size, 500)
        brand_layer, _bw, _ink = _tracked_layer(brand, brand_f, 0, gold_mid)
        blocks.append(('A', brand_layer.height,
                      lambda yc, ly=brand_layer: ('layer', ly, ('center', (cx, yc)),
                                                  glow_blur, 90, glow_for(gold_mid), None)))

    gap = max(10, round((y1 - y0) * 0.03))
    total_h = sum(h for _g, h, _b in blocks) + gap * max(0, len(blocks) - 1)
    top = (y0 + y1) / 2 - total_h / 2
    out = []
    y = top
    for group, h, builder in blocks:
        spec = builder(y + h / 2)
        if style == 'modern':
            # A whole-box average said "ivory is fine"; re-check against what's
            # actually under THIS element once its real position is known (see
            # _retint_local's docstring for why the average alone was not enough).
            min_ratio = _MIN_CONTRAST_SMALL if group == 'B' else _MIN_CONTRAST
            spec = _retint_local(spec, info['frame_gray'], min_ratio)
        out.append((group, spec))
        y += h + gap
    return out, top


def _draw_element(layer: Image.Image, spec: tuple, alpha: float, y_shift: float) -> tuple:
    """Draw one _elements() entry onto `layer` at fractional `alpha`, shifted up by
    `y_shift` px (the fade-in rise). Returns the tight ink bbox it touched, or None for a
    fully-transparent element (skip drawing at alpha ~ 0 rather than compositing nothing)."""
    kind, payload, anchor_xy, glow_blur, glow_opacity, glow_color, extra = spec
    if alpha <= 0.002:
        return None
    anchor, xy = anchor_xy
    xy = (xy[0], xy[1] - y_shift)

    if kind == 'hairline':
        color = payload
        cx, cy = xy
        w = extra
        faded = Image.new('RGBA', layer.size, (0, 0, 0, 0))
        box = _draw_hairline(faded, cx, cy, w, color)
        a = faded.split()[-1].point(lambda p: int(p * alpha))
        faded.putalpha(a)
        layer.alpha_composite(faded)
        return box

    if kind == 'monogram':
        font, initials, ink = payload
        cx, cy = xy
        diameter = extra
        faded = Image.new('RGBA', layer.size, (0, 0, 0, 0))
        box = _draw_monogram(faded, cx, cy, diameter, font, initials, ink)
        a = faded.split()[-1].point(lambda p: int(p * alpha))
        faded.putalpha(a)
        layer.alpha_composite(faded)
        return box

    # kind == 'layer': a pre-rendered tracked-text layer
    src = payload
    if alpha < 0.999:
        src = src.copy()
        src.putalpha(src.split()[-1].point(lambda p: int(p * alpha)))
    return _paste_with_glow(layer, src, anchor, xy, glow_blur, glow_opacity, glow_color)


# --- public: compose / preview / animate --------------------------------------------------

def _place_logo(logo: Image.Image, style: str, box: tuple, stack_top: float,
                width: int) -> tuple:
    """(position, resized_logo) for `logo` centred above the stack, or None -- heritage/
    minimal only (modern's corner layout has no room for one without crowding the
    tagline)."""
    if style not in ('heritage', 'minimal'):
        return None
    logo_w = max(1, round(width * 0.10))
    scale = logo_w / logo.width
    logo_small = logo.resize((logo_w, max(1, round(logo.height * scale))), Image.LANCZOS)
    cx = (box[0] + box[2]) / 2
    logo_top = stack_top - logo_small.height - 16
    pos = (round(cx - logo_small.width / 2), round(logo_top))
    return pos, logo_small


def compose(frame_img: Image.Image, style: str, brand: str, tagline: str,
           logo: Image.Image | None = None, keep_case: bool = False) -> tuple:
    """The settled (fully faded-in) text layer for `style`, as an RGBA image the same size
    as `frame_img`, transparent everywhere except the branding — NEVER a scrim/box/
    gradient. `logo`, if given, is drawn small and centred above the brand text (heritage/
    minimal only — modern's corner layout has no room for one without crowding the
    tagline). `keep_case` keeps the brand's typed capitalisation instead of heritage/
    modern's default uppercase display (spelling is never altered either way). Returns
    (layer, info) where info carries the placement box, the element ink boxes (for the
    no-scrim self-check) and the colours actually used."""
    if style not in STYLES:
        raise ValueError(f'unknown end-card style {style!r}; use one of {STYLES}')
    width, height = frame_img.size
    analysis = analyse(frame_img)[style]
    elements, stack_top = _elements(style, analysis, brand, tagline, width, height, ease=1.0,
                                    keep_case=keep_case)

    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ink_boxes = []
    box = analysis['box']

    if logo is not None:
        placed = _place_logo(logo, style, box, stack_top, width)
        if placed:
            pos, logo_small = placed
            layer.alpha_composite(logo_small, pos)
            ink_boxes.append((pos[0], pos[1], pos[0] + logo_small.width, pos[1] + logo_small.height))

    for _group, spec in elements:
        touched = _draw_element(layer, spec, alpha=1.0, y_shift=0)
        if touched:
            ink_boxes.append(touched)

    info = {'box': box, 'element_boxes': ink_boxes, 'mean_luminance': analysis['mean_luminance'],
           'local_variance': analysis['local_variance']}
    return layer, info


def preview(frame_path, style: str, brand: str, tagline: str, out_path,
           logo_path=None, keep_case: bool = False) -> dict:
    """Render one still PNG: `frame_path` with `style`'s end-card composited on top."""
    frame = Image.open(frame_path).convert('RGBA')
    logo = Image.open(logo_path).convert('RGBA') if logo_path else None
    layer, info = compose(frame, style, brand, tagline, logo=logo, keep_case=keep_case)
    out = Image.alpha_composite(frame, layer).convert('RGB')
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    return info


def _run(cmd: list) -> None:
    result = subprocess.run([str(part) for part in cmd], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f'{cmd[0]} failed (exit {result.returncode}):\n{result.stderr[-2000:]}')


def _ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def push_in_scale(t: float, duration: float) -> float:
    """The push-in zoom factor at time `t` of a `duration`-second end card (100% ->
    103%). Exposed (not just inlined in animate()) so a caller needing the frozen frame
    WITHOUT branding can still replicate the same background motion for an
    apples-to-apples composite check -- see render.py's `_DEBUG_SKIP_OVERLAY` path."""
    return 1.0 + 0.03 * min(1.0, max(0.0, t) / duration)


def animate(frame_path, style: str, brand: str, tagline: str, duration: float, out_mp4,
           fps: int = 30, size: tuple | None = None, logo_path=None,
           keep_case: bool = False) -> None:
    """The end-card video segment: a slow push-in (100% -> 103% over `duration`) on the
    frozen frame, `style`'s branding fading/rising in (group A: brand [+logo, if given],
    from 0.3s over 0.9s; group B: the rest, ~0.4s later), plus — heritage only — a
    letter-spacing ease and one soft shimmer sweep across the brand between 1.4s-2.0s.
    Frame-by-frame Pillow + ffmpeg encode (measured at ~16s wall-clock for a 3.5s
    1080x1920 card on this machine, inside the brief's <=25s-added budget); a
    precomputed-layer + ffmpeg-only path would be faster still but this one already fits
    and needed zero new ffmpeg filter-graph plumbing. `logo_path` is an extra beyond the
    brief's own signature (kept optional/last) so the workspace logo the old scrim-based
    end card used to show does not silently disappear."""
    if style not in STYLES:
        raise ValueError(f'unknown end-card style {style!r}; use one of {STYLES}')
    raw = Image.open(frame_path).convert('RGBA')
    if size:
        raw = raw.resize(size, Image.LANCZOS)
    width, height = raw.size
    analysis = analyse(raw)[style]
    logo = Image.open(logo_path).convert('RGBA') if logo_path else None

    nframes = max(1, round(duration * fps))
    a_start, a_dur = 0.3, 0.9
    b_start, b_dur = 0.7, 0.6
    shimmer_start, shimmer_end = 1.4, 2.0

    def pushed_in_bg(t):
        s = push_in_scale(t, duration)
        nw, nh = round(width * s), round(height * s)
        big = raw.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - width) // 2, (nh - height) // 2
        return big.crop((left, top, left + width, top + height))

    def shimmer_layer(brand_layer, prog):
        lw, lh = brand_layer.size
        band = Image.new('L', (lw, lh), 0)
        diag = lw + lh
        center_u = -0.3 * diag + prog * 1.6 * diag
        band_w = diag * 0.14
        px = band.load()
        for yy in range(lh):
            for xx in range(lw):
                u = xx * 0.7 + yy * 0.7
                d = abs(u - center_u)
                if d < band_w:
                    val = int(255 * (1 - d / band_w))
                    if val > px[xx, yy]:
                        px[xx, yy] = val
        band = band.filter(ImageFilter.GaussianBlur(3))
        alpha_src = brand_layer.split()[-1]
        shimmer_alpha = Image.composite(band, Image.new('L', (lw, lh), 0), alpha_src)
        shimmer = Image.new('RGBA', (lw, lh), (255, 248, 225, 0))
        shimmer.putalpha(shimmer_alpha)
        return shimmer

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        frames_dir = tmp / 'frames'
        frames_dir.mkdir()

        for i in range(nframes):
            t = i / fps
            frame = pushed_in_bg(t)
            g1 = _ease_out_cubic((t - a_start) / a_dur)
            g2 = _ease_out_cubic((t - b_start) / b_dur)
            elements, stack_top = _elements(style, analysis, brand, tagline, width, height,
                                            ease=g1, keep_case=keep_case)

            rise = lambda g: 12 * (1 - g)      # noqa: E731
            if logo is not None and g1 > 0.002:
                placed = _place_logo(logo, style, analysis['box'], stack_top, width)
                if placed:
                    pos, logo_small = placed
                    faded_logo = logo_small
                    if g1 < 0.999:
                        faded_logo = logo_small.copy()
                        faded_logo.putalpha(faded_logo.split()[-1].point(lambda a: int(a * g1)))
                    frame.alpha_composite(faded_logo, (pos[0], round(pos[1] - rise(g1))))
            for group, spec in elements:
                if group == 'A':
                    _draw_element(frame, spec, g1, rise(g1))
                else:
                    _draw_element(frame, spec, g2, rise(g2))

            if style == 'heritage' and shimmer_start <= t <= shimmer_end and g1 > 0.01:
                brand_spec = next((s for g, s in elements if g == 'A' and s[0] == 'layer'), None)
                if brand_spec is not None:
                    anchor, xy = brand_spec[2]
                    prog = (t - shimmer_start) / (shimmer_end - shimmer_start)
                    shimmer = shimmer_layer(brand_spec[1], prog)
                    shimmer.putalpha(shimmer.split()[-1].point(lambda a: int(a * g1 * 0.8)))
                    w, h = shimmer.size
                    if anchor == 'center':
                        pos = (int(xy[0] - w / 2), int(xy[1] - h / 2 - rise(g1)))
                    else:
                        pos = (int(xy[0] - w), int(xy[1] - rise(g1)))
                    frame.alpha_composite(shimmer, pos)

            frame.convert('RGB').save(frames_dir / f'f{i:04d}.png')

        out_mp4 = Path(out_mp4)
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        _run([FFMPEG, '-y', '-framerate', str(fps), '-i', str(frames_dir / 'f%04d.png'),
             '-frames:v', str(nframes), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
             '-pix_fmt', 'yuv420p', out_mp4])


# --- demo: fully offline self-check ------------------------------------------------------

SCRATCH_DIR = Path('/private/tmp/claude-501/-Volumes-Suyash2TB-07-Tech-Projects-Vox-Photo-Shoot'
                   '/01788ee8-70e3-46a8-8f8f-12b3970033f2/scratchpad/endcard-module')


def _fixture(luminance: int, w=1080, h=1920, gold_box=None, face_box=None) -> Image.Image:
    """A uniform frame at `luminance`, with a gold-hued patch (product) and a mid-grey
    patch (subject's face) at the given absolute boxes, or the module's own default
    positions if omitted -- lets the demo prove placement steers clear of both."""
    img = Image.new('RGB', (w, h), (luminance, luminance, luminance))
    d = ImageDraw.Draw(img)
    gold_box = gold_box or (round(w * 0.35), round(h * 0.55), round(w * 0.65), round(h * 0.68))
    face_box = face_box or _frac_box(_FACE_HEURISTIC, w, h)
    d.rectangle(gold_box, fill=(198, 148, 88))          # squarely gold-hued (H~35deg, S/V high)
    d.rectangle(face_box, fill=(160, 150, 145))         # a flat mid-tone stand-in for skin
    return img


def _dilate(box: tuple, px: int) -> tuple:
    return (box[0] - px, box[1] - px, box[2] + px, box[3] + px)


def _assert_no_scrim(layer: Image.Image, element_boxes: list) -> None:
    """The brief's own acceptance test: every alpha>8 pixel in the composed layer must
    fall inside SOME element's ink bbox, dilated by 12px -- i.e. nothing resembling a
    background rectangle exists anywhere the text/graphics themselves don't reach."""
    alpha = layer.split()[-1]
    mask = alpha.point(lambda a: 255 if a > 8 else 0)
    allowed = Image.new('L', layer.size, 0)
    d = ImageDraw.Draw(allowed)
    for box in element_boxes:
        d.rectangle(_dilate(box, 12), fill=255)
    outside = Image.new('L', layer.size, 0)
    outside.paste(mask, (0, 0))
    # outside = mask AND (NOT allowed)
    not_allowed = allowed.point(lambda a: 255 - a)
    from PIL import ImageChops
    outside = ImageChops.multiply(mask, not_allowed.point(lambda a: 1 if a else 0))
    stray = outside.histogram()
    stray_count = sum(v for i, v in enumerate(stray) if i > 0)
    assert stray_count == 0, f'{stray_count} px outside every dilated element box -- looks like a scrim'


def _assert_margins(element_boxes: list, width: int, height: int,
                    margin_fraction: float = _FRAME_MARGIN_FRACTION) -> dict:
    """Every element's ink bbox must clear `margin_fraction` of the FRAME's own
    dimensions on every side -- not just avoid the product/face (analyse()'s job), but
    avoid running close enough to the visible frame edge to read as clipped. Returns
    the measured margins (as fractions) for the caller to report/log."""
    mx, my = width * margin_fraction, height * margin_fraction
    margins = {'left': 1.0, 'right': 1.0, 'top': 1.0, 'bottom': 1.0}
    for box in element_boxes:
        x0, y0, x1, y1 = box
        margins['left'] = min(margins['left'], x0 / width)
        margins['right'] = min(margins['right'], (width - x1) / width)
        margins['top'] = min(margins['top'], y0 / height)
        margins['bottom'] = min(margins['bottom'], (height - y1) / height)
        assert x0 >= mx - 1, f'element {box} left margin < {margin_fraction:.0%} of frame width'
        assert x1 <= width - mx + 1, \
            f'element {box} right margin < {margin_fraction:.0%} of frame width'
        assert y0 >= my - 1, f'element {box} top margin < {margin_fraction:.0%} of frame height'
        assert y1 <= height - my + 1, \
            f'element {box} bottom margin < {margin_fraction:.0%} of frame height'
    return margins


def demo() -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    # --- analyse(): placement clears both the product and the face heuristic ------------
    light = _fixture(210)
    dark = _fixture(40)
    for fixture, label in ((light, 'light'), (dark, 'dark')):
        analysis = analyse(fixture)
        face_box = _frac_box(_FACE_HEURISTIC, *fixture.size)
        gold_box = _gold_bbox(fixture.convert('RGB'))
        for style in STYLES:
            box = analysis[style]['box']
            assert not _overlaps(box, face_box), f'{label}/{style} box overlaps the face heuristic'
            if gold_box:
                assert not _overlaps(box, gold_box), f'{label}/{style} box overlaps the gold blob'
    print('analyse() placement ok (clears face + product on light and dark fixtures)')

    # --- adaptive contrast: every style hits >= 3.0 on both a light and a dark fixture --
    for fixture, label in ((light, 'light'), (dark, 'dark')):
        analysis = analyse(fixture)
        for style in STYLES:
            info = analysis[style]
            gold_light, gold_mid, gold_deep = _adaptive_gold(
                info['gold_light'], info['gold_mid'], info['gold_deep'], info['mean_luminance'])
            ivory = _adaptive_ivory(info['mean_luminance'])
            ratio = contrast_ratio(gold_mid, info['mean_luminance'])
            assert ratio >= _MIN_CONTRAST, f'{label}/{style} adapted gold contrast {ratio:.2f} < {_MIN_CONTRAST}'
            assert contrast_ratio(ivory, info['mean_luminance']) >= _MIN_CONTRAST, \
                f'{label}/{style} ivory/ink contrast < {_MIN_CONTRAST}'
    print('adaptive contrast ok (>= 3.0 WCAG-large on light and dark fixtures, every style)')

    # --- no scrim/box/gradient/vignette behind the text, on a BUSY fixture too ----------
    busy = Image.new('RGB', (1080, 1920), (120, 120, 120))
    px = busy.load()
    for y in range(0, 1920, 3):
        for x in range(0, 1080, 3):
            px[x, y] = (90 + (x + y) % 60, 90 + (x * 2 + y) % 50, 90 + (x + y * 2) % 55)
    for style in STYLES:
        layer, info = compose(busy, style, 'MEHTA JEWELLER', 'Radiance begins at home.')
        _assert_no_scrim(layer, info['element_boxes'])
    print('no-scrim self-check ok (heritage/modern/minimal, busy fixture)')

    # --- frame-edge margins: nothing should ever read as clipped ------------------------
    for fixture, label in ((light, 'light'), (dark, 'dark'), (busy, 'busy')):
        for style in STYLES:
            layer, info = compose(fixture, style, 'MEHTA JEWELLER', 'Radiance begins at home.')
            margins = _assert_margins(info['element_boxes'], 1080, 1920)
            assert min(margins.values()) >= _FRAME_MARGIN_FRACTION, (label, style, margins)
    print(f'frame-edge margins ok (>= {_FRAME_MARGIN_FRACTION:.0%} on every side, every '
         'style, light/dark/busy fixtures)')

    # --- modern: per-element LOCAL contrast (not the whole box's average) -- brand
    # >= 3.0, tagline >= 4.5, both re-checked against a bright synthetic patch under
    # just the tagline to prove the local re-check actually fires -----------------------
    def _modern_local_contrast(fixture):
        analysis = analyse(fixture)['modern']
        elements, _top = _elements('modern', analysis, 'Mehta Jeweller',
                                   'Radiance begins at home.', 1080, 1920, ease=1.0)
        results = {}
        for group, spec in elements:
            kind, payload, anchor_xy = spec[0], spec[1], spec[2]
            if kind != 'layer':
                continue
            anchor, xy = anchor_xy
            w, h = payload.size
            bx0, by0 = (xy[0] - w, xy[1]) if anchor == 'right' else (xy[0] - w / 2, xy[1] - h / 2)
            local_lum = _local_luminance(analysis['frame_gray'], (bx0, by0, bx0 + w, by0 + h))
            ink = payload.convert('RGB').getpixel((0, 0))     # flat-filled -- any pixel is the ink
            results[group] = (contrast_ratio(ink, local_lum), local_lum)
        return results

    for fixture, label in ((light, 'light'), (dark, 'dark'), (busy, 'busy')):
        for group, (ratio, _lum) in _modern_local_contrast(fixture).items():
            target = _MIN_CONTRAST_SMALL if group == 'B' else _MIN_CONTRAST
            assert ratio >= target, f'modern/{label}/group {group} local contrast {ratio:.2f} < {target}'

    # A bright patch centred under where the TAGLINE lands (not the whole box) --
    # proves the fix responds to a LOCAL pocket a whole-box average would hide.
    bright_pocket = light.copy()
    pocket_analysis = analyse(bright_pocket)['modern']
    px0, py0, px1, py1 = pocket_analysis['box']
    ImageDraw.Draw(bright_pocket).rectangle(
        (px0, py0 + round((py1 - py0) * 0.55), px1, py1), fill=(250, 248, 244))
    pocket_results = _modern_local_contrast(bright_pocket)
    for group, (ratio, _lum) in pocket_results.items():
        target = _MIN_CONTRAST_SMALL if group == 'B' else _MIN_CONTRAST
        assert ratio >= target, f'modern/bright-pocket/group {group} contrast {ratio:.2f} < {target}'
    print('modern per-element local contrast ok (brand >= 3.0, tagline >= 4.5, measured '
         'under each element itself, incl. a synthetic bright pocket under the tagline)')

    # --- exact text: SPELLING is never touched; CASE is a per-style typographic choice --
    # heritage/modern display the brand in uppercase by default (keep_case=False) --
    # spelling is untouched (str.upper() changes case only), but the exact ORIGINAL-CASE
    # string must not be what actually got drawn either, or the "default uppercase" rule
    # is a no-op. keep_case=True must recover the exact typed string. The tagline and
    # minimal's brand must NEVER be touched, in either mode. Verified by spying on
    # _tracked_layer's own `text` argument -- the one point every code path funnels
    # through -- rather than re-deriving font size/tracking to compare rendered pixels.
    odd_brand = 'mehTA   jeweller & Co.'            # deliberately mixed case + odd spacing
    odd_tagline = 'RaDiance Begins AT Home.'        # also mixed case, so an accidental
                                                    # transform on the tagline is caught too
    real_tracked_layer = _tracked_layer
    captured = []

    def _spy_tracked_layer(text, *a, **k):
        captured.append(text)
        return real_tracked_layer(text, *a, **k)
    globals()['_tracked_layer'] = _spy_tracked_layer
    try:
        for style in STYLES:
            analysis = analyse(light)[style]
            for keep_case in (False, True):
                captured.clear()
                _elements(style, analysis, odd_brand, odd_tagline, 1080, 1920, ease=1.0,
                         keep_case=keep_case)
                if style != 'minimal':          # minimal never draws a tagline at all
                    assert odd_tagline in captured, \
                        f'{style}/keep_case={keep_case}: tagline was altered -- {captured}'
                if style in ('heritage', 'modern') and not keep_case:
                    assert odd_brand.upper() in captured, \
                        f'{style}: brand should display uppercase by default -- {captured}'
                    assert odd_brand not in captured, \
                        f'{style}: brand kept its typed case when it should not have'
                else:
                    assert odd_brand in captured, \
                        (f'{style}/keep_case={keep_case}: brand spelling or case was '
                        f'altered -- {captured}')
    finally:
        globals()['_tracked_layer'] = real_tracked_layer
    # Devanagari has no case at all -- str.upper() must be a true no-op on it (checked
    # directly, not just inferred from the Latin cases above).
    hindi = 'घर से शुरू होती है चमक।'
    assert hindi.upper() == hindi, 'str.upper() is not a no-op on Devanagari'
    print('exact-text ok (spelling always exact; heritage/modern default to uppercase '
         'display, keep_case recovers the typed case, tagline/minimal never transform)')

    # --- Devanagari tagline renders with the Devanagari font -----------------------------
    hindi_tagline = 'घर से शुरू होती है चमक।'
    for style in STYLES:
        size = 40
        font = _text_font(style, hindi_tagline, size, 'tagline')
        assert font.path == str(branding.DEVANAGARI_FONT), \
            f'{style} did not fall back to the Devanagari font for Hindi text'
    print('Devanagari fallback ok (all styles route Hindi text to Noto Sans Devanagari)')

    # --- red -> green proof: a temporary scrim must fail the no-scrim check -------------
    layer, info = compose(busy, 'heritage', 'MEHTA JEWELLER', 'Radiance begins at home.')
    scrim = Image.new('RGBA', layer.size, (0, 0, 0, 0))
    box = info['box']
    ImageDraw.Draw(scrim).rounded_rectangle(box, radius=20, fill=(10, 10, 10, 165))
    broken = Image.alpha_composite(scrim, layer)
    try:
        _assert_no_scrim(broken, info['element_boxes'])
    except AssertionError as red:
        print(f'RED (expected, scrim injected): {red}')
    else:
        raise AssertionError('the no-scrim check should have failed with a scrim present')
    _assert_no_scrim(layer, info['element_boxes'])       # unmodified layer: still GREEN
    print('GREEN (scrim removed): no-scrim check passes again')

    # --- preview(): writes a real composited still, on all three fixtures ---------------
    for fixture, label in ((light, 'light'), (dark, 'dark'), (busy, 'busy')):
        fixture_path = SCRATCH_DIR / f'{label}.png'
        fixture.save(fixture_path)
        for style in STYLES:
            out_path = SCRATCH_DIR / f'{label}-{style}.png'
            info = preview(fixture_path, style, 'Mehta Jeweller', 'Radiance begins at home.', out_path)
            assert out_path.exists() and out_path.stat().st_size > 0
    print(f'preview() ok, wrote fixtures + composites under {SCRATCH_DIR}')

    # --- animate(): a short real render, checked for actual motion (not a static loop) --
    anim_out = SCRATCH_DIR / 'heritage-anim.mp4'
    animate(SCRATCH_DIR / 'busy.png', 'heritage', 'Mehta Jeweller', 'Radiance begins at home.',
           duration=1.2, out_mp4=anim_out, fps=24)
    assert anim_out.exists() and anim_out.stat().st_size > 1000
    probe = video._probe(anim_out)
    assert abs(probe['duration'] - 1.2) <= 0.15, probe['duration']
    first_frame = SCRATCH_DIR / 'anim-first.png'
    last_frame = SCRATCH_DIR / 'anim-last.png'
    video._extract_frame(anim_out, 0.02, first_frame)
    video._extract_frame(anim_out, 1.05, last_frame)
    from PIL import ImageChops
    diff = ImageChops.difference(Image.open(first_frame).convert('L'),
                                 Image.open(last_frame).convert('L'))
    assert diff.getbbox() is not None, 'animate() produced a static loop, no motion detected'
    print(f'animate() ok, motion confirmed between first/last frame: {anim_out}')

    print('endcard ok')


if __name__ == '__main__':
    demo()
