"""Tile labelled images into one sheet.

Used for the location picker thumbnails and for judging A/B runs side by side, which is
the only practical way to compare faces and fine jewellery detail.
"""

import pathlib

from PIL import Image, ImageDraw, ImageFont

THUMB_WIDTH = 520
LABEL_HEIGHT = 54
PADDING = 12
BACKGROUND = (18, 18, 18)
TEXT = (238, 238, 238)


def _font(size: int):
    for candidate in ('/System/Library/Fonts/Supplemental/Arial.ttf',
                      '/System/Library/Fonts/Helvetica.ttc'):
        if pathlib.Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def build(items, output, columns: int = 5, thumb_width: int = THUMB_WIDTH):
    """items: iterable of (label, image path). Returns the written path."""
    thumbs = []
    for label, path in items:
        image = Image.open(path).convert('RGB')
        height = round(image.height * thumb_width / image.width)
        thumbs.append((label, image.resize((thumb_width, height), Image.LANCZOS)))

    if not thumbs:
        raise ValueError('nothing to montage')

    columns = min(columns, len(thumbs))
    cell_height = max(image.height for _label, image in thumbs) + LABEL_HEIGHT
    rows = -(-len(thumbs) // columns)

    sheet = Image.new(
        'RGB',
        (columns * thumb_width + (columns + 1) * PADDING,
         rows * cell_height + (rows + 1) * PADDING),
        BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)
    font = _font(26)

    for index, (label, image) in enumerate(thumbs):
        column, row = index % columns, index // columns
        x = PADDING + column * (thumb_width + PADDING)
        y = PADDING + row * (cell_height + PADDING)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + image.height + 14), label, fill=TEXT, font=font)

    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)
    return output


def demo() -> None:
    sample = next(pathlib.Path('assets/cast').glob('*.png'))
    out = build([('one', sample), ('two', sample)], 'out/_montage-selfcheck.jpg', columns=2)
    assert out.exists() and out.stat().st_size > 0
    out.unlink()
    print('montage ok')


if __name__ == '__main__':
    demo()
