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

import higgsfield_client

import hf
import locations
import trim

MODEL_PATH = 'higgsfield-ai/popcorn/auto'
GALLERY_DIR = pathlib.Path('assets/locations')
MANIFEST = GALLERY_DIR / 'gallery.json'

# Landscape, and higher resolution than a thumbnail needs: these plates are also the
# compositing backplate, so they want the detail.
ARGUMENTS = {
    'num_images': 1,
    'resolution': '1600p',
    'aspect_ratio': '4:3',
}


def main() -> None:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {} if '--redo' in sys.argv else (
        json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    )
    pending = [key for key in locations.ALL if key not in manifest]
    if not pending:
        print('gallery already complete')
        return

    sample = {'prompt': locations.compose_plate(pending[0]), **ARGUMENTS}
    estimate = hf.estimate(f'/{MODEL_PATH}', sample)
    print(f'{len(pending)} plates x {estimate["credits"]} credits = '
          f'{len(pending) * float(estimate["credits"]):.1f} total\n')

    for key in pending:
        location = locations.ALL[key]
        print(f'{key} ({location.label}) ...')
        result = higgsfield_client.subscribe(
            MODEL_PATH,
            arguments={'prompt': locations.compose_plate(key), **ARGUMENTS},
        )
        urls = hf.output_urls(result)
        if not urls:
            print(f'  FAILED status={result.get("status")}')
            continue
        [saved] = hf.download(urls, GALLERY_DIR, prefix=key)
        # The model draws a white mount now and then and ignores every instruction not
        # to; cropping it is deterministic where the prompt is not.
        if trim.trim(saved):
            print('  trimmed white border')
        manifest[key] = {
            'label': location.label,
            'region': location.region,
            'file': str(saved),
        }
        MANIFEST.write_text(json.dumps(manifest, indent=1))
        print(f'  {saved}')

    print(f'\n{len(manifest)}/{len(locations.ALL)} locations have plates')


if __name__ == '__main__':
    main()
