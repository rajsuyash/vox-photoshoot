"""Generate one sample image per location.

Serves two purposes at once: proves every preset actually renders, and produces the
thumbnail each location card needs in the picker UI. The model is held constant so the
only thing varying between cards is the location itself.

    .venv/bin/python gallery.py            # any locations not yet generated
    .venv/bin/python gallery.py --redo     # regenerate everything
"""

import json
import pathlib
import sys

import higgsfield_client

import hf
import locations
import shoot
from pave_test import CONTROL

GALLERY_DIR = pathlib.Path('assets/locations')
MANIFEST = GALLERY_DIR / 'gallery.json'
# One model across every card: the card sells the PLACE, so everything else holds still.
MODEL_KEY = 'aditi'
# 'hero' shows the location; 'detail' would crop it out entirely.
FRAMING = 'hero'

# The A/B went the other way. The SHORT description with ONE reference rendered the pavé
# correctly; the long 'pavé set ... kite pendant' wording with two references flattened it
# to a single cluster and hallucinated a matching necklace — most likely triggered by the
# word 'pendant'. Keep product text short, and never name a jewellery type the client is
# not selling.
PRODUCT = CONTROL


def main() -> None:
    # One clean front view only. The second client photo is a back view and the pavé A/B
    # showed extra product references muddy the geometry.
    product = sorted(pathlib.Path('clientphoto').glob('*.jpg'))[0]
    urls = [hf.upload(product)]
    face_url = hf.upload(shoot.load_cast()[MODEL_KEY]['file'])

    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {} if '--redo' in sys.argv else (
        json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    )

    pending = [key for key in locations.ALL if key not in manifest]
    if not pending:
        print('gallery already complete')
        return

    estimate = shoot.cost(urls, MODEL_KEY, pending[0], PRODUCT, framing=FRAMING)
    print(f'{len(pending)} locations, ~{estimate["credits"]} credits each '
          f'(~{len(pending) * float(estimate["credits"]):.1f} total)\n')

    for key in pending:
        location = locations.ALL[key]
        print(f'{key} ({location.label}) ...')
        _prompt, arguments = shoot.build(
            urls, MODEL_KEY, key, PRODUCT, face_url=face_url, framing=FRAMING
        )
        result = higgsfield_client.subscribe(shoot.MODEL_PATH, arguments=arguments)
        output = hf.output_urls(result)
        if not output:
            print(f'  FAILED status={result.get("status")}')
            continue
        [saved] = hf.download(output, GALLERY_DIR, prefix=key)
        manifest[key] = {
            'label': location.label,
            'region': location.region,
            'file': str(saved),
        }
        MANIFEST.write_text(json.dumps(manifest, indent=1))
        print(f'  {saved}')

    print(f'\n{len(manifest)}/{len(locations.ALL)} locations have thumbnails')


if __name__ == '__main__':
    main()
