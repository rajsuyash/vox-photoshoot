"""Run one photoshoot: product + model + location -> images.

This is the whole generation backend. The web app will call shoot() and nothing else.

    .venv/bin/python shoot.py clientphoto/50D5L1DOUAGA2A_1.jpg aditi amber-fort
"""

import json
import pathlib
import sys

import higgsfield_client

import hf
import locations

MODEL_PATH = 'higgsfield-ai/popcorn/auto'
CAST_MANIFEST = pathlib.Path('assets/cast/cast.json')

# popcorn/auto was the only endpoint on the account that preserved the client's actual
# product; soul/reference redesigned it and soul/standard ignored it. See fidelity_test.py.
DEFAULTS = {
    'num_images': 1,
    'resolution': '1600p',
    'aspect_ratio': '3:4',   # 4:5 is not offered by any live image model; crop for social
}

# Fixed per framing so a shoot is reproducible: same inputs re-run give the same set,
# which matters when a client asks for "the one from yesterday, but bigger".
SEEDS = {'hero': 101, 'profile': 202, 'detail': 303}


def load_cast() -> dict:
    if not CAST_MANIFEST.exists():
        raise RuntimeError('no cast yet — run cast.py first')
    return json.loads(CAST_MANIFEST.read_text())


def build(product_urls: list[str], model_key: str, location_key: str,
          product: str, face_url: str | None = None,
          framing: str = 'hero') -> tuple[str, dict]:
    """Return (prompt, arguments) without calling the API, so cost can be checked first.

    face_url is the cast portrait. Passing the face as prompt text alone re-rolls a
    different woman every generation and drifts non-Indian at foreign locations — see
    identity_test.py. The portrait has to go into image_urls to hold identity.
    """
    cast = load_cast()
    if model_key not in cast:
        raise KeyError(f'unknown model {model_key!r}; have {sorted(cast)}')
    if location_key not in locations.ALL:
        raise KeyError(f'unknown location {location_key!r}; have {sorted(locations.ALL)}')

    prompt = locations.compose(
        product=product,
        model_description=f'An Indian woman, {cast[model_key]["description"]}',
        location_key=location_key,
        framing=framing,
    )
    # Product first: it is the reference that must not be compromised.
    image_urls = [*product_urls, face_url] if face_url else list(product_urls)
    return prompt, {
        'prompt': prompt,
        'image_urls': image_urls,
        'seed': SEEDS[framing],
        **DEFAULTS,
    }


def shoot(product_paths, model_key: str, location_key: str, product: str,
          framings=None, out_dir='out/shoots') -> list[pathlib.Path]:
    """Run one photoshoot: one generation per framing, saved locally.

    This is the single entry point the web app calls.
    """
    framings = list(framings or locations.FRAMINGS)
    product_urls = [hf.upload(path) for path in product_paths]
    face_url = hf.upload(load_cast()[model_key]['file'])

    saved, failures = [], []
    for framing in framings:
        _prompt, arguments = build(
            product_urls, model_key, location_key, product, face_url, framing
        )
        try:
            result = higgsfield_client.subscribe(MODEL_PATH, arguments=arguments)
        except Exception as error:
            failures.append((framing, str(error)))
            continue

        urls = hf.output_urls(result)
        if not urls:
            # Usually 'nsfw' — content moderation rejects a frame now and then. Reported
            # rather than dropped: a shoot silently returning 2 of 3 looks like a bug.
            failures.append((framing, result.get('status') or 'no images returned'))
            continue
        saved += hf.download(
            urls, out_dir, prefix=f'{model_key}-{location_key}-{framing}'
        )
    return saved, failures


def cost(product_urls, model_key: str, location_key: str, product: str,
         framing: str = 'hero') -> dict:
    _prompt, arguments = build(
        product_urls, model_key, location_key, product, framing=framing
    )
    return hf.estimate(f'/{MODEL_PATH}', arguments)


def demo() -> None:
    """Self-check: prompt assembly and validation, without spending credits."""
    prompt, arguments = build(
        ['https://example.com/a.jpg'], 'aditi', 'kyoto',
        product='yellow gold kite-shaped diamond drop earrings',
    )
    assert arguments['image_urls'] == ['https://example.com/a.jpg']
    assert 'bamboo' in prompt
    assert arguments['aspect_ratio'] == '3:4'

    # The face must be appended after the product, never before it.
    _prompt, with_face = build(
        ['https://example.com/a.jpg'], 'aditi', 'kyoto',
        product='p', face_url='https://example.com/face.png',
    )
    assert with_face['image_urls'] == [
        'https://example.com/a.jpg', 'https://example.com/face.png'
    ]

    # A shoot must be three distinct prompts on three distinct seeds, not one repeated.
    built = [build(['u'], 'aditi', 'kyoto', product='p', framing=name)
             for name in locations.FRAMINGS]
    assert len({prompt for prompt, _args in built}) == len(locations.FRAMINGS)
    assert len({args['seed'] for _prompt, args in built}) == len(locations.FRAMINGS)

    for bad in (('nobody', 'kyoto'), ('aditi', 'atlantis')):
        try:
            build(['x'], *bad, product='p')
        except KeyError:
            pass
        else:
            raise AssertionError(f'{bad} should have been rejected')

    print('shoot.build ok')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        demo()
    else:
        product_path, model_key, location_key = sys.argv[1:4]
        paths, failures = shoot([product_path], model_key, location_key,
                                product='yellow gold kite-shaped diamond drop earrings '
                                        'with a fine chain')
        for path in paths:
            print(path)
        for framing, reason in failures:
            print(f'FAILED {framing}: {reason}')
