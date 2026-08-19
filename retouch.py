"""Clean up a product photograph, without redesigning the product.

A separate tool from the shoot: one jewellery photo in, the same piece back with the
dust, fingerprints, scratches and colour cast gone. It exists for two reasons.

The obvious one is that clients photograph stock on a desk and want a catalogue image.
The less obvious one is that a retouched photo is a better SHOOT input: the README's
oldest known limitation is that pavé collapses into a solitaire when the source file is
small and dirty, because the generator cannot see the individual stones. Cleaning the
reference first is the cheapest fix available for that.

The risk here is the same one the shoot has — the model quietly restyling the piece into
something prettier. Every mode therefore leads with the fidelity guard, and the strength
of the relight is the only thing that varies.

    .venv/bin/python retouch.py                          # self-check, no API call
    .venv/bin/python retouch.py photo.jpg studio
"""

import pathlib
import sys

import hf
import providers

# Held constant across every mode: this is what stops a "retouch" coming back as a
# different, glossier piece of jewellery.
FIDELITY = (
    'The piece must remain exactly the same design as the reference image — identical '
    'shape, proportion, stone count and layout, setting, engraving and metal colour. '
    'Do not restyle it, do not replace any part of it, and do not add or remove any '
    'stone or detail. This is a retouch of one photograph, not a new design.'
)

# What retouching means regardless of how far the relight goes.
CLEANUP = (
    'Remove dust, lint, fibres, fingerprints, smudges and stray scratches from the '
    'metal. Correct the white balance so the metal reads its true colour with no cast. '
    'Smooth gradients cleanly with no banding or posterisation. Tack sharp focus across '
    'the whole piece, high resolution jewellery product photography. '
    'Full bleed photograph filling the entire frame edge to edge, with no white border, '
    'mount or frame around it.'
)

# How much licence the retoucher gets. Ordered by how far each departs from the photo
# the client actually took.
MODES = {
    'faithful': (
        'Retouch conservatively. Keep the lighting, angle, surface character and mood '
        'of the original photograph exactly as shot — clean it and correct the colour, '
        'but add no gloss, no drama and no new highlights.'
    ),
    'studio': (
        'Relight as a clean studio product shot: soft even light from a large diffused '
        'source, gentle gradient reflections travelling along the metal, and a soft '
        'contact shadow directly beneath the piece.'
    ),
    'vivid': (
        'Relight as a high end campaign product shot: crisp specular highlights on the '
        'metal, deep saturated stone colour with visible internal fire, and strong '
        'contrast. Keep it photographic — no illustration, no render, no glow effects.'
    ),
}
DEFAULT_MODE = 'faithful'

# The one toggle that is a genuine judgement call rather than a preference. Inclusions
# are what make a stone read as a real stone; polishing them out is what makes catalogue
# images look synthetic. Their own competitor defaults this ON — we default it OFF,
# because this app's entire premise is that the client's actual piece comes back.
STONES = {
    True: 'Clean the stones so they read bright and polished, removing surface dust and '
          'tiny blemishes.',
    False: 'Keep every stone exactly as it is, including its natural inclusions and '
           'internal character. Do not clarify, clean or idealise the stones.',
}

# Backgrounds a jewellery catalogue actually uses.
BACKGROUNDS = {
    'keep': '',
    'white': 'Place the piece on a plain seamless pure white background.',
    'grey': 'Place the piece on a plain seamless neutral mid grey background.',
    'black': 'Place the piece on a plain seamless deep black background.',
}
DEFAULT_BACKGROUND = 'keep'


def compose(mode: str = DEFAULT_MODE, retouch_stones: bool = False,
            background: str = DEFAULT_BACKGROUND, instructions: str = '') -> str:
    """Build the retouch prompt. Fidelity first, licence last."""
    if mode not in MODES:
        raise KeyError(f'unknown mode {mode!r}; have {sorted(MODES)}')
    if background not in BACKGROUNDS:
        raise KeyError(f'unknown background {background!r}; have {sorted(BACKGROUNDS)}')
    parts = [
        'Professional jewellery product retouching of the piece in the reference image.',
        FIDELITY,
        CLEANUP,
        STONES[bool(retouch_stones)],
        MODES[mode],
        BACKGROUNDS[background],
        # Last so it can override the mode — "leave the patina alone" has to beat a
        # generic instruction about polished metal.
        (instructions or '').strip(),
    ]
    return ' '.join(part for part in parts if part)


def run(image_path, mode: str = DEFAULT_MODE, retouch_stones: bool = False,
        background: str = DEFAULT_BACKGROUND, instructions: str = '',
        quality: str = 'high', out_dir='out/retouches') -> list[pathlib.Path]:
    """Retouch one photograph and save the result."""
    from PIL import Image

    try:  # iPhone photos, same as the shoot path.
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    provider = providers.get()
    prompt = compose(mode, retouch_stones, background, instructions)
    with Image.open(image_path) as image:
        # The client's own photograph goes back to them, so it keeps its own shape.
        aspect = provider.nearest_aspect(*image.size)
    urls = provider.generate(prompt, image_urls=[provider.upload(image_path)],
                             aspect_ratio=aspect, quality=quality)
    if not urls:
        raise RuntimeError('retouch returned no image')
    return hf.download(urls, out_dir, prefix=f'retouch-{mode}')


def demo() -> None:
    """Self-check: prompt assembly, without spending anything."""
    assert tuple(MODES) == ('faithful', 'studio', 'vivid'), 'modes reordered'
    assert DEFAULT_MODE in MODES and DEFAULT_BACKGROUND in BACKGROUNDS

    # Every mode is a different photograph, or the control is decoration.
    assert len({compose(mode=m) for m in MODES}) == len(MODES)
    assert len({compose(background=b) for b in BACKGROUNDS}) == len(BACKGROUNDS)
    assert compose(retouch_stones=True) != compose(retouch_stones=False)

    # The guard against the model redesigning the piece is not optional.
    for mode in MODES:
        for stones in (True, False):
            prompt = compose(mode=mode, retouch_stones=stones)
            assert FIDELITY in prompt, (mode, stones)
            assert 'not a new design' in prompt

    # Default keeps the stones honest: inclusions are what make a real stone read real.
    assert 'natural inclusions' in compose()
    assert 'natural inclusions' not in compose(retouch_stones=True)

    # 'keep' must add nothing at all, or every retouch quietly restages the shot.
    assert BACKGROUNDS['keep'] == ''
    assert 'background' not in compose(background='keep').lower()
    assert 'pure white' in compose(background='white')

    # The client's note has to win against the mode it contradicts.
    note = 'Leave the patina on the silver alone.'
    vivid = compose(mode='vivid', instructions=note)
    assert vivid.endswith(note), vivid
    assert vivid.index('specular') < vivid.index(note)

    for bad in (dict(mode='glossy'), dict(background='tartan')):
        try:
            compose(**bad)
        except KeyError:
            pass
        else:
            raise AssertionError(f'{bad} should have been rejected')

    print('retouch ok')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        demo()
    else:
        path = sys.argv[1]
        mode = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODE
        for saved in run(path, mode=mode):
            print(saved)
