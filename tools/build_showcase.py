"""Build the login page's mosaic from real shoot output.

The login page is the one page served before a session exists, so it cannot use
/media — that route is behind auth. These are copied into static/ instead, where the
StaticFiles mount serves them to strangers.

Hand-picked, not globbed: out/shoots holds retries, placeholders and duplicate
framings of the same piece. The point of the wall is range — different faces, ages,
locations and framings — which a glob does not give you.

    .venv/bin/python tools/build_showcase.py
"""

import pathlib

from PIL import Image

OUT = pathlib.Path('static/showcase')
EDGE, QUALITY = 560, 76

# (source, crop) — 'tall' keeps the 3:4 the generator writes, 'square' centre-crops.
# Mixing the two is what stops the wall reading as a spreadsheet.
PICKS = [
    ('out/shoots/264506264c39/simran-taj-mahal-hero-0.png', 'tall'),
    ('out/shoots/3bb220c7d4ea/aditi-kyoto-detail-0.png', 'square'),
    ('out/shoots/e6155d2d1d12/kavya-kerala-backwaters-detail-0.png', 'tall'),
    ('out/shoots/aditi-amber-fort-detail-0.png', 'square'),
    ('out/shoots/78bee64709eb/priya-udaipur-palace-detail-0.png', 'square'),
    ('out/shoots/efdeb802159b/aditi-santorini-hero-0.png', 'tall'),
    ('out/shoots/92311fa6dea2/aditi-kyoto-detail-0.png', 'tall'),
    ('out/shoots/ee18ded9b0b1/priya-udaipur-palace-detail-0.png', 'square'),
    ('out/shoots/1874f5331b52/kavya-kerala-backwaters-detail-0.png', 'square'),
    ('out/shoots/aditi-amber-fort-hero-0.png', 'tall'),
    ('out/shoots/747f2b8a-37a9-4f35-9e95-d032796a1e2d/detail-1-0.png', 'square'),
    ('out/shoots/ecd5117f77b5/simran-kerala-backwaters-hero-0.png', 'tall'),
    ('out/shoots/48c5d6d68bd1/lakshmi-udaipur-palace-profile-0.png', 'tall'),
    ('out/shoots/aditi-amber-fort-profile-0.png', 'square'),
    ('out/shoots/ee18ded9b0b1/priya-udaipur-palace-hero-0.png', 'tall'),
    ('out/shoots/efdeb802159b/aditi-santorini-profile-0.png', 'square'),
]


def square(image: Image.Image) -> Image.Image:
    """Centre-crop, but biased to the top third — the face is never at the middle."""
    side = min(image.size)
    left = (image.width - side) // 2
    top = min((image.height - side) // 3, image.height - side)
    return image.crop((left, top, left + side, top + side))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for index, (source, crop) in enumerate(PICKS):
        path = pathlib.Path(source)
        if not path.exists():
            raise SystemExit(f'missing {path} — regenerate it or drop it from PICKS')
        with Image.open(path) as image:
            image = image.convert('RGB')
            if crop == 'square':
                image = square(image)
            image.thumbnail((EDGE, EDGE), Image.LANCZOS)
            target = OUT / f'{index:02d}.jpg'
            image.save(target, format='JPEG', quality=QUALITY, optimize=True,
                       progressive=True)
        total += target.stat().st_size
        print(f'{target}  {image.size[0]}x{image.size[1]}  '
              f'{target.stat().st_size // 1024}KB')
    print(f'\n{len(PICKS)} images, {total // 1024}KB total')


if __name__ == '__main__':
    main()
