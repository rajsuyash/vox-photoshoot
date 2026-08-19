"""What the uploaded piece is, how big it is, and where on the body it goes.

The shoot used to assume every upload was the client's drop earrings: the description,
the occlusion clause, the framings and the negative hint all said "earring". Upload a
ring and the reference photo went to the model correctly but the prose told it to hang
the thing off an ear, and prose wins on placement. So the piece is read off the photo
before the prompt is built.

Two kinds of fact go into that prompt, and they come from different places:

  detected  what the piece IS — category, sub-type, description. Visible in the photo,
            so a vision model reads it and the client never types it.
  asked     how BIG it is, and which finger or hand. A product shot on a table carries
            no scale reference at all, so no model can infer it — the client picks it.

Scale is the one that makes an image look real. Without it the generator renders a
plausible-looking ring rather than this ring, which is why every competitor asks for
size against a body part rather than in millimetres.

    .venv/bin/python product.py                      # self-check, no API call
    .venv/bin/python -c "import product; print(product.identify('ring.jpg'))"
"""

import base64
import io
import json
import pathlib
import re
import tempfile
from dataclasses import dataclass, field, replace

# Single-image classification with a fixed schema. Haiku is the right tier for it and
# costs about a fifth of a US cent per shoot, against ~4.5 fal credits for the images.
VISION_MODEL = 'claude-haiku-4-5'

# Ordered smallest to largest. A slider, not a set — the UI renders them in this order.
SIZES = ('xs', 's', 'm', 'l', 'xl')
DEFAULT_SIZE = 'm'

# Rings only. 'thumb' is not a finger and reads wrong in the prompt, so it is special
# cased where the clause is built rather than here.
FINGERS = ('index', 'middle', 'ring', 'little', 'thumb')
HANDS = ('left', 'right')


@dataclass(frozen=True)
class Category:
    """Everything about the prompt that depends on where the piece is worn."""
    key: str
    label: str
    placement: str        # completes "wearing <product> <placement>"
    craft: str            # what must stay bare and unobstructed for this piece
    negative: str         # the other three categories, not to be invented
    scale_label: str      # what the client sees above the size control
    scale: dict           # xs..xl -> a sentence anchoring size to a body part
    types: dict           # sub-type -> how that type sits on the body
    default_type: str
    asks: tuple           # extra controls this category needs from the client
    framings: dict = field(default_factory=dict)  # set below, one block per category

    def worn(self, options=None):
        """The full 'wearing X ...' clause: placement, sub-type, and scale."""
        options = options or Options()
        placement = self.placement
        if 'finger' in self.asks:
            finger = options.finger if options.finger in FINGERS else 'ring'
            hand = options.hand if options.hand in HANDS else 'right'
            placement = (f'on the thumb of her {hand} hand' if finger == 'thumb'
                         else f'on the {finger} finger of her {hand} hand')
        type_key = options.type if options.type in self.types else self.default_type
        size = options.size if options.size in self.scale else DEFAULT_SIZE
        return f'{placement}. {self.types[type_key]} {self.scale[size]}'


@dataclass(frozen=True)
class Options:
    """What the client chose. Everything here is unknowable from the photo."""
    size: str = DEFAULT_SIZE
    type: str = ''            # '' falls back to whatever was detected
    finger: str = 'ring'      # rings only
    hand: str = 'right'       # rings only
    instructions: str = ''    # free text, e.g. "keep the engraving"


# The framing keys are load-bearing: shoot.SEEDS, app.merge_images and the reshoot
# endpoint all address a frame by these names. Only the prose changes per category.
#
# Nano Banana Pro weights the reference images heavily and framing prose lightly, so
# every entry states the crop in camera terms (what is in frame, what is cut off)
# rather than describing a mood. Three genuinely different photographs, not one three
# times: a wide that sells the location, a profile, and a macro on the piece.
CATEGORIES = {
    'earrings': Category(
        key='earrings',
        label='Earrings',
        placement='in her ears',
        craft='Hair tucked back behind both ears so the full earring including any drop '
              'is visible and unobstructed. Her neck and collarbone are bare.',
        negative='Do not invent a different jewellery design, and do not add a necklace, '
                 'pendant, nose ring, bracelet or finger ring that is not in the reference.',
        scale_label='Earring size (against her earlobe)',
        scale={
            'xs': 'It is tiny, sitting entirely within the earlobe and smaller than the '
                  'lobe itself.',
            's': 'It is small, about two thirds the height of her earlobe.',
            'm': 'It is about the height of her earlobe.',
            'l': 'It is large, hanging down to about the line of her jaw.',
            'xl': 'It is a bold statement piece, hanging well below the jawline toward '
                  'the collarbone.',
        },
        types={
            'stud': 'It is a stud, sitting flat against the front of the earlobe with '
                    'nothing hanging below it.',
            'hoop': 'It is a hoop, a closed ring passing through the lobe and hanging '
                    'below it.',
            'drop': 'It hangs as a drop below the lobe, swinging clear of her neck.',
            'chandelier': 'It is a chandelier, tiered and widening as it falls from '
                          'the lobe.',
            'huggie': 'It is a huggie, a small thick hoop hugging the earlobe closely '
                      'with no gap.',
            'crawler': 'It is an ear crawler, climbing the outer edge of the ear upward '
                       'from the lobe rather than hanging down.',
            'cuff': 'It is an ear cuff, clipped around the outer rim of the ear well '
                    'above the lobe, with no piercing.',
        },
        default_type='drop',
        asks=('type', 'size'),
    ),
    'necklace': Category(
        key='necklace',
        label='Necklace',
        placement='around her neck',
        craft='Hair swept back off her shoulders and her neckline open so the full '
              'necklace including any pendant lies flat against her skin, unobstructed '
              'by hair or fabric. Her ears are bare.',
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'nose ring, bracelet or finger ring that is not in the reference.',
        scale_label='Necklace size (how far it falls)',
        scale={
            'xs': 'It is a fine delicate piece sitting high and close at the base of '
                  'her throat.',
            's': 'It sits just below the hollow of her throat.',
            'm': 'It sits on her collarbone.',
            'l': 'It falls a hand\'s width below the collarbone, onto the upper chest.',
            'xl': 'It is a long statement piece falling well down the chest, with a '
                  'pendant about the width of two fingers.',
        },
        types={
            'pendant': 'It hangs as a single pendant on a fine chain.',
            'chain': 'It is a plain chain with no pendant.',
            'choker': 'It is a choker, sitting tight and high around the base of the neck.',
            'collar': 'It is a rigid collar lying flat on the collarbone.',
            'layered': 'It is layered, several strands hanging at different lengths.',
            'statement': 'It is a broad statement piece covering much of the upper chest.',
        },
        default_type='pendant',
        asks=('size',),
    ),
    'ring': Category(
        key='ring',
        label='Ring',
        placement='on the ring finger of her right hand',
        craft='Sleeves pushed well back so her hand, fingers and wrist are completely '
              'bare and unobstructed, fingers relaxed and slightly apart so the ring is '
              'never hidden behind another finger. Her ears and neck are bare.',
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'necklace, pendant, nose stud or bracelet that is not in the reference.',
        scale_label='Ring size (against her fingernail)',
        scale={
            'xs': 'The setting is small and delicate, about half the width of her '
                  'fingernail.',
            's': 'The setting is about two thirds the width of her fingernail.',
            'm': 'The setting is about as wide as her fingernail.',
            'l': 'The setting is large, about one and a half times the width of her '
                 'fingernail.',
            'xl': 'The setting is a bold statement piece, about twice the width of her '
                  'fingernail and sitting high off the finger.',
        },
        types={
            'solitaire': 'It is a solitaire, a single raised stone above a plain shank.',
            'band': 'It is a plain band with no raised setting.',
            'signet': 'It is a signet ring, a broad flat face on a solid shank.',
            'cocktail': 'It is a cocktail ring, a large ornate head sitting high off '
                        'the finger.',
            'stacking': 'It is a slim stacking ring worn as a single fine band.',
            'eternity': 'It is an eternity band, stones set continuously all the way round.',
        },
        default_type='solitaire',
        asks=('size', 'finger', 'hand'),
    ),
    'bracelet': Category(
        key='bracelet',
        label='Bracelet',
        placement='on her right wrist',
        craft='Sleeves pushed well back above the elbow so her wrist and forearm are '
              'completely bare and unobstructed, and the full bracelet sits clear of '
              'any fabric. Her ears and neck are bare.',
        negative='Do not invent a different jewellery design, and do not add earrings, a '
                 'necklace, pendant, nose stud or finger ring that is not in the reference.',
        scale_label='Bracelet size (against her wrist)',
        scale={
            'xs': 'It is a fine delicate band, far narrower than her wrist and sitting '
                  'close against the bone.',
            's': 'It is narrow, about a third of the width of her wrist.',
            'm': 'It is about half the width of her wrist.',
            'l': 'It is wide, covering much of the width of her wrist.',
            'xl': 'It is a bold cuff covering most of the wrist and reaching toward '
                  'the forearm.',
        },
        types={
            'bangle': 'It is a rigid bangle, a closed circle slipped over the hand.',
            'cuff': 'It is an open cuff with a gap at the underside of the wrist.',
            'chain': 'It is a flexible chain following the curve of the wrist.',
            'tennis': 'It is a tennis bracelet, a continuous line of matched stones.',
            'charm': 'It is a charm bracelet with charms hanging from the chain.',
        },
        default_type='bangle',
        asks=('size',),
    ),
}

# Framings live apart from the rest of the category so the four crop sets can be read
# against each other — they are the same three shots pointed at four body parts, and
# that only stays true if they sit side by side. The earrings block is the original
# copy, unchanged: it is the one already proven against the client's own product.
CATEGORIES['earrings'] = replace(CATEGORIES['earrings'], framings={
    'hero': (
        'WIDE SHOT. Full upper body from the waist up, she is turned slightly away and '
        'looking back toward the camera, standing well back from the lens so the whole '
        'location is visible around her and reads clearly.'
    ),
    'profile': (
        'STRICT SIDE PROFILE, camera exactly 90 degrees to her face. She faces fully to '
        'the left edge of the frame and does not look at the camera. The silhouette of '
        'her nose, lips and chin is drawn against the background, and the whole ear '
        'with the earring is square on to the lens.'
    ),
    'detail': (
        'EXTREME CLOSE UP of the ear and jaw only. The frame is filled by the ear and '
        'the earring, cropped so the top of the head and the mouth are outside the '
        'frame. Macro jewellery photography, the metal and stones fill much of the '
        'picture and the face is only a supporting element.'
    ),
})
CATEGORIES['necklace'] = replace(CATEGORIES['necklace'], framings={
    'hero': (
        'WIDE SHOT. Full upper body from the waist up, she is turned slightly away and '
        'looking back toward the camera, standing well back from the lens so the whole '
        'location is visible around her and reads clearly.'
    ),
    'profile': (
        'THREE QUARTER TURN, camera at 45 degrees and slightly below her chin so the '
        'line of her throat and collarbone is drawn against the background. She looks '
        'away from the camera, chin lifted, and the whole necklace from clasp to '
        'pendant is square on to the lens.'
    ),
    'detail': (
        'EXTREME CLOSE UP of the neck, collarbone and upper chest only. The frame is '
        'filled by the necklace, cropped so her mouth and shoulders are outside the '
        'frame. Macro jewellery photography, the metal and stones fill much of the '
        'picture and the skin is only a supporting element.'
    ),
})
CATEGORIES['ring'] = replace(CATEGORIES['ring'], framings={
    'hero': (
        'WIDE SHOT. Full upper body from the waist up, she is turned slightly away and '
        'looking back toward the camera with one hand raised near her jaw so the ring '
        'reads clearly, standing well back from the lens so the whole location is '
        'visible around her.'
    ),
    'profile': (
        'HAND AND FOREARM fill the frame, held up in front of her, camera level with '
        'the hand and 90 degrees to the back of it. Her face is soft and out of focus '
        'behind the hand and she does not look at the camera. The ringed finger is '
        'square on to the lens against the blurred location.'
    ),
    'detail': (
        'EXTREME CLOSE UP of the hand only, cropped at the wrist so no part of her face '
        'or body is in the frame. The frame is filled by the ringed finger and the '
        'ring. Macro jewellery photography, the metal and stones fill much of the '
        'picture and the skin is only a supporting element.'
    ),
})
CATEGORIES['bracelet'] = replace(CATEGORIES['bracelet'], framings={
    'hero': (
        'WIDE SHOT. Full upper body from the waist up, she is turned slightly away and '
        'looking back toward the camera with one arm raised so the wrist and bracelet '
        'read clearly, standing well back from the lens so the whole location is '
        'visible around her.'
    ),
    'profile': (
        'WRIST AND FOREARM fill the frame, arm held up and bent, camera level with the '
        'wrist and 90 degrees to it. Her face is soft and out of focus behind the arm '
        'and she does not look at the camera. The whole circumference of the bracelet '
        'is square on to the lens against the blurred location.'
    ),
    'detail': (
        'EXTREME CLOSE UP of the wrist only, cropped mid-forearm and mid-hand so no '
        'part of her face or body is in the frame. The frame is filled by the bracelet. '
        'Macro jewellery photography, the metal and stones fill much of the picture and '
        'the skin is only a supporting element.'
    ),
})

# Falls back to the client's own piece. Their catalogue is mostly earrings, so a failed
# detection lands on the shoot this app has always produced rather than on nothing.
DEFAULT_CATEGORY = CATEGORIES['earrings']
DEFAULT_PRODUCT = 'yellow gold kite-shaped diamond drop earrings with a fine chain'

# Every sub-type across every category, because a JSON schema enum cannot depend on
# another field. A type that does not belong to the detected category is discarded.
ALL_TYPES = sorted({t for c in CATEGORIES.values() for t in c.types})


def types_for(category_key: str) -> list[str]:
    """Sub-types the client may pick, once the category is known."""
    return sorted(CATEGORIES[category_key].types)


# Short on purpose: a longer description hallucinated a matching necklace and flattened
# the pavé. The model is told to name the metal, stones and silhouette and stop there.
BRIEF = f"""Identify this piece of jewellery from the photograph.

category: which of {sorted(CATEGORIES)} it is worn as. If the photograph shows a matched
set, pick the piece that dominates the frame.

type: the sub-style, from {ALL_TYPES}. Pick one that belongs to the category you chose —
studs and hoops are earrings, signets and solitaires are rings, chokers and pendants are
necklaces, bangles and cuffs are bracelets. If none fits, pick the closest.

description: one short noun phrase a photographer would use to identify it on a shot
list — metal colour, stone setting, and silhouette. Fifteen words at most. No sentence,
no adjectives about beauty or quality, no mention of the background or packaging.
Examples: "yellow gold solitaire ring with a pave band", "silver hoop earrings",
"emerald and gold layered necklace with a teardrop pendant".

Name the stone setting explicitly when you can see it — pave, solitaire, cluster, halo,
channel, bezel. This is the detail that gets lost: a pave of many tiny stones is
otherwise redrawn as one large stone, and the piece stops being the client's.

Do not guess the physical size. A product photograph carries no scale reference and the
client is asked for size separately."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'category': {'type': 'string', 'enum': sorted(CATEGORIES)},
        'type': {'type': 'string', 'enum': ALL_TYPES},
        'description': {'type': 'string'},
    },
    'required': ['category', 'type', 'description'],
    'additionalProperties': False,
}


@dataclass(frozen=True)
class Piece:
    """What the upload was read as, and whether it was read at all."""
    category: Category
    description: str
    detected: bool
    type: str = ''

    def __post_init__(self):
        if not self.type:
            object.__setattr__(self, 'type', self.category.default_type)


# Anthropic accepts these four and rejects everything else with a 400. A client
# photographing a ring on their desk sends an iPhone HEIC, which is not on the list —
# that 400 is what silently shot a ring as the client's earrings in production.
# Everything is re-encoded to JPEG rather than sniffed, so the format never matters.
VISION_MEDIA_TYPE = 'image/jpeg'

# Anthropic downscales anything larger than this before the model sees it, so sending
# a 12MP phone photo only spends upload bandwidth and risks the request size limit.
VISION_MAX_EDGE = 1568


def encode(image_path) -> str:
    """Any image the client can produce -> base64 JPEG the vision API will accept."""
    from PIL import Image

    try:  # iPhone photos. Optional: without it HEIC raises here and we fall back.
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    with Image.open(image_path) as image:
        # convert() first: HEIC and PNG can carry alpha or a palette, and JPEG has
        # neither. thumbnail() is a no-op on anything already small enough.
        image = image.convert('RGB')
        image.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
    return base64.standard_b64encode(buffer.getvalue()).decode()


def identify(image_path) -> Piece:
    """Read the piece off its photograph.

    Never raises. A missing key, an unreadable file, a network failure or a nonsense
    answer falls back to the client's earrings — the shoot this app produced before
    detection existed. The caller is told, via Piece.detected, so a fallback can be
    shown rather than quietly delivering the wrong piece.
    """
    try:
        import anthropic

        response = anthropic.Anthropic().messages.create(
            model=VISION_MODEL,
            max_tokens=256,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image',
                     'source': {'type': 'base64', 'media_type': VISION_MEDIA_TYPE,
                                'data': encode(image_path)}},
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
        # The enum spans every category, so the model can return a hoop for a ring.
        # Wrong pairings are dropped rather than trusted; Piece falls back to the
        # category default.
        sub_type = answer['type'] if answer['type'] in category.types else ''
    except Exception as error:
        print(f'product.identify failed ({error!r}); falling back to '
              f'{DEFAULT_CATEGORY.key}', flush=True)
        return Piece(DEFAULT_CATEGORY, DEFAULT_PRODUCT, detected=False)
    return Piece(category, description, detected=True, type=sub_type)


def demo() -> None:
    """Self-check: the presets, without spending anything."""
    assert set(CATEGORIES) == {'earrings', 'necklace', 'ring', 'bracelet'}

    # Substring matching is a trap here — "near" contains "ear" and "earrings" contains
    # "ring" — so every check below is on whole words.
    def says(text: str, word: str) -> bool:
        return re.search(rf'\b{word}s?\b', text.lower()) is not None

    for key, category in CATEGORIES.items():
        assert category.key == key, key
        # Every category offers the same three frames, and they must be three different
        # photographs — this is the bug that shipped once already.
        assert set(category.framings) == {'hero', 'profile', 'detail'}, key
        assert len(set(category.framings.values())) == 3, f'{key} repeats a framing'
        # A size control with nothing behind it would silently do nothing.
        assert tuple(sorted(category.scale, key=SIZES.index)) == SIZES, key
        assert len(set(category.scale.values())) == len(SIZES), f'{key} repeats a size'
        assert category.default_type in category.types, key
        assert 'size' in category.asks, f'{key} never asks for scale'

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
    assert SCHEMA['properties']['type']['enum'] == ALL_TYPES

    # --- worn(): the clause the client's choices actually produce -------------------
    ring_cat = CATEGORIES['ring']
    # Size must change the prompt, or the control is decoration.
    assert len({ring_cat.worn(Options(size=s)) for s in SIZES}) == len(SIZES)
    # So must sub-type.
    assert len({ring_cat.worn(Options(type=t)) for t in ring_cat.types}) == \
        len(ring_cat.types)

    left_index = ring_cat.worn(Options(finger='index', hand='left'))
    assert 'index finger of her left hand' in left_index, left_index
    assert 'thumb of her left hand' in ring_cat.worn(Options(finger='thumb', hand='left'))

    # Nonsense in must not blow up a shoot: unknown values fall back to the defaults.
    for bad in (Options(size='enormous'), Options(type='hoop'),
                Options(finger='elbow'), Options(hand='third')):
        clause = ring_cat.worn(bad)
        assert 'fingernail' in clause, (bad, clause)

    # Categories that do not ask for a finger must never name one.
    for key in ('earrings', 'necklace', 'bracelet'):
        clause = CATEGORIES[key].worn(Options(finger='index', hand='left'))
        assert 'finger' not in clause, (key, clause)

    # Piece fills in the category default when detection returned no usable sub-type.
    assert Piece(CATEGORIES['ring'], 'x', True).type == 'solitaire'
    assert Piece(CATEGORIES['ring'], 'x', True, type='signet').type == 'signet'

    # An unreadable file degrades instead of failing the upload — and says so, because
    # a silent fallback delivered a ring shot as earrings before anyone noticed.
    piece = identify('nonexistent.jpg')
    assert piece.category is DEFAULT_CATEGORY and piece.description == DEFAULT_PRODUCT
    assert piece.detected is False

    # Every format a client can hand us has to reach the API as JPEG. An iPhone HEIC
    # sent as image/heic is a 400, which is exactly how the ring became an earring.
    from PIL import Image
    for mode, suffix, fmt in [('RGBA', '.png', 'PNG'), ('P', '.gif', 'GIF'),
                              ('RGB', '.bmp', 'BMP'), ('RGB', '.tiff', 'TIFF'),
                              ('RGB', '.heic', 'JPEG')]:  # .heic name, jpeg bytes
        path = pathlib.Path(tempfile.gettempdir()) / f'vox-encode-test{suffix}'
        Image.new(mode, (3000, 2000), 'red').save(path, format=fmt)
        decoded = Image.open(io.BytesIO(base64.b64decode(encode(path))))
        assert decoded.format == 'JPEG', (suffix, decoded.format)
        assert max(decoded.size) == VISION_MAX_EDGE, (suffix, decoded.size)
        path.unlink()

    print('product ok')


if __name__ == '__main__':
    demo()
