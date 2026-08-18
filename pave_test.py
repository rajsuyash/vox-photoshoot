"""Can we recover the pavé setting?

popcorn/auto preserved the earring's shape and chain but rendered its pavé (a grid of
small diamonds) as one large solitaire. Two candidate causes: too little detail in the
single 640px reference, and a product description that never says "pavé". This varies
both against a control, holding model and location fixed.
"""

import pathlib

import higgsfield_client

import hf
import locations
import shoot

PRODUCTS = sorted(pathlib.Path('clientphoto').glob('*.jpg'))  # empty outside dev

CONTROL = locations.DEFAULT_PRODUCT
CANDIDATE = (
    'yellow gold kite-shaped drop earrings, each kite frame pavé set with a cluster of '
    'many tiny round brilliant diamonds rather than one large stone, suspended from a '
    'fine gold cable chain ending in a small pavé set kite pendant'
)

def variants():
    """Built lazily: PRODUCTS is empty outside the dev machine."""
    return {
        # control: one reference, no pavé wording — reproduces the known result
        'control': ([PRODUCTS[0]], CONTROL),
        # candidate: both references (front and back views) plus explicit pavé wording
        'pave-fix': (PRODUCTS, CANDIDATE),
    }

MODEL_KEY, LOCATION_KEY = 'aditi', 'amber-fort'


def main() -> None:
    uploaded = {path: hf.upload(path) for path in PRODUCTS}
    print(f'uploaded {len(uploaded)} product photos\n')

    for name, (paths, product) in variants().items():
        urls = [uploaded[path] for path in paths]
        estimate = shoot.cost(urls, MODEL_KEY, LOCATION_KEY, product)
        print(f'{name}: {len(urls)} ref(s), {estimate["credits"]} credits')

        _prompt, arguments = shoot.build(urls, MODEL_KEY, LOCATION_KEY, product)
        result = higgsfield_client.subscribe(shoot.MODEL_PATH, arguments=arguments)
        output = hf.output_urls(result)
        if not output:
            print(f'  FAILED status={result.get("status")}')
            continue
        for saved in hf.download(output, 'out/pave', prefix=name):
            print(f'  {saved}')


if __name__ == '__main__':
    main()
