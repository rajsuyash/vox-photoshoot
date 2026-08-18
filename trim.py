"""Crop the white mount the model sometimes draws around an image.

"no white border" was ignored twice, like every other negative instruction in this
project. Cropping it is deterministic, free, and cannot regress.

    .venv/bin/python trim.py assets/locations/*.png
"""

import pathlib
import sys

from PIL import Image, ImageChops

# A mount is near-white but rarely pure white after JPEG-ish generation noise.
WHITE_FLOOR = 244
# Below this, the "border" is really just a pale sky and must be left alone.
MAX_BORDER_FRACTION = 0.12


def find_content_box(image: Image.Image):
    """Bounding box of the non-white content, or None if there is no mount."""
    grey = image.convert('L')
    # point() maps near-white to black so getbbox() sees the mount as empty.
    mask = grey.point(lambda value: 0 if value >= WHITE_FLOOR else 255)
    box = mask.getbbox()
    if box is None:
        return None

    left, top, right, bottom = box
    trimmed = max(left, top, image.width - right, image.height - bottom)
    if trimmed == 0:
        return None
    if trimmed > min(image.width, image.height) * MAX_BORDER_FRACTION:
        # Too much: this is a bright sky or a white wall, not a mount.
        return None
    return box


def trim(path) -> bool:
    """Crop in place. Returns True if anything was removed."""
    path = pathlib.Path(path)
    with Image.open(path) as image:
        image = image.convert('RGB')
        box = find_content_box(image)
        if box is None:
            return False
        image.crop(box).save(path)
    return True


def demo() -> None:
    # A red square inside a white mount must be recovered exactly.
    canvas = Image.new('RGB', (120, 100), (255, 255, 255))
    canvas.paste(Image.new('RGB', (100, 80), (200, 30, 30)), (10, 10))
    assert find_content_box(canvas) == (10, 10, 110, 90)

    # A full-bleed image has no mount to find.
    assert find_content_box(Image.new('RGB', (50, 50), (12, 90, 200))) is None

    # A mostly-white image is left alone rather than cropped to nothing.
    sky = Image.new('RGB', (100, 100), (255, 255, 255))
    sky.paste(Image.new('RGB', (20, 20), (10, 10, 10)), (40, 40))
    assert find_content_box(sky) is None
    print('trim ok')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        demo()
    else:
        for argument in sys.argv[1:]:
            print(f'{"trimmed " if trim(argument) else "clean   "} {argument}')
