"""Generate one empty location plate per location.

These are NOT shoots. They are photographs of the place with nobody in them: the image
on each location card in the picker, and the backplate a model gets composited onto in
the next step. No model, no product, no wardrobe, nothing blurred.

    .venv/bin/python gallery.py            # any locations not yet generated
    .venv/bin/python gallery.py --redo     # regenerate everything
"""

import json
import pathlib
import sys

import concurrent.futures

import hf
import locations
import providers
import trim

GALLERY_DIR = pathlib.Path('assets/locations')
MANIFEST = GALLERY_DIR / 'gallery.json'

# Landscape, and higher resolution than a thumbnail needs: these plates are also the
# compositing backplate, so they want the detail.
ASPECT = '4:3'

# Plates are independent of one another and each takes about a minute at the provider,
# so fifty of them serially is the better part of an hour of waiting. Six at a time is
# the same ceiling the app puts on its own threadpool.
WORKERS = 6


def main() -> None:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {} if '--redo' in sys.argv else (
        json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    )
    pending = [key for key in locations.ALL if key not in manifest]
    if not pending:
        print('gallery already complete')
        return

    print(f'{len(pending)} plates, about ${len(pending) * 0.15:.2f} at fal\n')
    if '--yes' not in sys.argv and input('run? [y/N] ').strip().lower() != 'y':
        sys.exit('aborted')

    def make(key: str):
        location = locations.ALL[key]
        urls = providers.get().generate(
            locations.compose_plate(key), aspect_ratio=ASPECT,
            quality='high', num_images=1)
        if not urls:
            raise RuntimeError('no images returned')
        [saved] = hf.download(urls, GALLERY_DIR, prefix=key)
        # The model draws a white mount now and then and ignores every instruction not
        # to; cropping it is deterministic where the prompt is not.
        trimmed = trim.trim(saved)
        return key, {'label': location.label, 'region': location.region,
                     'file': str(saved)}, trimmed

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(make, key): key for key in pending}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                key, entry, trimmed = future.result()
            except Exception as error:      # noqa: BLE001 - one plate, not the run
                print(f'{key}: FAILED {error}')
                continue
            manifest[key] = entry
            # Written after every plate, not at the end: fifty generations is long
            # enough that a crash at plate forty should not throw away thirty-nine.
            MANIFEST.write_text(json.dumps(manifest, indent=1))
            print(f'{key}: {entry["file"]}{" (trimmed)" if trimmed else ""}')

    print(f'\n{len(manifest)}/{len(locations.ALL)} locations have plates')


if __name__ == '__main__':
    main()
