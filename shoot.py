"""Run one photoshoot: product + model + location -> images.

This is the whole generation backend. The web app will call shoot() and nothing else.

    .venv/bin/python shoot.py clientphoto/50D5L1DOUAGA2A_1.jpg aditi amber-fort
"""

import json
import pathlib
import sys

import hf
import locations
import product
import providers

CAST_MANIFEST = pathlib.Path('assets/cast/cast.json')

# popcorn/auto was the only endpoint on the account that preserved the client's actual
# product; soul/reference redesigned it and soul/standard ignored it. See fidelity_test.py.
# fal offers 4:5, the Instagram portrait ratio, which no Higgsfield image model has.
# providers.Provider rejects an aspect its backend cannot do rather than substituting.
DEFAULTS = {'quality': 'high', 'aspect_ratio': '3:4'}

# Fixed per framing so a shoot is reproducible: same inputs re-run give the same set,
# which matters when a client asks for "the one from yesterday, but bigger".
SEEDS = {'hero': 101, 'profile': 202, 'detail': 303}


def load_cast() -> dict:
    if not CAST_MANIFEST.exists():
        raise RuntimeError('no cast yet — run cast.py first')
    return json.loads(CAST_MANIFEST.read_text())


def build(product_urls: list[str], model_key: str, location_key: str,
          description: str, category, face_url: str | None = None,
          framing: str = 'hero') -> tuple[str, dict]:
    """Return (prompt, arguments) without calling the API, so cost can be checked first.

    description and category both come from the uploaded photo (product.identify): what
    the piece looks like, and where on the body it is worn. The reference image alone
    does not carry placement — the prompt has to say it, or a ring comes back as an
    earring.

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
        product=description,
        category=category,
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


def shoot(product_paths, model_key: str, location_key: str, description: str,
          category, framings=None, out_dir='out/shoots') -> list[pathlib.Path]:
    """Run one photoshoot: one generation per framing, saved locally.

    This is the single entry point the web app calls.
    """
    provider = providers.get()
    framings = list(framings or locations.FRAMINGS)
    product_urls = [provider.upload(path) for path in product_paths]
    face_url = provider.upload(load_cast()[model_key]['file'])

    saved, failures = [], []
    for framing in framings:
        _prompt, arguments = build(
            product_urls, model_key, location_key, description, category,
            face_url, framing,
        )
        try:
            urls = provider.generate(**arguments)
        except Exception as error:
            # Content moderation rejects a frame now and then. Reported rather than
            # dropped: a shoot silently returning 2 of 3 looks like a bug.
            failures.append((framing, str(error)))
            continue

        if not urls:
            failures.append((framing, 'no images returned'))
            continue
        saved += hf.download(
            urls, out_dir, prefix=f'{model_key}-{location_key}-{framing}'
        )
    return saved, failures


def demo() -> None:
    """Self-check: prompt assembly and validation, without spending credits."""
    earrings = product.CATEGORIES['earrings']
    prompt, arguments = build(
        ['https://example.com/a.jpg'], 'aditi', 'kyoto',
        description='yellow gold kite-shaped diamond drop earrings', category=earrings,
    )
    assert arguments['image_urls'] == ['https://example.com/a.jpg']
    assert 'bamboo' in prompt
    assert arguments['aspect_ratio'] == '3:4'

    # The face must be appended after the product, never before it.
    _prompt, with_face = build(
        ['https://example.com/a.jpg'], 'aditi', 'kyoto',
        description='p', category=earrings, face_url='https://example.com/face.png',
    )
    assert with_face['image_urls'] == [
        'https://example.com/a.jpg', 'https://example.com/face.png'
    ]

    # A shoot must be three distinct prompts on three distinct seeds, not one repeated.
    built = [build(['u'], 'aditi', 'kyoto', description='p', category=earrings,
                   framing=name)
             for name in locations.FRAMINGS]
    assert len({prompt for prompt, _args in built}) == len(locations.FRAMINGS)
    assert len({args['seed'] for _prompt, args in built}) == len(locations.FRAMINGS)

    # Every framing this app can shoot must have a seed, or a reshoot silently reuses
    # another frame's seed and returns the same photograph.
    assert set(SEEDS) == set(locations.FRAMINGS), (sorted(SEEDS), locations.FRAMINGS)

    # The category has to reach the provider arguments, not just the category presets.
    ring, _args = build(['u'], 'aditi', 'kyoto', description='a gold solitaire ring',
                        category=product.CATEGORIES['ring'], framing='detail')
    assert 'ring finger' in ring and 'EXTREME CLOSE UP of the hand' in ring, ring

    for bad in (('nobody', 'kyoto'), ('aditi', 'atlantis')):
        try:
            build(['x'], *bad, description='p', category=earrings)
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
        # The CLI reads the piece off the photo exactly as the web app does, so a shoot
        # run from the terminal is the same shoot the client gets.
        category, description = product.identify(product_path)
        print(f'detected {category.key}: {description}')
        paths, failures = shoot([product_path], model_key, location_key,
                                description, category)
        for path in paths:
            print(path)
        for framing, reason in failures:
            print(f'FAILED {framing}: {reason}')
