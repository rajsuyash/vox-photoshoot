"""What the uploaded piece actually is, and how a photograph of it must be framed.

The shoot used to assume every upload was the client's drop earrings: the description,
the occlusion clause, the framings and the negative hint all said "earring". Upload a
ring and the reference photo went to the model correctly but the prose told it to hang
the thing off an ear, and prose wins on placement. So the piece is read off the photo
before the prompt is built.

    .venv/bin/python product.py                      # self-check, no API call
    .venv/bin/python -c "import product; print(product.identify('ring.jpg'))"
"""

import base64
import json
import mimetypes
import os
import pathlib
import re
from dataclasses import dataclass

# Single-image classification with a fixed schema. Haiku is the right tier for it and
# costs about a fifth of a US cent per shoot, against ~4.5 fal credits for the images.
VISION_MODEL = 'claude-haiku-4-5'


@dataclass(frozen=True)
class Category:
    """Everything about the prompt that depends on where the piece is worn."""
    key: str
    placement: str   # completes "wearing <product> <placement>"
    craft: str       # what must stay bare and unobstructed for this piece
    negative: str    # the other three categories, which must not be invented
    framings: dict   # hero / profile / detail — same keys for every category


# The keys are load-bearing: shoot.SEEDS, app.merge_images and the reshoot endpoint all
# address a frame by these names. Only the prose changes per category.
#
# Nano Banana Pro weights the reference images heavily and framing prose lightly, so
# every entry states the crop in camera terms (what is in frame, what is cut off)
# rather than describing a mood. Three genuinely different photographs, not one three
# times: a wide that sells the location, a profile, and a macro on the piece.
CATEGORIES = {
    'earrings': Category(
        key='earrings',
        placement='in her ears',
        craft='Hair tucked back behind both ears so the full earring including any drop '
              'is visible and unobstructed. Her neck and collarbone are bare.',
        negative='Do not invent a different jewellery design, and do not add a necklace, '
                 'pendant, nose ring, bracelet or finger ring that is not in the reference.',
        framings={
            'hero': (
                'WIDE SHOT. Full upper body from the waist up, she is turned slightly away '
                'and looking back toward the camera, standing well back from the lens so '
                'the whole location is visible around her and reads clearly.'
            ),
            'profile': (
                'STRICT SIDE PROFILE, camera exactly 90 degrees to her face. She faces '
                'fully to the left edge of the frame and does not look at the camera. The '
                'silhouette of her nose, lips and chin is drawn against the background, '
                'and the whole ear with the earring is square on to the lens.'
            ),
            'detail': (
                'EXTREME CLOSE UP of the ear and jaw only. The frame is filled by the ear '
                'and the earring, cropped so the top of the head and the mouth are outside '
                'the frame. Macro jewellery photography, the metal and stones fill much of '
                'the picture and the face is only a supporting element.'
            ),
        },
    ),
    'necklace': Category(
        key='necklace',
        placement='around her neck',
        craft='Hair swept back off her shoulders and her neckline open so the full '
              'necklace including any pendant lies flat against her skin, unobstructed by '
              'hair or fabric. Her ears are bare.',
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'nose ring, bracelet or finger ring that is not in the reference.',
        framings={
            'hero': (
                'WIDE SHOT. Full upper body from the waist up, she is turned slightly away '
                'and looking back toward the camera, standing well back from the lens so '
                'the whole location is visible around her and reads clearly.'
            ),
            'profile': (
                'THREE QUARTER TURN, camera at 45 degrees and slightly below her chin so '
                'the line of her throat and collarbone is drawn against the background. '
                'She looks away from the camera, chin lifted, and the whole necklace from '
                'clasp to pendant is square on to the lens.'
            ),
            'detail': (
                'EXTREME CLOSE UP of the neck, collarbone and upper chest only. The frame '
                'is filled by the necklace, cropped so her mouth and shoulders are outside '
                'the frame. Macro jewellery photography, the metal and stones fill much of '
                'the picture and the skin is only a supporting element.'
            ),
        },
    ),
    'ring': Category(
        key='ring',
        placement='on the ring finger of her right hand',
        craft='Sleeves pushed well back so her hand, fingers and wrist are completely bare '
              'and unobstructed, fingers relaxed and slightly apart so the ring is never '
              'hidden behind another finger. Her ears and neck are bare.',
        # "nose stud", not "nose ring": the piece being shot is itself a ring, and a
        # negative hint that repeats the product's own noun invites exactly the mistake.
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'necklace, pendant, nose stud or bracelet that is not in the reference.',
        framings={
            'hero': (
                'WIDE SHOT. Full upper body from the waist up, she is turned slightly away '
                'and looking back toward the camera with one hand raised near her jaw so '
                'the ring reads clearly, standing well back from the lens so the whole '
                'location is visible around her.'
            ),
            'profile': (
                'HAND AND FOREARM fill the frame, held up in front of her, camera level '
                'with the hand and 90 degrees to the back of it. Her face is soft and out '
                'of focus behind the hand and she does not look at the camera. The ringed '
                'finger is square on to the lens against the blurred location.'
            ),
            'detail': (
                'EXTREME CLOSE UP of the hand only, cropped at the wrist so no part of her '
                'face or body is in the frame. The frame is filled by the ring finger and '
                'the ring. Macro jewellery photography, the metal and stones fill much of '
                'the picture and the skin is only a supporting element.'
            ),
        },
    ),
    'bracelet': Category(
        key='bracelet',
        placement='on her right wrist',
        craft='Sleeves pushed well back above the elbow so her wrist and forearm are '
              'completely bare and unobstructed, and the full bracelet sits clear of any '
              'fabric. Her ears and neck are bare.',
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'necklace, pendant, nose ring or finger ring that is not in the reference.',
        framings={
            'hero': (
                'WIDE SHOT. Full upper body from the waist up, she is turned slightly away '
                'and looking back toward the camera with one arm raised so the wrist and '
                'bracelet read clearly, standing well back from the lens so the whole '
                'location is visible around her.'
            ),
            'profile': (
                'WRIST AND FOREARM fill the frame, arm held up and bent, camera level with '
                'the wrist and 90 degrees to it. Her face is soft and out of focus behind '
                'the arm and she does not look at the camera. The whole circumference of '
                'the bracelet is square on to the lens against the blurred location.'
            ),
            'detail': (
                'EXTREME CLOSE UP of the wrist only, cropped mid-forearm and mid-hand so '
                'no part of her face or body is in the frame. The frame is filled by the '
                'bracelet. Macro jewellery photography, the metal and stones fill much of '
                'the picture and the skin is only a supporting element.'
            ),
        },
    ),
}

# Falls back to the client's own piece. Their catalogue is mostly earrings, so a failed
# detection lands on the shoot this app has always produced rather than on nothing.
DEFAULT_CATEGORY = CATEGORIES['earrings']
DEFAULT_PRODUCT = 'yellow gold kite-shaped diamond drop earrings with a fine chain'

# Short on purpose: a longer description hallucinated a matching necklace and flattened
# the pavé. The model is told to name the metal, stones and silhouette and stop there.
BRIEF = f"""Identify this piece of jewellery from the photograph.

category: which of {sorted(CATEGORIES)} it is worn as. If the photograph shows a matched
set, pick the piece that dominates the frame.

description: one short noun phrase a photographer would use to identify it on a shot
list — metal colour, stone setting, and silhouette. Fifteen words at most. No sentence,
no adjectives about beauty or quality, no mention of the background or packaging.
Examples: "yellow gold solitaire ring with a pave band", "silver hoop earrings",
"emerald and gold layered necklace with a teardrop pendant".

Name the stone setting explicitly when you can see it — pave, solitaire, cluster, halo,
channel, bezel. This is the detail that gets lost: a pave of many tiny stones is
otherwise redrawn as one large stone, and the piece stops being the client's."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'category': {'type': 'string', 'enum': sorted(CATEGORIES)},
        'description': {'type': 'string'},
    },
    'required': ['category', 'description'],
    'additionalProperties': False,
}


def identify(image_path) -> tuple[Category, str]:
    """Read the piece off its photograph -> (category, description).

    Never raises. A missing key, a network failure or a nonsense answer falls back to
    the client's earrings, which is exactly the shoot this app produced before detection
    existed — a degraded shoot beats a failed upload during a live client demo.
    """
    try:
        import anthropic

        path = pathlib.Path(image_path)
        media_type = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
        data = base64.standard_b64encode(path.read_bytes()).decode()

        response = anthropic.Anthropic().messages.create(
            model=VISION_MODEL,
            max_tokens=256,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image',
                     'source': {'type': 'base64', 'media_type': media_type, 'data': data}},
                    {'type': 'text', 'text': BRIEF},
                ],
            }],
            output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
        )
        text = next(block.text for block in response.content if block.type == 'text')
        answer = json.loads(text)
        category = CATEGORIES[answer['category']]
        description = answer['description'].strip()
        if not description:
            raise ValueError('empty description')
    except Exception as error:
        # Surfaced in the container log, not to the client: the shoot still runs.
        print(f'product.identify failed ({error!r}); falling back to '
              f'{DEFAULT_CATEGORY.key}', flush=True)
        return DEFAULT_CATEGORY, DEFAULT_PRODUCT
    return category, description


def demo() -> None:
    """Self-check: the presets, without spending anything."""
    assert set(CATEGORIES) == {'earrings', 'necklace', 'ring', 'bracelet'}

    for key, category in CATEGORIES.items():
        assert category.key == key, key
        # Every category offers the same three frames, and they must be three different
        # photographs — this is the bug that shipped once already.
        assert set(category.framings) == {'hero', 'profile', 'detail'}, key
        assert len(set(category.framings.values())) == 3, f'{key} repeats a framing'

    # Substring matching is a trap here — "near" contains "ear" and "earrings" contains
    # "ring" — so every check below is on whole words.
    def says(text: str, word: str) -> bool:
        return re.search(rf'\b{word}s?\b', text.lower()) is not None

    # Where the camera is pointed. craft is excluded deliberately: it names the OTHER
    # body parts on purpose, to say they are bare.
    def aim(category: Category) -> str:
        return ' '.join([category.placement, *category.framings.values()])

    # The whole point: a ring is shot on a hand, and the camera never goes near an ear.
    ring = aim(CATEGORIES['ring'])
    assert says(ring, 'finger') and says(ring, 'hand')
    for wrong in ('ear', 'earring'):
        assert not says(ring, wrong), f'ring framing still points at the {wrong}'

    neck = aim(CATEGORIES['necklace'])
    assert says(neck, 'collarbone') and not says(neck, 'ear')

    wrist = aim(CATEGORIES['bracelet'])
    assert says(wrist, 'wrist') and not says(wrist, 'ear')

    # And the earrings preset must still be the one that works today.
    assert says(aim(CATEGORIES['earrings']), 'ear')

    # Each category tells the model not to invent the other three, and never names
    # itself in its own negative hint.
    for key, category in CATEGORIES.items():
        assert not says(category.negative, key.rstrip('s')), \
            f'{key} forbids its own product'
        for other in CATEGORIES:
            if other != key:
                assert says(category.negative, other.rstrip('s')), \
                    f'{key} does not rule out {other}'

    assert SCHEMA['properties']['category']['enum'] == sorted(CATEGORIES)

    # No credentials, no crash: identify() degrades instead of failing the upload.
    key = os.environ.pop('ANTHROPIC_API_KEY', None)
    try:
        category, description = identify('nonexistent.jpg')
    finally:
        if key is not None:
            os.environ['ANTHROPIC_API_KEY'] = key
    assert category is DEFAULT_CATEGORY and description == DEFAULT_PRODUCT

    print('product ok')


if __name__ == '__main__':
    demo()
