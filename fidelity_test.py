"""Does the product survive generation?

The whole demo rests on one unknown: can any available endpoint place the client's
ACTUAL earring on a model without inventing a different earring? This runs the same
product image and prompt through every image-conditioned endpoint on the account so
the outputs can be compared side by side.

    .venv/bin/python fidelity_test.py path/to/earring.jpg

Costs roughly 3 credits per approach. Estimates are printed and confirmed first.
"""

import sys
import pathlib

import higgsfield_client

import hf

# Shot brief held constant across approaches so only the endpoint varies.
PROMPT = (
    'Photorealistic editorial jewellery campaign photograph of an Indian woman, head '
    'turned slightly to show her ear, wearing the exact earrings from the reference '
    'image: yellow gold kite-shaped diamond studs with a fine gold chain dropping to a '
    'small kite-shaped diamond pendant. The earring design must be unchanged in shape, '
    'proportion, stone layout and metalwork. Hair tucked behind the ear so the full '
    'earring including the chain drop is visible. Elegant silk saree, natural warm '
    'daylight, shallow depth of field, tack sharp focus on the earring, '
    'luxury jewellery brand catalogue quality.'
)

APPROACHES = {
    # Popcorn takes a list of references and is the closest thing to product conditioning.
    'popcorn': (
        'higgsfield-ai/popcorn/auto',
        lambda url: {
            'prompt': PROMPT,
            'image_urls': [url],
            'num_images': 2,
            'resolution': '1600p',
            'aspect_ratio': '3:4',
        },
    ),
    # Soul reference conditions on a single image; strength controls how literally.
    'soul-reference': (
        'higgsfield-ai/soul/reference',
        lambda url: {
            'prompt': PROMPT,
            'image_reference_url': url,
            'resolution': '1080p',
            'aspect_ratio': '3:4',
            'batch_size': 1,
            'style_strength': 1,
            'enhance_prompt': True,
        },
    ),
    # Baseline with no product conditioning at all — proves what we LOSE without it.
    'soul-standard-baseline': (
        'higgsfield-ai/soul/standard',
        lambda _url: {
            'prompt': PROMPT,
            'num_images': 1,
            'resolution': '1080p',
            'aspect_ratio': '3:4',
        },
    ),
}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    product = pathlib.Path(sys.argv[1])
    if not product.exists():
        sys.exit(f'no such file: {product}')

    print(f'uploading {product.name} ...')
    product_url = hf.upload(product)
    print(f'  -> {product_url}\n')

    plans = []
    total = 0.0
    for name, (model_path, build) in APPROACHES.items():
        arguments = build(product_url)
        cost = hf.estimate(f'/{model_path}', arguments)
        total += float(cost['credits'])
        plans.append((name, model_path, arguments, cost))
        print(f'{name:24} {cost["credits"]:>7} credits  (${cost["usd"]})')

    print(f'\ntotal {total:.3f} credits')
    if input('run? [y/N] ').strip().lower() != 'y':
        sys.exit('aborted')

    for name, model_path, arguments, _cost in plans:
        print(f'\n--- {name}')
        try:
            result = higgsfield_client.subscribe(model_path, arguments=arguments)
        except Exception as error:
            print(f'  FAILED: {error}')
            continue
        urls = hf.output_urls(result)
        if not urls:
            print(f'  no images returned: status={result.get("status")}')
            continue
        for path in hf.download(urls, 'out/fidelity', prefix=name):
            print(f'  {path}')


if __name__ == '__main__':
    main()
