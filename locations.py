"""Shoot location presets, and the prompt they compose into.

The whole point of the 3-step UX is that the client never writes a prompt. They pick
a face and a place; everything a photographer would decide — lens, light, framing,
wardrobe — is baked into the preset here.

Every preset must keep the jewellery readable. Locations are therefore written as
BACKGROUND, deliberately thrown out of focus, never as a wide establishing shot: a
model standing in front of the Taj Mahal at full length shows no earring at all.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    key: str
    label: str
    region: str
    scene: str      # what is behind the model
    light: str      # time of day and quality of light
    wardrobe: str   # what she wears, chosen to not fight the jewellery


INDIAN = [
    Location(
        key='amber-fort',
        label='Amber Fort, Jaipur',
        region='India',
        scene='carved amber sandstone arches and jali latticework of a Rajasthani palace '
              'courtyard, softly blurred behind her',
        light='warm golden hour sunlight raking across the stone, soft bounce onto her face',
        wardrobe='deep red and gold silk banarasi saree',
    ),
    Location(
        key='udaipur-palace',
        label='City Palace, Udaipur',
        region='India',
        scene='white marble columns and scalloped arches of a lakeside palace terrace, '
              'Lake Pichola shimmering out of focus beyond',
        light='cool bright morning light with soft reflected glow from the water',
        wardrobe='pastel mint and silver chanderi saree',
    ),
    Location(
        key='kerala-backwaters',
        label='Kerala Backwaters',
        region='India',
        scene='coconut palms leaning over still green backwater, a wooden houseboat '
              'far out of focus',
        light='humid diffused afternoon light under overcast sky, gentle and even',
        wardrobe='cream and gold kasavu saree',
    ),
    Location(
        key='taj-mahal',
        label='Taj Mahal, Agra',
        region='India',
        scene='white marble domes and minarets rising softly blurred in the far background',
        light='pale pink dawn light, low sun, cool shadows',
        wardrobe='ivory and pale blue georgette saree',
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
    ),
    Location(
        key='santorini',
        label='Santorini, Greece',
        region='International',
        scene='whitewashed cycladic walls and a blue dome, deep blue Aegean far below, '
              'thrown out of focus',
        light='bright Mediterranean late afternoon sun, strong warm key with white wall bounce',
        wardrobe='flowing white linen dress with long sleeves covering her shoulders',
    ),
    Location(
        key='dubai-desert',
        label='Dubai Desert',
        region='International',
        scene='rolling golden sand dunes with soft wind-carved ridges, no structures',
        light='low amber sunset light, long soft shadows, warm rim light on her jaw',
        wardrobe='bronze silk kaftan',
    ),
    Location(
        key='lake-como',
        label='Lake Como, Italy',
        region='International',
        scene='a stone villa terrace with cypress trees and the lake and mountains '
              'softly blurred behind',
        light='clear late morning Italian light, bright with soft shade on her face',
        wardrobe='navy silk dress with elbow length sleeves covering her shoulders',
    ),
    Location(
        key='kyoto',
        label='Kyoto, Japan',
        region='International',
        scene='tall green bamboo grove, vertical stalks receding into soft blur',
        light='cool filtered green-tinted daylight through the canopy, very diffused',
        wardrobe='minimal charcoal grey wrap top',
    ),
]

ALL = {location.key: location for location in INDIAN + FOREIGN}


# Held constant across every shoot: this is the part that protects product fidelity
# and framing, and it is not exposed in the UI.
CRAFT = (
    'The jewellery must match the reference image exactly in shape, proportion, stone '
    'layout and metal colour, with no redesign. Hair tucked back so the full earring '
    'including any drop is visible and unobstructed. Shot on an 85mm lens at f/2, tack '
    'sharp focus on the jewellery, background thrown well out of focus. Photorealistic, '
    'natural skin texture with visible pores, no beauty retouching, luxury jewellery '
    'brand campaign photograph. She wears no necklace, chain, pendant or nose ring — the '
    'earrings are the only jewellery anywhere in the photograph. She is dressed exactly '
    'as described above, fully and modestly, with her shoulders covered. '
    'Full bleed photograph filling the entire frame edge to edge, with no white border, '
    'mount or frame around it.'
)

# One shoot returns several genuinely different photographs, not one photograph several
# times. num_images alone produces near-duplicates, so framing is varied explicitly and
# each variant is given its own seed.
FRAMINGS = {
    'hero': (
        'Three quarter view from the shoulders up, turned slightly away and looking back '
        'toward the camera, the location clearly readable behind her.'
    ),
    'profile': (
        'Clean side profile, her ear and the full earring drop centred in the frame, '
        'the background compressed into soft colour.'
    ),
    'detail': (
        'Tight beauty crop from cheekbone to collarbone, the earring filling much of the '
        'frame, skin texture and metal detail clearly visible.'
    ),
}

# Kept, but it is NOT the load-bearing instruction: a necklace appeared in four separate
# runs that all carried this line. The positive "neck and collarbone are bare" statement
# in CRAFT is the one expected to do the work; this is belt and braces.
NEGATIVE_HINT = (
    'Do not invent a different jewellery design, do not add extra earrings, necklaces '
    'or nose rings that are not in the reference.'
)


def compose(product: str, model_description: str, location_key: str,
            framing: str = 'hero') -> str:
    """Build the popcorn/auto prompt from the three things the client actually picked."""
    location = ALL[location_key]
    if framing not in FRAMINGS:
        raise KeyError(f'unknown framing {framing!r}; have {sorted(FRAMINGS)}')
    return (
        # Expression sits near the front deliberately: at the end of the prompt it was
        # ignored and every shot came back neutral. The brand's own campaigns are warm
        # and smiling, so this is not a detail we can leave to chance.
        f'{model_description}, smiling warmly with a genuine open smile, '
        f'wearing {product} shown in the reference image. '
        f'She wears a {location.wardrobe}. '
        f'Behind her: {location.scene}. '
        f'Lighting: {location.light}. '
        f'Framing: {FRAMINGS[framing]} '
        f'{CRAFT} {NEGATIVE_HINT}'
    )


def demo() -> None:
    assert len(ALL) == 10, len(ALL)
    assert len([l for l in ALL.values() if l.region == 'India']) == 5
    assert len({l.key for l in ALL.values()}) == 10, 'duplicate location key'

    prompt = compose(
        product='yellow gold kite-shaped diamond drop earrings with a fine chain',
        model_description='An Indian woman in her mid twenties with fair wheatish skin '
                          'and long straight dark hair',
        location_key='amber-fort',
    )
    assert 'Amber' not in prompt, 'label leaks into prompt; scene text should stand alone'
    assert 'reference image' in prompt

    # Each framing must actually change the prompt, or a "shoot" is one photo repeated.
    rendered = {
        name: compose(product='p', model_description='m',
                      location_key='kyoto', framing=name)
        for name in FRAMINGS
    }
    assert len({*rendered.values()}) == len(FRAMINGS), 'framings collapsed to one prompt'

    try:
        compose(product='p', model_description='m', location_key='kyoto', framing='wide')
    except KeyError:
        pass
    else:
        raise AssertionError('unknown framing should be rejected')

    print(prompt)


if __name__ == '__main__':
    demo()
