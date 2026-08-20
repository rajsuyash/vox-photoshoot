"""How the photograph is composed: expression, view, angle, distance, pose, frame.

The keys are stable because the database stores them in jobs.params. The prose is not —
it is prompt engineering and will be rewritten many times without a migration.

Two things here are load-bearing and easy to get wrong later:

  Frames and poses are CURATED PER CATEGORY. The obvious design is one shared list, and
  it is wrong: "ankle", "both feet" and "product strap across torso" cannot apply to a
  ring, and a menu of sixty options where fifty are impossible is worse than a menu of
  eight that all work. A ring gets hands and fingers; a necklace gets neck and
  decolletage.

  Nothing here is free text. Every value the client sends is looked up in these tables
  before it reaches a prompt, so an unknown pose is a 400 rather than a sentence someone
  typed being appended to our prompt.

    .venv/bin/python composition.py       # self-check, no network
"""

import dataclasses

# --- global controls, shared by every category ---------------------------------------

# Replaces the smile that used to be hardcoded into every prompt. A brand that wants a
# composed, serious campaign could not have one at any price before this existed.
EXPRESSIONS = {
    'smiling': 'smiling warmly with a genuine open smile',
    'natural': 'with a relaxed, natural expression',
    'soft': 'with a soft, gentle expression and a faint smile',
    'confident': 'with a confident, self-assured expression',
    'serious': 'with a composed, serious expression, not smiling',
    'neutral': 'with a calm neutral expression, lips together',
    'laughing': 'laughing openly, caught mid-laugh',
}
DEFAULT_EXPRESSION = 'smiling'

VIEWS = {
    'front': 'She faces the camera squarely, front on.',
    'front-34': 'She is turned about forty-five degrees from the camera, a front '
                'three-quarter view.',
    'side': 'She is in full profile, side on to the camera.',
    'rear-34': 'She is turned away from the camera at about forty-five degrees, looking '
               'back over her shoulder.',
    'back': 'She has her back to the camera.',
}
DEFAULT_VIEW = 'front-34'

ANGLES = {
    'eye-level': 'The camera is at eye level, straight on.',
    'from-above': 'The camera is slightly above her, looking down.',
    'from-below': 'The camera is slightly below her, looking up.',
    'tilted': 'The camera is tilted a few degrees for a dynamic frame.',
    'top-down': 'The camera is directly overhead, looking straight down.',
}
DEFAULT_ANGLE = 'eye-level'

DISTANCES = {
    'close': 'Shot close, the piece and the skin around it filling most of the frame.',
    'mid': 'Shot at mid range, head and upper body in frame.',
    'wide': 'Shot wide, the full figure and the location around her clearly visible.',
}
DEFAULT_DISTANCE = 'mid'

# fal accepts all of these; higgsfield is narrower and providers.Provider rejects an
# aspect its backend cannot do rather than quietly substituting one.
ASPECTS = {
    '3:4': 'portrait, the house default',
    '1:1': 'square, for marketplace listings',
    '4:5': 'portrait, for Instagram',
    '9:16': 'tall, for stories and reels',
    '2:3': 'portrait, classic print',
    '4:3': 'landscape',
    '3:2': 'landscape, classic print',
    '16:9': 'wide, for a website banner',
}
DEFAULT_ASPECT = '3:4'

# Maps onto providers.FAL_RESOLUTION, which already had a 4K tier nobody could reach.
RESOLUTIONS = {'2K': 'high', '4K': 'max'}
DEFAULT_RESOLUTION = '2K'

# 4K is roughly twice the work at the provider, so it is twice the credits. Provisional
# until the real fal rate is measured — 2 is the safe direction to be wrong in, and if it
# turns out to cost the same this drops to 1 rather than quietly becoming margin.
RESOLUTION_MULTIPLIER = {'2K': 1, '4K': 2}


# --- per-category frames and poses ---------------------------------------------------

FRAMES = {
    'ring': {
        'single-finger': 'the ringed finger alone fills the frame, cropped at the knuckle',
        'fingers': 'her fingers fill the frame, the ringed finger foremost',
        'single-hand': 'one hand fills the frame, cropped at the wrist',
        'both-hands': 'both hands are in frame together, the ringed hand in front',
        'hand-and-face': 'her hand is raised beside her face, both in frame',
        'upper-body': 'her upper body from the waist up, the ringed hand raised and clear',
        'full-body': 'her whole figure, the ringed hand held away from her body',
    },
    'earrings': {
        'ear': 'one ear fills the frame, cropped so no other feature is included',
        'head': 'her head and neck fill the frame, hair tucked behind the ear',
        'head-and-shoulders': 'her head and shoulders, turned so the ear reads clearly',
        'single-shoulder': 'one shoulder and the side of her neck, the ear above',
        'upper-body': 'her upper body from the chest up, head turned to show the ear',
        'full-body': 'her whole figure, head turned so the earring catches the light',
    },
    'necklace': {
        'neck': 'her neck and collarbone fill the frame, cropped below the chin',
        'decolletage': 'her decolletage, collarbone to upper chest',
        'chest': 'her chest and shoulders, the piece lying centred',
        'head-and-shoulders': 'her head and shoulders, chin lifted to open the neck',
        'upper-body': 'her upper body from the waist up',
        'full-body': 'her whole figure, the piece still clearly readable',
    },
    'bracelet': {
        'wrist': 'her wrist alone fills the frame, cropped mid-forearm',
        'forearm': 'her forearm and wrist, hand relaxed',
        'single-hand': 'one hand and wrist, cropped below the elbow',
        'both-hands': 'both hands together, the braceleted wrist foremost',
        'hand-and-face': 'her wrist raised near her face, both in frame',
        'upper-body': 'her upper body, the braceleted arm raised and clear',
        'full-body': 'her whole figure, the braceleted arm held away from her body',
    },
}

POSES = {
    'ring': {
        'hand-to-cheek': 'her hand rests lightly against her cheek',
        'hand-under-chin': 'her chin rests on the back of her hand',
        'hands-clasped': 'her hands are loosely clasped in front of her',
        'fingers-spread': 'her fingers are relaxed and slightly spread, palm down',
        'hand-on-shoulder': 'her ringed hand rests on the opposite shoulder',
        'reaching-out': 'her hand reaches gently toward the camera, fingers relaxed',
        'hand-in-hair': 'her hand is raised into her hair at the temple',
        'arms-crossed': 'her arms are lightly crossed, the ringed hand visible on top',
    },
    'earrings': {
        'looking-over-shoulder': 'she looks back over her shoulder toward the camera',
        'chin-lifted': 'her chin is lifted and her head turned to one side',
        'hand-to-neck': 'her fingertips rest at the side of her neck below the ear',
        'tucking-hair': 'she tucks her hair back behind the ear',
        'head-tilted': 'her head is tilted slightly so the drop swings clear',
        'glancing-away': 'she looks away from the camera, off to one side',
        'hands-at-sides': 'her hands rest at her sides, head turned to the camera',
    },
    'necklace': {
        'chin-lifted': 'her chin is lifted, lengthening the neck',
        'hand-to-collarbone': 'her fingertips rest lightly on her collarbone',
        'touching-pendant': 'she touches the pendant lightly with one hand',
        'hands-clasped': 'her hands are loosely clasped in front of her',
        'shoulders-back': 'her shoulders are drawn back, posture open',
        'looking-down': 'she looks gently downward toward the piece',
        'hands-at-sides': 'her hands rest at her sides, shoulders square',
    },
    'bracelet': {
        'hand-to-cheek': 'her braceleted wrist is raised, hand resting near her cheek',
        'hand-in-hair': 'her braceleted hand is raised into her hair',
        'arms-crossed': 'her arms are lightly crossed, the braceleted wrist on top',
        'wrist-forward': 'her wrist is held toward the camera, fingers relaxed',
        'hands-clasped': 'her hands are loosely clasped, the braceleted wrist in front',
        'adjusting-cuff': 'her other hand rests on the bracelet as if adjusting it',
        'arm-extended': 'her braceleted arm is extended and relaxed',
    },
}


@dataclasses.dataclass(frozen=True)
class Composition:
    """What the client asked for. Frozen: a shoot's composition must not drift mid-job."""

    expression: str = DEFAULT_EXPRESSION
    view: str = DEFAULT_VIEW
    angle: str = DEFAULT_ANGLE
    aspect: str = DEFAULT_ASPECT
    resolution: str = DEFAULT_RESOLUTION
    pose: str = ''                       # '' means "let the shot decide"
    # Custom mode only. On a three-shot shoot the framing owns these, because
    # hero/profile/detail already ARE frames and distances.
    frame: str = ''
    distance: str = ''

    @property
    def quality(self) -> str:
        """The provider's word for our resolution."""
        return RESOLUTIONS.get(self.resolution, RESOLUTIONS[DEFAULT_RESOLUTION])

    def multiplier(self) -> int:
        return RESOLUTION_MULTIPLIER.get(self.resolution, 1)

    def direction(self, category_key: str) -> str:
        """The sentences appended to the framing line on a normal three-shot shoot.

        View, angle and pose only. Frame and distance are deliberately absent: the shot's
        own framing already decided those, and saying both would give the model two
        answers to the same question.
        """
        parts = [VIEWS.get(self.view, ''), ANGLES.get(self.angle, '')]
        if self.pose:
            gesture = POSES.get(category_key, {}).get(self.pose)
            if gesture:
                parts.append(f'Her pose: {gesture}.')
        return ' '.join(p for p in parts if p)

    def custom_framing(self, category_key: str) -> str:
        """The whole framing line in Custom mode, built from frame and distance.

        Nothing from category.framings is used here, which is the point — a shot-owned
        crop and a client-chosen frame would contradict each other, and the model would
        resolve that contradiction arbitrarily.
        """
        frame = FRAMES.get(category_key, {}).get(self.frame or '')
        distance = DISTANCES.get(self.distance or DEFAULT_DISTANCE, '')
        opening = f'The frame is composed so that {frame}. ' if frame else ''
        return f'{opening}{distance} {self.direction(category_key)}'.strip()


def parse(raw: dict | None, category_key: str) -> Composition:
    """Build a Composition from client input, keeping only values we actually offer.

    Unknown values fall back to the default rather than raising. The endpoint validates
    and 400s on nonsense; this is the second line, so a stored job from an older
    vocabulary still renders instead of crashing a reshoot months later.
    """
    raw = raw or {}

    def pick(value, table, fallback):
        value = str(value or '').strip()
        return value if value in table else fallback

    frames = FRAMES.get(category_key, {})
    poses = POSES.get(category_key, {})
    return Composition(
        expression=pick(raw.get('expression'), EXPRESSIONS, DEFAULT_EXPRESSION),
        view=pick(raw.get('view'), VIEWS, DEFAULT_VIEW),
        angle=pick(raw.get('angle'), ANGLES, DEFAULT_ANGLE),
        aspect=pick(raw.get('aspect'), ASPECTS, DEFAULT_ASPECT),
        resolution=pick(raw.get('resolution'), RESOLUTIONS, DEFAULT_RESOLUTION),
        pose=pick(raw.get('pose'), poses, ''),
        frame=pick(raw.get('frame'), frames, ''),
        distance=pick(raw.get('distance'), DISTANCES, ''),
    )


def options_for(category_key: str) -> dict:
    """Everything the UI needs to draw the section for one category."""
    labelled = lambda table: [{'key': k, 'label': _label(k)} for k in table]
    return {
        'expressions': labelled(EXPRESSIONS),
        'views': labelled(VIEWS),
        'angles': labelled(ANGLES),
        'distances': labelled(DISTANCES),
        'aspects': [{'key': k, 'label': k, 'note': note} for k, note in ASPECTS.items()],
        'resolutions': [{'key': k, 'label': k,
                         'multiplier': RESOLUTION_MULTIPLIER.get(k, 1)}
                        for k in RESOLUTIONS],
        'frames': labelled(FRAMES.get(category_key, {})),
        'poses': labelled(POSES.get(category_key, {})),
        'defaults': {'expression': DEFAULT_EXPRESSION, 'view': DEFAULT_VIEW,
                     'angle': DEFAULT_ANGLE, 'aspect': DEFAULT_ASPECT,
                     'resolution': DEFAULT_RESOLUTION, 'distance': DEFAULT_DISTANCE},
    }


def _label(key: str) -> str:
    """'hand-to-cheek' -> 'Hand to cheek'. Keys are the contract; labels are cosmetic."""
    words = key.replace('-34', ' three-quarter').replace('-', ' ')
    return words[:1].upper() + words[1:]


def demo() -> None:
    import product

    # Every category the app can shoot needs frames and poses, or its Composition section
    # renders empty and the client silently gets fewer controls than the next category.
    for key in product.CATEGORIES:
        assert key in FRAMES and FRAMES[key], f'{key} has no frames'
        assert key in POSES and POSES[key], f'{key} has no poses'

    # No empty prose anywhere: an empty string joins into the prompt as a missing
    # sentence and is invisible until someone reads a generated image and wonders.
    for name, table in (('expressions', EXPRESSIONS), ('views', VIEWS),
                        ('angles', ANGLES), ('distances', DISTANCES)):
        for key, text in table.items():
            assert text.strip(), f'{name}[{key}] is empty'
    for group in (FRAMES, POSES):
        for category, table in group.items():
            for key, text in table.items():
                assert text.strip(), f'{category}/{key} is empty'

    assert DEFAULT_EXPRESSION in EXPRESSIONS and DEFAULT_VIEW in VIEWS
    assert DEFAULT_ANGLE in ANGLES and DEFAULT_ASPECT in ASPECTS
    assert DEFAULT_RESOLUTION in RESOLUTIONS and DEFAULT_DISTANCE in DISTANCES

    # The smile that used to be hardcoded must still be reachable, or every existing
    # shoot silently changes character.
    assert EXPRESSIONS['smiling'] == 'smiling warmly with a genuine open smile'

    # parse() must never trust the client.
    junk = parse({'expression': 'levitating', 'view': '../../etc/passwd',
                  'pose': 'DROP TABLE jobs', 'aspect': '999:1',
                  'resolution': '8K', 'frame': 'ankle'}, 'ring')
    assert junk.expression == DEFAULT_EXPRESSION and junk.view == DEFAULT_VIEW
    assert junk.pose == '' and junk.frame == '' and junk.aspect == DEFAULT_ASPECT
    assert junk.resolution == DEFAULT_RESOLUTION

    # A pose from the wrong category is not a pose. 'tucking-hair' is an earring pose and
    # must not survive on a ring, or a ring shoot is directed to tuck hair behind an ear.
    assert parse({'pose': 'tucking-hair'}, 'ring').pose == ''
    assert parse({'pose': 'tucking-hair'}, 'earrings').pose == 'tucking-hair'

    good = parse({'expression': 'serious', 'view': 'side', 'angle': 'from-below',
                  'pose': 'hand-to-cheek', 'resolution': '4K'}, 'ring')
    assert good.expression == 'serious' and good.multiplier() == 2
    assert good.quality == 'max'
    assert parse({}, 'ring').multiplier() == 1
    assert parse({}, 'ring').quality == 'high'

    # direction() carries view, angle and pose — and never frame or distance, which the
    # shot owns on a three-frame shoot.
    line = good.direction('ring')
    assert 'full profile' in line and 'below' in line and 'cheek' in line
    assert 'fills the frame' not in line, 'direction() leaked a frame'

    # Custom mode is the opposite: the framing line comes only from frame and distance.
    custom = parse({'frame': 'single-hand', 'distance': 'close', 'view': 'front'}, 'ring')
    built = custom.custom_framing('ring')
    assert 'one hand fills the frame' in built and 'Shot close' in built

    # An unset pose contributes nothing rather than the word 'None'.
    assert 'None' not in parse({}, 'ring').direction('ring')
    assert 'pose' not in parse({}, 'ring').direction('ring').lower()

    assert options_for('ring')['frames'], 'the UI would render an empty section'
    assert _label('hand-to-cheek') == 'Hand to cheek'
    print('composition ok')


if __name__ == '__main__':
    demo()
