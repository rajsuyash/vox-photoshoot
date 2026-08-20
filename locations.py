"""Shoot location presets, and the prompt they compose into.

The whole point of the 3-step UX is that the client never writes a prompt. They pick
a face and a place; everything a photographer would decide — lens, light, framing,
wardrobe — is baked into the preset here.

Every preset must keep the jewellery readable. Locations are therefore written as
BACKGROUND, deliberately thrown out of focus, never as a wide establishing shot: a
model standing in front of the Taj Mahal at full length shows no earring at all.
"""

from dataclasses import dataclass

import product


@dataclass(frozen=True)
class Location:
    key: str
    label: str
    region: str
    scene: str      # what is behind the model, deliberately blurred, for shoots
    light: str      # time of day and quality of light
    wardrobe: str   # what she wears, chosen to not fight the jewellery
    plate: str      # the empty place itself, sharp and unpopulated, for the picker card


INDIAN = [
    Location(
        key='amber-fort',
        label='Amber Fort, Jaipur',
        region='India',
        scene='carved amber sandstone arches and jali latticework of a Rajasthani palace '
              'courtyard, softly blurred behind her',
        light='warm golden hour sunlight raking across the stone, soft bounce onto her face',
        wardrobe='deep red and gold silk banarasi saree',
        plate='the carved amber sandstone courtyard of Amber Fort in Jaipur, scalloped arches and jali lattice screens, warm stone underfoot, empty of people',
    ),
    Location(
        key='udaipur-palace',
        label='City Palace, Udaipur',
        region='India',
        scene='white marble columns and scalloped arches of a lakeside palace terrace, '
              'Lake Pichola shimmering out of focus beyond',
        light='cool bright morning light with soft reflected glow from the water',
        wardrobe='pastel mint and silver chanderi saree',
        plate='a white marble lakeside terrace of the City Palace in Udaipur, scalloped arches and carved columns, Lake Pichola and the far hills beyond, empty of people',
    ),
    Location(
        key='kerala-backwaters',
        label='Kerala Backwaters',
        region='India',
        scene='coconut palms leaning over still green backwater, a wooden houseboat '
              'far out of focus',
        light='humid diffused afternoon light under overcast sky, gentle and even',
        wardrobe='cream and gold kasavu saree',
        plate='a still green Kerala backwater channel lined with leaning coconut palms, a wooden houseboat moored along the bank, empty of people',
    ),
    Location(
        key='taj-mahal',
        label='Taj Mahal, Agra',
        region='India',
        scene='white marble domes and minarets rising softly blurred in the far background',
        light='pale pink dawn light, low sun, cool shadows',
        wardrobe='ivory and pale blue georgette saree',
        plate='the white marble Taj Mahal in Agra seen across its reflecting pool and formal gardens, minarets on either side, empty of people',
    ),
    Location(
        key='rann-of-kutch',
        label='Rann of Kutch',
        region='India',
        scene='endless white salt flat meeting a wide empty horizon, no landmarks',
        light='dusk light after sunset, soft magenta and blue sky, very even',
        # Mirror-work and heavy embroidery render as jewellery and out-compete the
        # earring — every wardrobe here stays plain above the shoulders.
        wardrobe='plain black cotton kutchi outfit with no embroidery or mirror work',
        plate='the white salt flats of the Rann of Kutch, cracked hexagonal salt crust stretching to a flat empty horizon, no structures and no people',
    ),
]

FOREIGN = [
    Location(
        key='paris',
        label='Paris, France',
        region='International',
        scene='Haussmann stone facades and wrought iron balconies of a Paris boulevard, '
              'heavily blurred, warm bokeh from a cafe behind',
        light='soft overcast European daylight, cool and flattering',
        wardrobe='tailored ivory wool coat over a simple black top',
        plate='a Paris boulevard of Haussmann stone facades and wrought iron balconies, a corner cafe with awning and rattan chairs, empty pavement',
    ),
    Location(
        key='santorini',
        label='Santorini, Greece',
        region='International',
        scene='whitewashed cycladic walls and a blue dome, deep blue Aegean far below, '
              'thrown out of focus',
        light='bright Mediterranean late afternoon sun, strong warm key with white wall bounce',
        wardrobe='flowing white linen dress with long sleeves covering her shoulders',
        plate='a whitewashed Santorini terrace in Oia, blue domed church and cubic white houses stepping down the caldera, deep blue Aegean below, empty of people',
    ),
    Location(
        key='dubai-desert',
        label='Dubai Desert',
        region='International',
        scene='rolling golden sand dunes with soft wind-carved ridges, no structures',
        light='low amber sunset light, long soft shadows, warm rim light on her jaw',
        wardrobe='bronze silk kaftan',
        plate='rolling golden sand dunes in the desert outside Dubai, sharp wind carved ridges and long shadows, no structures and no people',
    ),
    Location(
        key='lake-como',
        label='Lake Como, Italy',
        region='International',
        scene='a stone villa terrace with cypress trees and the lake and mountains '
              'softly blurred behind',
        light='clear late morning Italian light, bright with soft shade on her face',
        wardrobe='navy silk dress with elbow length sleeves covering her shoulders',
        plate='a stone villa terrace on Lake Como with a balustrade, tall cypress trees, the lake and mountains beyond, empty of people',
    ),
    Location(
        key='kyoto',
        label='Kyoto, Japan',
        region='International',
        scene='tall green bamboo grove, vertical stalks receding into soft blur',
        light='cool filtered green-tinted daylight through the canopy, very diffused',
        wardrobe='minimal charcoal grey wrap top',
        plate='a tall green bamboo grove in Arashiyama Kyoto, dense vertical stalks lining a narrow path, empty of people',
    ),
]

ALL = {location.key: location for location in INDIAN + FOREIGN}


# Held constant across every shoot: the part that protects product fidelity, and not
# exposed in the UI. Everything here is true of any piece — what is specific to where
# the piece is worn (which body part stays bare, how it is framed, what must not be
# invented alongside it) lives on product.Category instead.
CRAFT_BASE = (
    'The jewellery must match the reference image exactly in shape, proportion, stone '
    'layout and metal colour, with no redesign. Shot on an 85mm lens at f/2, tack '
    'sharp focus on the jewellery, background thrown well out of focus. Photorealistic, '
    'natural skin texture with visible pores, no beauty retouching, luxury jewellery '
    'brand campaign photograph. She is dressed exactly as described above, fully and '
    'modestly, with her shoulders covered. '
    'Full bleed photograph filling the entire frame edge to edge, with no white border, '
    'mount or frame around it.'
)

# One shoot returns several genuinely different photographs, not one photograph several
# times: num_images alone produces near-duplicates, so framing is varied explicitly and
# each variant is given its own seed.
#
# The keys, not the prose, live here. Every category writes its own three framings —
# a ring cannot be shot on the crop that works for an earring — but the key names are
# fixed, because shoot.SEEDS, app.merge_images and the reshoot endpoint all address a
# frame by name.
FRAMINGS = ('hero', 'profile', 'detail')

# What compose() will accept. Deliberately NOT part of FRAMINGS: a shoot costs
# len(FRAMINGS) credits, so adding 'custom' there would silently reprice every shoot from
# three credits to four. 'custom' is a single client-composed shot, priced separately.
ALL_FRAMINGS = FRAMINGS + ('custom',)


def compose_plate(location_key: str) -> str:
    """Prompt for an EMPTY location plate — the picker card, and the backplate that a
    model gets composited onto later. No model, no product, no wardrobe, nothing blurred.
    """
    location = ALL[location_key]
    # The light strings are written for a shoot ("soft bounce onto her face"), so any
    # clause that talks about the model is dropped before reusing them on an empty plate.
    light = ', '.join(
        clause for clause in location.light.split(', ')
        if ' her' not in f' {clause}'
    )
    return (
        f'Photorealistic travel and architectural photograph of {location.plate}. '
        f'{light}. '
        'Wide establishing shot of the empty location, deserted and unoccupied, '
        'everything in sharp focus front to back, shot on a 24mm lens at f/8, '
        'high end location scouting photograph for a fashion campaign. '
        'Full bleed photograph filling the entire frame edge to edge, with no white '
        'border, mount or frame around it.'
    )


def compose(product: str, category, model_description: str, location_key: str,
            framing: str = 'hero', options=None, comp=None) -> str:
    """Build the prompt from the three things the client picked, plus what they uploaded.

    category is a product.Category — it decides where on the body the piece goes, how
    the frame is cropped, and what must not be invented next to it. options is a
    product.Options carrying what the photograph cannot say: how big the piece is, and
    which finger it goes on.
    """
    import composition as composition_module

    location = ALL[location_key]
    if framing not in ALL_FRAMINGS:
        raise KeyError(f'unknown framing {framing!r}; have {sorted(ALL_FRAMINGS)}')
    comp = comp or composition_module.Composition()
    # Free text from the client, placed after the craft rules so it can override them —
    # "keep the engraving" has to beat a generic instruction about sharpness.
    note = (options.instructions or '').strip() if options else ''

    # In CUSTOM mode the framing line comes only from the client's frame and distance.
    # Mixing it with category.framings would put two answers to the same question in one
    # prompt — "extreme close up of the hand" and "her whole figure" — and the model
    # resolves that contradiction arbitrarily.
    if framing == 'custom':
        frame_line = comp.custom_framing(category.key)
    else:
        frame_line = f'{category.framings[framing]} {comp.direction(category.key)}'.strip()

    return (
        # Expression sits near the front deliberately: at the end of the prompt it was
        # ignored and every shot came back neutral. It used to be a hardcoded smile,
        # which meant a brand wanting a composed, serious campaign could not have one at
        # any price — the default is still a smile, but it is now the client's to change.
        f'{model_description}, '
        f'{composition_module.EXPRESSIONS[comp.expression]}, '
        # "from the reference image" stays here at the front, next to the product, even
        # though CRAFT_BASE restates fidelity later: the early anchor is what stopped
        # the model redesigning the piece, and CRAFT_BASE alone did not.
        f'wearing {product} from the reference image {category.worn(options)} '
        f'She wears a {location.wardrobe}. '
        f'Behind her: {location.scene}. '
        f'Lighting: {location.light}. '
        f'Framing: {frame_line} '
        f'{CRAFT_BASE} {category.craft} '
        + (f'{note} ' if note else '')
        + category.negative
    )


def _check_composition(category) -> None:
    """The composition controls must actually reach the prompt.

    Every one of these is a silent failure otherwise: the client picks 'serious', the
    prompt still says 'smiling', and the only way anyone finds out is by looking at a
    photograph they paid for.
    """
    import composition

    base = compose(product='p', category=category, model_description='m',
                   location_key='kyoto', framing='hero')

    # The default must be exactly what the hardcoded line used to be, or every existing
    # shoot silently changes character the day this ships.
    assert 'smiling warmly with a genuine open smile' in base

    serious = compose(product='p', category=category, model_description='m',
                      location_key='kyoto', framing='hero',
                      comp=composition.parse({'expression': 'serious'}, category.key))
    assert 'not smiling' in serious and 'genuine open smile' not in serious

    # View, angle and pose each have to change the prompt on their own.
    for field, value, needle in (('view', 'side', 'full profile'),
                                 ('angle', 'top-down', 'directly overhead'),
                                 ('pose', 'tucking-hair', 'tucks her hair')):
        got = compose(product='p', category=category, model_description='m',
                      location_key='kyoto', framing='hero',
                      comp=composition.parse({field: value}, category.key))
        assert needle in got, f'{field}={value} never reached the prompt'
        assert got != base

    # Custom mode must build its framing from the client's frame and distance, and must
    # NOT carry the shot-owned crop as well — two answers to one question.
    custom = compose(product='p', category=category, model_description='m',
                     location_key='kyoto', framing='custom',
                     comp=composition.parse({'frame': 'ear', 'distance': 'close'},
                                            category.key))
    assert 'one ear fills the frame' in custom and 'Shot close' in custom
    for fixed in FRAMINGS:
        assert category.framings[fixed] not in custom, f'custom leaked the {fixed} crop'

    # An unknown framing is still a loud failure, not an unframed prompt.
    try:
        compose(product='p', category=category, model_description='m',
                location_key='kyoto', framing='nonsense')
    except KeyError:
        pass
    else:
        raise AssertionError('an unknown framing produced a prompt')


def demo() -> None:
    assert len(ALL) == 10, len(ALL)
    assert len([l for l in ALL.values() if l.region == 'India']) == 5
    assert len({l.key for l in ALL.values()}) == 10, 'duplicate location key'

    earrings = product.CATEGORIES['earrings']
    prompt = compose(
        product=product.DEFAULT_PRODUCT,
        category=earrings,
        model_description='An Indian woman in her mid twenties with fair wheatish skin '
                          'and long straight dark hair',
        location_key='amber-fort',
    )
    assert 'Amber' not in prompt, 'label leaks into prompt; scene text should stand alone'
    assert 'reference image' in prompt

    # Each framing must actually change the prompt, or a "shoot" is one photo repeated.
    rendered = {
        name: compose(product='p', category=earrings, model_description='m',
                      location_key='kyoto', framing=name)
        for name in FRAMINGS
    }
    assert len({*rendered.values()}) == len(FRAMINGS), 'framings collapsed to one prompt'

    _check_composition(earrings)

    # And the category must reach the prompt: this is the bug that put a ring on an ear.
    # Only the half before CRAFT_BASE is checked — the craft and negative clauses name
    # the ear on purpose, to say it stays bare and empty.
    ring = compose(product='a gold solitaire ring', category=product.CATEGORIES['ring'],
                   model_description='m', location_key='kyoto', framing='detail')
    aimed = ring.split(CRAFT_BASE)[0]
    assert 'ring finger' in aimed, aimed
    assert 'earring' not in aimed, 'ring prompt still poses the piece as an earring'

    # The client's own choices have to survive into the prompt, or the controls are
    # decoration. Scale especially: a product shot carries no size reference, so this
    # sentence is the only thing telling the model how big the piece is.
    chosen = compose(
        product='a gold signet ring', category=product.CATEGORIES['ring'],
        model_description='m', location_key='kyoto', framing='hero',
        options=product.Options(size='xl', type='signet', finger='index', hand='left',
                                instructions='Keep the engraving crisp.'),
    )
    assert 'index finger of her left hand' in chosen, chosen
    assert 'signet' in chosen and 'twice the width of her fingernail' in chosen
    assert 'Keep the engraving crisp.' in chosen
    # The note must land before the negative hint, which stays last.
    assert chosen.index('Keep the engraving') < chosen.index('Do not invent')

    # Every size must produce a different prompt for every category.
    for key, category in product.CATEGORIES.items():
        rendered = {compose(product='p', category=category, model_description='m',
                            location_key='kyoto', options=product.Options(size=size))
                    for size in product.SIZES}
        assert len(rendered) == len(product.SIZES), f'{key} ignores the size control'

    try:
        compose(product='p', category=earrings, model_description='m',
                location_key='kyoto', framing='wide')
    except KeyError:
        pass
    else:
        raise AssertionError('unknown framing should be rejected')

    # Plates must describe a place and nothing else: no model, no product, no wardrobe.
    for key, location in ALL.items():
        card = compose_plate(key)
        assert ' her' not in f' {card}', f'{key} plate mentions the model: {card}'
        assert 'earring' not in card and 'jewellery' not in card, key
        assert location.wardrobe.split()[0] not in card, f'{key} plate leaks wardrobe'
        assert 'out of focus' not in card, f'{key} plate should be sharp throughout'
    print('plates ok')

    print(prompt)


if __name__ == '__main__':
    demo()
