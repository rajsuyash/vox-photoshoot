"""Shrink the shipped assets to the size they are actually used at.

The generator writes full resolution PNGs — 5.8MB for a portrait, 3.3MB for a plate —
and every one of them goes into git and into the Docker image. At fifty locations and
twenty-eight faces that is over 400MB of container for pictures the browser renders at
250 pixels wide.

The two are used differently, so they shrink differently:

  Location plates are ONLY the picker card. Nothing uploads them anywhere and no shoot
  reads them — the shoot composes its background from location.scene as text. A card
  shown at 250px needs 900px for a retina display and nothing more.

  Cast portraits ARE uploaded to the provider as the face reference that holds identity
  across a shoot. They keep real resolution; they simply do not need to be lossless PNG
  at 1792x2400.

    .venv/bin/python optimise_assets.py            # report only
    .venv/bin/python optimise_assets.py --write    # rewrite and update the manifests
"""

import json
import pathlib
import sys

from PIL import Image

CAST = pathlib.Path('assets/cast')
LOCATIONS = pathlib.Path('assets/locations')

# Portraits go to the provider as an identity reference, so they keep the long edge that
# a face needs. Plates are a thumbnail and nothing else.
PORTRAIT_EDGE, PORTRAIT_QUALITY = 1280, 90
PLATE_EDGE, PLATE_QUALITY = 900, 82


def shrink(path: pathlib.Path, edge: int, quality: int, write: bool):
    """Return (old_bytes, new_bytes, new_path). Writes a JPEG beside the original."""
    old = path.stat().st_size
    with Image.open(path) as image:
        image = image.convert('RGB')
        image.thumbnail((edge, edge), Image.LANCZOS)
        target = path.with_suffix('.jpg')
        if write:
            image.save(target, format='JPEG', quality=quality, optimize=True,
                       progressive=True)
            if path.suffix.lower() != '.jpg':
                path.unlink()
        new = target.stat().st_size if write and target.exists() else old // 20
    return old, new, target


def main() -> None:
    write = '--write' in sys.argv
    before = after = 0

    manifest_path = LOCATIONS / 'gallery.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for key, entry in manifest.items():
        path = pathlib.Path(entry['file'])
        if not path.exists():
            print(f'  {key}: missing {path}')
            continue
        old, new, target = shrink(path, PLATE_EDGE, PLATE_QUALITY, write)
        before += old
        after += new
        if write:
            entry['file'] = str(target)
    if write:
        manifest_path.write_text(json.dumps(manifest, indent=1))

    cast_path = CAST / 'cast.json'
    cast = json.loads(cast_path.read_text()) if cast_path.exists() else {}
    for name, entry in cast.items():
        path = pathlib.Path(entry['file'])
        if not path.exists():
            print(f'  {name}: missing {path}')
            continue
        old, new, target = shrink(path, PORTRAIT_EDGE, PORTRAIT_QUALITY, write)
        before += old
        after += new
        if write:
            entry['file'] = str(target)
    if write:
        cast_path.write_text(json.dumps(cast, indent=1))

    print(f'  {len(manifest)} plates + {len(cast)} portraits')
    print(f'  {before/1e6:.0f}MB -> {after/1e6:.0f}MB'
          f'  ({100 - after/max(1, before)*100:.0f}% smaller)')
    if not write:
        print('  (estimate only — run with --write to apply)')


if __name__ == '__main__':
    main()
