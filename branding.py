"""Stamp a client's logo and contact line onto a delivered image.

Composited with Pillow at DOWNLOAD time, never at generation time, and never written
back over the stored file. Two reasons, and both are the whole design:

  The model must not draw the logo. Asked to render a wordmark it produces something
  logo-ISH — invented letterforms, wrong kerning, a mangled mark. That is precisely the
  failure this product exists to avoid on the jewellery itself, and it would cost a
  credit per attempt.

  The stored master stays clean. A brand that rebrands, or needs an unbranded file for a
  magazine, gets it from the same original rather than paying to reshoot a catalogue.
  Branding is a view of an image, not a property of one.

    .venv/bin/python branding.py      # self-check, writes nothing but a temp file
"""

import io
import pathlib

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = pathlib.Path('assets/fonts')

# Latin, the rupee sign and the punctuation a contact line actually uses. Inter is the
# app's own typeface, so a stamped image looks like it came from the same place.
LATIN_FONT = FONTS / 'Inter-SemiBold.ttf'

# Inter has no Indic coverage at all — Devanagari, Tamil, Gujarati and Arabic all render
# as the SAME .notdef box, which would burn tofu onto an image a client paid for. Hindi
# and Marathi are the likely second script for an Indian jeweller, so Devanagari is
# bundled; everything else is caught by unsupported() and reported before it is stamped
# rather than discovered on a delivered file.
DEVANAGARI_FONT = FONTS / 'NotoSansDevanagari.ttf'
DEVANAGARI_RANGE = range(0x0900, 0x0980)

POSITIONS = ('bottom-right', 'bottom-left', 'top-right', 'top-left', 'bottom-centre')
DEFAULT_POSITION = 'bottom-right'

# Everything scales with the image rather than sitting at a fixed pixel size, so a 1:1
# marketplace crop and a 3:4 hero get branding of the same visual weight.
LOGO_WIDTH_FRACTION = 0.14
PADDING_FRACTION = 0.04
TEXT_HEIGHT_FRACTION = 0.022
GAP_FRACTION = 0.012

# A contact line wider than this stops reading as a signature and starts reading as a
# stock-photo watermark across the bottom of someone's campaign image. Indian contact
# lines are long — name, +91 number, domain — so this is what actually binds, not the
# padding.
TEXT_MAX_WIDTH_FRACTION = 0.55

# Mean luminance above this counts as a light backdrop, so the branding flips to dark
# ink. Nothing else here can save white type on a cream marble floor.
LIGHT_BACKDROP = 145

DEFAULT_OPACITY = 70            # percent
MIN_TEXT_PX = 13                # below this it is decoration, not contact information


def font_for(text: str, size: int) -> ImageFont.FreeTypeFont:
    """Pick the face that can actually draw this string.

    Per-string rather than per-character: mixing faces mid-line lands glyphs on
    different baselines with different metrics, which looks worse than either face alone.
    """
    if any(ord(c) in DEVANAGARI_RANGE for c in text) and DEVANAGARI_FONT.exists():
        return ImageFont.truetype(str(DEVANAGARI_FONT), size)
    return ImageFont.truetype(str(LATIN_FONT), size)


def unsupported(text: str) -> list[str]:
    """Characters neither bundled font can draw, so the caller can say so up front.

    Detected by rendering rather than by codepoint ranges: a missing glyph produces the
    face's .notdef box, and EVERY missing glyph in a face produces the identical box.
    That is what distinguishes "this font has \u0915" from "this font has a rectangle
    where \u0915 should be" — the check that stops tofu being burned onto a paid image.
    """
    faces = [ImageFont.truetype(str(path), 40)
             for path in (LATIN_FONT, DEVANAGARI_FONT) if path.exists()]
    if not faces:
        return []
    # Fine if ANY bundled face can draw it; font_for() decides which one actually does.
    return [c for c in dict.fromkeys(text)
            if not c.isspace() and not any(_drawable(f, c) for f in faces)]


# Codepoints permanently unassigned by Unicode, so whatever a face draws for them IS
# that face's .notdef box.
#
# NOT a Private Use Area codepoint: Inter ships a real glyph at U+E000, so a PUA
# sentinel measured 108115 while Inter's actual .notdef is 119888 \u2014 and every missing
# character then looked drawable. More than one sentinel, because a font is free to
# surprise us again; a character matching ANY of them is missing.
NOTDEF_PROBES = ('\uffff', '\U0010FFFF', '\ufdd0')


def _drawable(font: ImageFont.FreeTypeFont, char: str) -> bool:
    mark = _ink(font, char)
    if mark == _ink(font, ' '):
        return False
    return all(mark != _ink(font, probe) for probe in NOTDEF_PROBES)


def _ink(font: ImageFont.FreeTypeFont, char: str) -> int:
    canvas = Image.new('L', (90, 90), 0)
    ImageDraw.Draw(canvas).text((10, 10), char, font=font, fill=255)
    return sum(canvas.tobytes())


def apply(image_bytes: bytes, logo_bytes: bytes | None = None, text: str = '',
          position: str = DEFAULT_POSITION, opacity: int = DEFAULT_OPACITY) -> bytes:
    """Return a new PNG with the branding stamped on. The input is never modified."""
    if position not in POSITIONS:
        position = DEFAULT_POSITION
    opacity = max(10, min(100, int(opacity)))

    base = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
    width, height = base.size
    pad = int(width * PADDING_FRACTION)
    gap = int(width * GAP_FRACTION)

    # Drawn on its own layer and alpha-composited once, so the logo and the text share a
    # single opacity and neither can darken the other where they overlap.
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))

    block_width = 0
    block_height = 0
    logo = None
    if logo_bytes:
        logo = Image.open(io.BytesIO(logo_bytes)).convert('RGBA')
        target_width = max(1, int(width * LOGO_WIDTH_FRACTION))
        scale = target_width / logo.width
        logo = logo.resize((target_width, max(1, int(logo.height * scale))),
                           Image.LANCZOS)
        block_width, block_height = logo.width, logo.height

    font = None
    text_width = text_height = 0
    if text:
        size = max(MIN_TEXT_PX, int(height * TEXT_HEIGHT_FRACTION))
        font = font_for(text, size)
        # Shrink to fit. A long contact line — and Indian ones are long, with a name, a
        # +91 number and a domain — otherwise runs to the padding edge or past it, which
        # is the difference between branding and damage.
        usable = min(width - 2 * pad, int(width * TEXT_MAX_WIDTH_FRACTION))
        while size > MIN_TEXT_PX and font.getlength(text) > usable:
            size -= 1
            font = font_for(text, size)
        box = font.getbbox(text)
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        block_width = max(block_width, text_width)
        block_height += (gap if logo else 0) + text_height

    if not block_width:
        return image_bytes                      # nothing to stamp

    left, top = _anchor(position, width, height, block_width, block_height, pad)

    # Read the backdrop the branding will actually land on rather than assuming a dark
    # one. A fixed white treatment disappears on a cream marble floor or a sunlit
    # Santorini wall, and those are locations this product ships.
    patch = base.convert('L').crop((left, top,
                                    min(width, left + block_width),
                                    min(height, top + block_height)))
    light_backdrop = (sum(patch.tobytes()) / max(1, len(patch.tobytes()))) > LIGHT_BACKDROP
    ink = (28, 26, 24, 255) if light_backdrop else (255, 255, 255, 255)
    halo_colour = (255, 255, 255, 170) if light_backdrop else (0, 0, 0, 190)

    cursor = top
    if logo is not None:
        # Centred within the block so a wide contact line does not leave the mark
        # hanging off to one side.
        spot = (left + (block_width - logo.width) // 2, cursor)
        # The same soft halo the text gets, built from the logo's own alpha so it traces
        # the mark rather than boxing it. A white wordmark on a cream marble floor is
        # otherwise as invisible as white type on the same floor.
        # The logo's halo contrasts the LOGO, not the backdrop. We cannot recolour a
        # client's mark, so the only way to keep a white wordmark visible on cream — or
        # a black one on a dark saree — is to outline it in its own opposite. Judging
        # this by the backdrop instead is what left a white logo ghosted on a light
        # floor while its own contact line underneath read perfectly.
        logo_halo = (0, 0, 0, 190) if _is_light(logo) else (255, 255, 255, 200)
        halo = Image.new('RGBA', base.size, (0, 0, 0, 0))
        halo.paste(Image.new('RGBA', logo.size, logo_halo), spot,
                   logo.getchannel('A'))
        # Composited twice. A single pass at 190 alpha survives the opacity multiply
        # below as ~133, which is not enough separation for a white mark on cream —
        # the case that sent it invisible in testing.
        blurred = halo.filter(ImageFilter.GaussianBlur(max(3, logo.width // 28)))
        layer.alpha_composite(blurred)
        layer.alpha_composite(blurred)
        layer.alpha_composite(logo, spot)
        cursor += logo.height + gap

    if text and font is not None:
        x = left + (block_width - text_width) // 2
        offset = font.getbbox(text)[1]
        # A BLURRED shadow, not a hard offset. Two-pixel hard shadows are invisible
        # against a bright backdrop — white type on a sunlit wall simply disappears,
        # which is the one thing this must never do on a delivered image. A soft dark
        # halo works on both a dark saree and a cream marble floor without a scrim
        # heavy enough to look like a stock-photo watermark.
        halo = Image.new('RGBA', base.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).text((x, cursor - offset), text, font=font,
                                  fill=halo_colour)
        halo = halo.filter(ImageFilter.GaussianBlur(max(2, font.size // 6)))
        layer.alpha_composite(halo)
        layer.alpha_composite(halo)
        ImageDraw.Draw(layer).text((x, cursor - offset), text, font=font, fill=ink)

    if opacity < 100:
        alpha = layer.getchannel('A').point(lambda v: int(v * opacity / 100))
        layer.putalpha(alpha)

    out = io.BytesIO()
    Image.alpha_composite(base, layer).convert('RGB').save(out, format='PNG')
    return out.getvalue()


def _is_light(logo: Image.Image) -> bool:
    """Is the mark itself light? Measured only where it is opaque.

    Averaging the whole bitmap would answer "mostly transparent", which is true of every
    logo and useless — the transparent pixels are exactly the ones that are not the mark.
    """
    grey = logo.convert('L')
    alpha = logo.getchannel('A')
    total = weight = 0
    for value, mask in zip(grey.tobytes(), alpha.tobytes()):
        if mask > 40:
            total += value * mask
            weight += mask
    return weight > 0 and (total / weight) > 128


def _anchor(position: str, width: int, height: int,
            block_width: int, block_height: int, pad: int) -> tuple[int, int]:
    top = pad if position.startswith('top') else height - block_height - pad
    if position.endswith('centre'):
        left = (width - block_width) // 2
    elif position.endswith('left'):
        left = pad
    else:
        left = width - block_width - pad
    return max(0, left), max(0, top)


def demo() -> None:
    """Self-check: geometry, glyph coverage, and that the original is never touched."""
    # A stand-in photograph and a stand-in logo, both made here so this needs no assets.
    photo = Image.new('RGB', (1792, 2400), (190, 170, 150))
    buf = io.BytesIO(); photo.save(buf, format='PNG')
    photo_bytes = buf.getvalue()

    mark = Image.new('RGBA', (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(mark).ellipse((0, 0, 399, 119), fill=(255, 255, 255, 255))
    buf = io.BytesIO(); mark.save(buf, format='PNG')
    logo_bytes = buf.getvalue()

    line = 'Kalyan Jewellers · +91 98765 43210 · kalyan.com'
    out = apply(photo_bytes, logo_bytes, line)
    assert out != photo_bytes, 'nothing was stamped'
    assert Image.open(io.BytesIO(out)).size == (1792, 2400), 'the image was resized'
    # The input bytes are the stored master and must come back untouched.
    assert Image.open(io.BytesIO(photo_bytes)).getpixel((10, 10)) == (190, 170, 150)

    # Something must actually change in the corner the branding was asked for, and
    # nothing in the opposite one.
    def corner(data, box):
        return Image.open(io.BytesIO(data)).convert('RGB').crop(box).tobytes()
    br = (1500, 2100, 1780, 2380)
    tl = (10, 10, 290, 290)
    assert corner(out, br) != corner(photo_bytes, br), 'bottom-right is unchanged'
    assert corner(out, tl) == corner(photo_bytes, tl), 'top-left was touched'

    top_left = apply(photo_bytes, logo_bytes, line, position='top-left')
    assert corner(top_left, tl) != corner(photo_bytes, tl), 'top-left did nothing'

    for spot in POSITIONS:
        assert apply(photo_bytes, logo_bytes, line, position=spot) != photo_bytes, spot
    # An unknown position must fall back, never crash a paid download.
    assert apply(photo_bytes, logo_bytes, line, position='nowhere') != photo_bytes

    # Text alone, and logo alone, are both valid configurations.
    assert apply(photo_bytes, None, line) != photo_bytes
    assert apply(photo_bytes, logo_bytes, '') != photo_bytes
    # Neither is a no-op that returns the original rather than an error.
    assert apply(photo_bytes, None, '') == photo_bytes

    # Opacity is clamped, not trusted: it arrives from a form.
    for bad in (-50, 0, 500, 101):
        assert apply(photo_bytes, logo_bytes, line, opacity=bad) != photo_bytes

    # A tiny image must still produce readable type rather than sub-pixel decoration.
    small = Image.new('RGB', (400, 400), (30, 30, 30))
    buf = io.BytesIO(); small.save(buf, format='PNG')
    assert apply(buf.getvalue(), logo_bytes, 'Kalyan · kalyan.com') != buf.getvalue()

    # Glyph coverage, which is the thing that silently ships boxes if it regresses.
    assert unsupported('Kalyan Jewellers · +91 98765 · ₹1,050') == [], \
        'Latin, the rupee sign and a middot must all be drawable'
    assert unsupported('कल्याण ज्वेलर्स') == [], 'Devanagari must be drawable'
    tamil = unsupported('கல்யாண்')
    assert tamil, 'Tamil is not bundled and must be reported, not silently boxed'

    assert LATIN_FONT.exists() and DEVANAGARI_FONT.exists(), 'a font is missing'
    print('branding ok')


if __name__ == '__main__':
    demo()
