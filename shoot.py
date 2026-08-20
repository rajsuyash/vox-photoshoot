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
SEEDS = {'hero': 101, 'profile': 202, 'detail': 303, 'custom': 404}


def seed_for(framing: str, attempt: int = 1) -> int:
    """Reproducible within an attempt, different across attempts.

    Reproducibility and reshoot pull in opposite directions, and reproducibility used to
    win by accident: reshoot passed the same model, location, description, options and
    framing, so it got the same fixed seed and returned a byte-identical image. Free
    when nobody was paying. Once a reshoot costs a credit, it is charging for nothing.

    Attempt 1 of a given shoot is still always the same photograph.
    """
    return SEEDS[framing] + 1000 * (attempt - 1)


def load_cast() -> dict:
    if not CAST_MANIFEST.exists():
        raise RuntimeError('no cast yet — run cast.py first')
    return json.loads(CAST_MANIFEST.read_text())


def build(product_urls: list[str], model_key: str, location_key: str,
          description: str, category, face_url: str | None = None,
          framing: str = 'hero', options=None, attempt: int = 1,
          comp=None) -> tuple[str, dict]:
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
        # The description carries its own opening ("a 26 year old Kashmiri woman, ...").
        # A hardcoded 'An Indian woman,' in front of it said it twice, and made a cast
        # entry of any other heritage impossible to describe honestly.
        model_description=cast[model_key]['description'],
        location_key=location_key,
        framing=framing,
        options=options,
        comp=comp,
    )
    # Product first: it is the reference that must not be compromised.
    image_urls = [*product_urls, face_url] if face_url else list(product_urls)
    arguments = {
        'prompt': prompt,
        'image_urls': image_urls,
        'seed': seed_for(framing, attempt),
        **DEFAULTS,
    }
    if comp is not None:
        # The client's aspect ratio and resolution, not the house defaults. Provider
        # rejects an aspect its backend cannot do rather than substituting one, so a bad
        # value fails loudly here instead of silently shipping the wrong crop.
        arguments['aspect_ratio'] = comp.aspect
        arguments['quality'] = comp.quality
    return prompt, arguments


def shoot(product_paths, model_key: str, location_key: str, description: str,
          category, framings=None, out_dir='out/shoots', options=None,
          attempts=None, on_image=None, comp=None) -> tuple[list[dict], list[tuple]]:
    """Run one photoshoot: one generation per framing.

    This is the single entry point the web app calls.

    attempts maps framing -> which go this is, so a reshoot gets a different seed and a
    different filename from the image it replaces rather than overwriting it.

    on_image is called with each image the moment it lands, before the next framing
    starts. That is what makes a half-finished shoot worth something: the caller can
    persist and charge per image, so a container that dies on framing three still owes
    the customer nothing for the two they already have.
    """
    provider = providers.get()
    framings = list(framings or locations.FRAMINGS)
    attempts = attempts or {}
    product_urls = [provider.upload(path) for path in product_paths]
    face_url = provider.upload(load_cast()[model_key]['file'])

    saved, failures = [], []
    for framing in framings:
        attempt = attempts.get(framing, 1)
        _prompt, arguments = build(
            product_urls, model_key, location_key, description, category,
            face_url, framing, options, attempt, comp,
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

        # The attempt is in the filename, so a reshoot cannot land on the key of the
        # image it replaces. The customer paid for both and can compare them.
        for path in hf.download(urls, out_dir, prefix=f'{framing}-{attempt}'):
            image = {'framing': framing, 'attempt': attempt, 'path': path,
                     'seed': arguments['seed']}
            if on_image is not None:
                on_image(image)
            saved.append(image)
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
    assert set(SEEDS) == set(locations.ALL_FRAMINGS), (sorted(SEEDS),
                                                       locations.ALL_FRAMINGS)

    # A reshoot must be a different photograph. This is the one that used to be a bug:
    # same seed in, byte-identical image out, and the customer charged for it.
    for name in locations.FRAMINGS:
        seeds = {seed_for(name, attempt) for attempt in (1, 2, 3)}
        assert len(seeds) == 3, f'{name} repeats a seed across attempts: {seeds}'
    # ...while attempt 1 stays reproducible, which is why the seeds were fixed at all.
    assert seed_for('hero', 1) == SEEDS['hero']
    # No two framings may collide at any attempt, or a reshoot of one returns another.
    everything = {seed_for(f, a) for f in locations.FRAMINGS for a in range(1, 6)}
    assert len(everything) == len(locations.FRAMINGS) * 5, 'seed spaces overlap'

    # build() must actually use the attempt, not just accept it.
    _p, first = build(['u'], 'aditi', 'kyoto', description='p', category=earrings,
                      framing='hero', attempt=1)
    _p, second = build(['u'], 'aditi', 'kyoto', description='p', category=earrings,
                       framing='hero', attempt=2)
    assert first['seed'] != second['seed'], 'attempt does not reach the provider'

    # The category has to reach the provider arguments, not just the category presets.
    ring, _args = build(['u'], 'aditi', 'kyoto', description='a gold solitaire ring',
                        category=product.CATEGORIES['ring'], framing='detail')
    assert 'ring finger' in ring and 'EXTREME CLOSE UP of the hand' in ring, ring

    # So do the client's own choices — build() is the last place they can be dropped.
    chosen, _args = build(['u'], 'aditi', 'kyoto', description='a gold band',
                          category=product.CATEGORIES['ring'], framing='detail',
                          options=product.Options(size='xs', finger='little',
                                                  hand='left'))
    assert 'little finger of her left hand' in chosen, chosen
    assert 'half the width of her fingernail' in chosen, chosen

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
        piece = product.identify(product_path)
        label = 'detected' if piece.detected else 'COULD NOT READ, falling back to'
        print(f'{label} {piece.category.key}: {piece.description}')
        paths, failures = shoot([product_path], model_key, location_key,
                                piece.description, piece.category)
        for path in paths:
            print(path)
        for framing, reason in failures:
            print(f'FAILED {framing}: {reason}')
