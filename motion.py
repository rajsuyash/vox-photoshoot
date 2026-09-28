"""Motion vocabulary for turning one shoot still into a short video ad.

Mirrors composition.py's shape and its reasons: motions are CURATED PER CATEGORY (a
ring turns a hand toward the camera; a necklace turns shoulders and chin — a bracelet
gesture on a necklace shoot is worn on the wrong body part), keys are stable because a
motion will eventually be stored in jobs.params, and the prose is not — it will be
rewritten many times without a migration.

One rule here is new and not in composition.py: REVEAL. A jewellery video ad has one
job a still photo does not — proving the piece is real by turning to face the camera —
and a spike against a real necklace still (2026-09-27) showed that if the piece is not
already clearly visible in frame 1 (partly turned away, pendant hidden), a naive motion
prompt keeps it hidden or invents a different piece entirely. So every category carries
at least one motion flagged reveals=True, and parse() forces one whenever the director
was not confident the piece was already in full view.

    .venv/bin/python motion.py       # self-check, no network
"""

import dataclasses
import re

import locations

# --- motion vocabulary, curated per category ------------------------------------------


@dataclasses.dataclass(frozen=True)
class MotionSpec:
    prose: str
    reveals: bool   # does this motion turn the piece toward the camera into full view?


MOTIONS: dict[str, dict[str, MotionSpec]] = {
    'earrings': {
        'head-turn-to-ear': MotionSpec(
            'she turns her head just enough to bring the earring into view and '
            'catch the light, her face still toward the camera', reveals=True),
        'hair-tuck': MotionSpec(
            'she tucks a loose strand of hair behind her ear, revealing the earring',
            reveals=True),
        'chin-lift': MotionSpec(
            'her chin lifts slightly and she holds still, the earring catching '
            'the light', reveals=False),
        'glance-down': MotionSpec(
            'she glances gently downward, a soft natural movement', reveals=False),
    },
    'necklace': {
        'turn-to-camera': MotionSpec(
            'she turns her shoulders and face toward the camera, bringing the '
            'pendant to the centre of her neckline', reveals=True),
        'chin-lift-reveal': MotionSpec(
            'her chin lifts and her shoulders draw back, opening her neckline so '
            'the pendant comes fully into view', reveals=True),
        'breath-roll': MotionSpec(
            'she takes a soft breath and rolls one shoulder back', reveals=False),
        'push-in-hold': MotionSpec(
            'she holds still as the camera closes the distance toward her '
            'decolletage', reveals=False),
    },
    'ring': {
        'hand-to-camera': MotionSpec(
            'she raises her hand to chest height and turns it toward the camera, '
            'the ring coming square into view', reveals=True),
        'hand-rotate-reveal': MotionSpec(
            'she rotates her hand from the back toward the palm side, bringing '
            'the ring face fully into view', reveals=True),
        'fingers-spread': MotionSpec(
            'her fingers relax and spread slightly, palm held steady',
            reveals=False),
        'fabric-brush': MotionSpec(
            'her fingers brush lightly across the fabric at her shoulder',
            reveals=False),
    },
    'bracelet': {
        'wrist-to-camera': MotionSpec(
            'she raises her wrist to chest height and turns it toward the camera, '
            'bringing the bracelet square into the light', reveals=True),
        'arm-extend-reveal': MotionSpec(
            'she extends her arm gently toward the camera, the bracelet sliding '
            'fully into view', reveals=True),
        'wrist-rotate': MotionSpec(
            'she rotates her wrist slowly, turning the bracelet toward the light',
            reveals=False),
        'adjust-cuff': MotionSpec(
            'her other hand rests briefly on the bracelet as if adjusting it',
            reveals=False),
    },
}

# A category outside product.CATEGORIES (a stale job, a future category not yet wired
# here) falls back to this rather than a KeyError three layers down in render().
FALLBACK_CATEGORY = 'earrings'

# Flat index used by render(), which is category-agnostic (see its docstring for why).
# Keys are asserted unique across categories in demo() — that uniqueness is what makes
# this safe.
_ALL_MOTIONS: dict[str, MotionSpec] = {}
for _category_motions in MOTIONS.values():
    for _key, _spec in _category_motions.items():
        assert _key not in _ALL_MOTIONS, f'motion key {_key!r} reused across categories'
        _ALL_MOTIONS[_key] = _spec
del _category_motions, _key, _spec


def _motion_prose(key: str) -> str:
    spec = _ALL_MOTIONS.get(key)
    return spec.prose if spec else 'she moves gently, keeping the piece in view'


# --- camera / mood / pace, shared across categories ------------------------------------

# Every entry is bounded: it names the move AND where it stops, so a chest-up frame
# with her whole face in view is the floor, never a side effect the model has to
# infer. A 2026-09-27 spike showed 'rack-focus' read as a hard zoom by Kling and
# cropped her face at the eyes on the last frame — removed rather than reworded,
# because the whole point of a rack focus is to punch in hard, which is exactly the
# failure mode.
CAMERAS = {
    'push-in': 'a slow push-in, settling chest-up with her whole face in view',
    'orbit': 'a gentle orbit, settling chest-up with her whole face in view and '
            'subtle parallax on the background',
    'locked-light-sweep': 'a locked-off chest-up frame, her whole face in view, '
                          'with a soft light sweep across the stones',
}
DEFAULT_CAMERA = 'push-in'

MOODS = {
    'luxe': 'a polished, high-end luxury mood',
    'festive': 'a warm, festive, celebratory mood',
    'bridal': 'a soft, romantic, bridal mood',
    'everyday': 'a relaxed, easy, everyday mood',
}
DEFAULT_MOOD = 'luxe'

PACE = {
    'slow': 'slow pacing',
    'medium': 'medium pacing',
}
DEFAULT_PACE = 'slow'

PIECE_VISIBLE = ('clear', 'partial', 'hidden')
DEFAULT_PIECE_VISIBLE = 'clear'

MODELS = ('kling', 'seedance')
DURATIONS = (5, 10)

# --- fidelity, appended by code, never by the director model or a client note ----------

FIDELITY_LOCK = (
    'The jewellery stays exactly as in the first frame: same metal, stones, shape and '
    'size, nothing new added. By the end it faces the camera, visible and in focus. '
    'Her whole face stays in frame; her identity does not change. No text, logos or '
    'watermarks.'
)
# Kling only. Seedance's template is positive-phrasing only, per the brief.
NEGATIVE = ('jewellery changing design, different pendant shape, extra jewellery, '
            'morphing or melting metal, warped or extra fingers, face change, text, '
            'watermark, logo, blur, distortion')

# --- action text: the one piece of free text a director model may contribute -----------

MAX_ACTION_WORDS = 25
BANNED_ACTION_WORDS = ('text', 'logo', 'caption', 'new', 'another', 'extra', 'remove',
                       'cover', 'hide')
CONTRADICTION_PHRASES = ('turn away', 'turns away', 'turning away', 'looks away',
                         'looking away', 'back to camera', 'out of frame', 'out of view')
# Turning to profile risks walking the near-camera earring out of shot; a ring/necklace/
# bracelet shot does not share that specific risk.
CATEGORY_CONTRADICTION_PHRASES = {'earrings': ('profile',)}

# A director's action must read as third-person prose ABOUT the model ("she turns...",
# "her chin lifts..."), never an instruction addressed to her — a 2026-09-27 spike
# showed the director answering with the imperative itself ("gently lift chin..."),
# which then duplicated the chosen motion's own prose word for word. Bare imperative
# verb forms are the tell: third person adds an -s ("turns", "lifts"), so a sentence
# that OPENS on the bare form ("turn", "lift") is a command, not a description.
IMPERATIVE_START_WORDS = frozenset({
    'lift', 'turn', 'rotate', 'raise', 'tilt', 'move', 'slowly', 'gently', 'bring',
    'hold', 'settle', 'look', 'glance', 'tuck', 'draw', 'extend', 'roll', 'bend',
})

# Ignored when comparing an action's words against the chosen motion's own prose, so
# the overlap check measures content words, not shared connective tissue.
STOPWORDS = frozenset({
    'she', 'her', 'him', 'he', 'his', 'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in',
    'on', 'at', 'into', 'onto', 'with', 'as', 'it', 'its', 'that', 'this', 'while',
    'then', 'so', 'from', 'toward', 'towards', 'around', 'over', 'under', 'is', 'are',
    'be', 'being', 'was', 'were',
})

# Above this fraction of an action's content words already appearing in the motion's
# own prose, the action is saying nothing new — e.g. a director echoing "turn-to-
# camera"'s own prose back as its "action" ("she turns... bringing the pendant into
# view") rather than adding what the motion prose doesn't already say.
MAX_ACTION_MOTION_OVERLAP = 0.5

MAX_PROMPT_CHARS = 1000     # hard ceiling, both models
KLING_TARGET_CHARS = 650    # soft target for the Kling template specifically


def _content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z']+", text.lower()) if word not in STOPWORDS}


def clean_action(text, category_key: str, motion_prose: str = '') -> str | None:
    """A director's free-text action, or None if it cannot be trusted.

    Rejects (returns None) anything over MAX_ACTION_WORDS, anything naming text/logo/
    an invented extra piece, anything that contradicts FIDELITY_LOCK's "stays visible
    throughout" (turning away, going out of frame, or — for earrings only — turning to
    profile, which can walk the near-camera earring out of shot), anything that opens
    on a bare imperative verb (an instruction to her, not a description of her), and —
    when `motion_prose` is given — anything that mostly just repeats the chosen
    motion's own prose rather than adding something new (what the light does on the
    piece, for instance). A None here means the template omits the "Then, ..." beat
    and uses the motion's own prose alone.
    """
    # rstrip('.') before the word/phrase checks: the template always supplies its own
    # trailing period, so a director answer that already ends in one would double up
    # ("...as she faces the camera..") if left alone.
    text = (text or '').strip().rstrip('.').strip()
    if not text:
        return None
    if len(text.split()) > MAX_ACTION_WORDS:
        return None
    lower = text.lower()
    if any(re.search(rf'\b{re.escape(word)}s?\b', lower) for word in BANNED_ACTION_WORDS):
        return None
    phrases = CONTRADICTION_PHRASES + CATEGORY_CONTRADICTION_PHRASES.get(category_key, ())
    if any(phrase in lower for phrase in phrases):
        return None
    first_word = re.match(r"[a-z']+", lower)
    if first_word and first_word.group() in IMPERATIVE_START_WORDS:
        return None
    if motion_prose:
        action_words = _content_words(text)
        if action_words:
            overlap = action_words & _content_words(motion_prose)
            if len(overlap) / len(action_words) > MAX_ACTION_MOTION_OVERLAP:
                return None
    return text


# --- Plan: what actually gets rendered --------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Plan:
    motion: str
    camera: str
    mood: str
    pace: str
    action: str
    piece_visible: str   # 'clear' | 'partial' | 'hidden' — what the director saw


def _motions_for(category_key: str) -> dict[str, MotionSpec]:
    return MOTIONS.get(category_key, MOTIONS[FALLBACK_CATEGORY])


def _reveal_motion_key(category_key: str) -> str:
    motions = _motions_for(category_key)
    return next(key for key, spec in motions.items() if spec.reveals)


def parse(raw: dict | None, category_key: str) -> Plan:
    """Build a Plan from client/director input, keeping only values we actually offer.

    Unknown values fall back to a default rather than raising — the same second line
    of defence composition.parse uses, because a stored job from an older vocabulary
    must still render. The one rule beyond "known value or default": if the piece was
    not clearly visible in the source still, the motion is forced to a reveals=True one
    for this category (unless the requested motion already reveals, in which case it is
    left alone).
    """
    raw = raw or {}
    motions = _motions_for(category_key)

    def pick(value, table, fallback):
        value = str(value or '').strip()
        return value if value in table else fallback

    motion = pick(raw.get('motion'), motions, next(iter(motions)))
    camera = pick(raw.get('camera'), CAMERAS, DEFAULT_CAMERA)
    mood = pick(raw.get('mood'), MOODS, DEFAULT_MOOD)
    pace = pick(raw.get('pace'), PACE, DEFAULT_PACE)
    piece_visible = pick(raw.get('piece_visible'),
                        {v: v for v in PIECE_VISIBLE}, DEFAULT_PIECE_VISIBLE)

    if piece_visible != 'clear' and not motions[motion].reveals:
        motion = _reveal_motion_key(category_key)

    action = (clean_action(raw.get('action'), category_key, motions[motion].prose)
             or motions[motion].prose)

    return Plan(motion=motion, camera=camera, mood=mood, pace=pace, action=action,
                piece_visible=piece_visible)


# --- rendering ---------------------------------------------------------------------------


def _sentence_case(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def _lower_first(text: str) -> str:
    return text[0].lower() + text[1:] if text else text


def _scene_label(location_key: str) -> str:
    """Just the place name, softly blurred behind her — never the full location
    paragraph. Used by the Seedance template's short scene clause."""
    place = locations.ALL.get(location_key)
    label = place.label if place else 'a softly lit studio'
    return f'{label}, softly blurred behind her.'


def _scene_clause_short(location_key: str) -> str:
    """One short clause, for Kling.

    Per fal's Kling 3 prompting guide, the input still is the anchor for an
    image-to-video prompt: the prompt describes how the scene EVOLVES from it, not
    what is already visible, so pasting a whole location paragraph (which describes
    what is already in frame 1) burns budget on facts the model already has as pixels.
    A short location label plus one generic light-change note keeps the location
    legible without repeating it.
    """
    place = locations.ALL.get(location_key)
    if place is None:
        return 'a softly lit studio, light shifting gently behind her'
    return f'{place.label}, light shifting softly behind her'


def _kling_prompt(plan: Plan, duration: int, description: str,
                  location_key: str) -> tuple[str, str]:
    """Kling image-to-video prompt.

    Order follows fal's Kling 3 prompting guide for image-to-video: a short subject
    anchor, then how the scene evolves over time (subject movement, then camera
    behaviour), then one short scene/light clause, then our own fidelity lock last.
    Simple words, short sentences, concrete detail — no "stunning"/"masterpiece"/"8K".
    The product description is carried verbatim, never paraphrased.
    """
    subject = f'The model, wearing {description}.' if description else 'The model.'
    motion_prose = _motion_prose(plan.motion)
    # A director call that failed entirely falls back to the motion's own prose as the
    # action too (see video.direct) — "First, X. Then, X." would just be noise, so
    # collapse to one beat rather than repeat it. Neither branch restates "the piece
    # faces the camera by the end" — FIDELITY_LOCK already says that once, and Kling's
    # per-model char budget is tight enough that saying it twice is pure waste.
    if plan.action.strip() == motion_prose.strip():
        movement = f'{_sentence_case(motion_prose)}.'
    else:
        movement = f'First, {motion_prose}. Then, {_lower_first(plan.action)}.'
    # Duration is already an explicit API argument (see video.py's build_args), so it
    # is not repeated here — only the pacing, which the API has no field for.
    camera = f'{_sentence_case(CAMERAS[plan.camera])}, {PACE[plan.pace]}.'
    scene = _scene_clause_short(location_key)
    prompt = f'{subject} {movement} {camera} {scene}. {FIDELITY_LOCK}'
    return prompt, NEGATIVE


# Seedance has no negative prompt, and portrait video is more prone to inventing
# subtitles than Kling — this is a fixed tail, not something the director model or a
# client note can influence.
SEEDANCE_CONSTRAINTS = (
    'Keep it subtitle-free: no text, logos or watermarks, no cuts, no duplicated '
    'people, no extra jewellery. Her face stays stable without deformation; motion '
    'is smooth, no flicker.'
)


def _seedance_prompt(plan: Plan, duration: int, description: str,
                     location_key: str) -> tuple[str, None]:
    """Seedance timed-stage prompt, per ByteDance's Seedance 2.0/2.5 prompt guide and
    fal's 2.5 guide: subject anchor -> timestamped action beats (one primary change
    each, body-part-specific, slow and continuous, the last beat settling on the piece
    facing camera) -> a short scene clause -> lighting/tone -> exactly ONE camera
    movement for the whole clip -> our fidelity lock plus a Seedance-specific
    constraints tail (no negative prompt exists here, so these have to be positive
    instructions instead)."""
    subject = f'The model, wearing {description}.' if description else 'The model.'
    motion_prose = _motion_prose(plan.motion)
    motion_beat = _sentence_case(motion_prose)
    settled = 'The piece rests facing the camera, centred and in focus.'
    # Same collapse as Kling: a director call that failed entirely (or an action
    # clean_action rejected) falls back to the motion's own prose as the action too —
    # restating that same sentence across two beats would just be noise, so the second
    # (and, for 10s, third) beat holds instead of repeating it.
    if plan.action.strip() == motion_prose.strip():
        if duration == 5:
            beats = [f'0-2s: {motion_beat}.',
                    f'2-5s: she holds as the piece settles. {settled}']
        else:
            beats = [
                f'0-3s: {motion_beat}.',
                '3-7s: she holds the pose, piece coming into focus.',
                f'7-10s: light settles on the piece. {settled}',
            ]
    else:
        action_beat = _sentence_case(plan.action)
        if duration == 5:
            beats = [f'0-2s: {motion_beat}.', f'2-5s: {action_beat}. {settled}']
        else:
            beats = [
                f'0-3s: {motion_beat}.',
                f'3-7s: {action_beat}.',
                f'7-10s: she holds steady as the light settles. {settled}',
            ]

    scene = _scene_label(location_key)
    lighting_tone = _sentence_case(MOODS[plan.mood]) + '.'
    # Exactly one camera movement, stated once for the whole clip — never per beat,
    # and never combined with a second movement (no push+pan+orbit together).
    camera = f'The camera does {CAMERAS[plan.camera]} throughout, {PACE[plan.pace]}.'

    prompt = (f'{subject} {" ".join(beats)} {scene} {lighting_tone} {camera} '
             f'{FIDELITY_LOCK} {SEEDANCE_CONSTRAINTS}')
    return prompt, None


def render(plan: Plan, model: str, duration: int, description: str,
          location_key: str) -> tuple[str, str | None]:
    """The final prompt (and negative prompt, Kling only) for one provider call."""
    if model not in MODELS:
        raise ValueError(f'unknown model {model!r}; use one of {MODELS}')
    if duration not in DURATIONS:
        raise ValueError(f'unknown duration {duration!r}; use one of {DURATIONS}')
    if model == 'kling':
        return _kling_prompt(plan, duration, description, location_key)
    return _seedance_prompt(plan, duration, description, location_key)


def demo() -> None:
    global FIDELITY_LOCK, IMPERATIVE_START_WORDS
    import product

    # Every category the app can shoot needs a curated motion list, and at least one
    # of them must reveal the piece — the whole reason this module exists.
    for key in product.CATEGORIES:
        assert key in MOTIONS and MOTIONS[key], f'{key} has no motions'
        assert any(spec.reveals for spec in MOTIONS[key].values()), \
            f'{key} has no revealing motion'

    # No empty prose anywhere.
    for table in (CAMERAS, MOODS, PACE):
        for key, text in table.items():
            assert text.strip(), f'{key} is empty'
    for category, table in MOTIONS.items():
        for key, spec in table.items():
            assert spec.prose.strip(), f'{category}/{key} is empty'

    # A 2026-09-27 spike showed 'rack-focus' read as a hard zoom and cropped her face
    # at the eyes — it must never come back, and every remaining camera must bound the
    # move to a chest-up frame with her whole face in view.
    assert 'rack-focus' not in CAMERAS
    for key, prose in CAMERAS.items():
        assert 'whole face in view' in prose, f'{key} does not bound the move'

    # The fidelity lock must say the face stays in frame — the other half of the fix,
    # since a bounded camera move is only half the story if the lock never asks for it.
    assert 'whole face stays in frame' in FIDELITY_LOCK

    # necklace's forced reveal must be the turn, not some other revealing motion.
    assert _reveal_motion_key('necklace') == 'turn-to-camera'

    # parse() must never trust the caller.
    junk = parse({'motion': 'levitate', 'camera': 'dolly-zoom', 'mood': 'gothic',
                 'pace': 'frantic', 'piece_visible': 'sparkling',
                 'action': 'DROP TABLE jobs'}, 'ring')
    assert junk.camera == DEFAULT_CAMERA and junk.mood == DEFAULT_MOOD
    assert junk.pace == DEFAULT_PACE and junk.piece_visible == DEFAULT_PIECE_VISIBLE
    assert junk.motion in MOTIONS['ring']

    # A motion from the wrong category is not a motion.
    assert parse({'motion': 'turn-to-camera'}, 'ring').motion != 'turn-to-camera'
    assert parse({'motion': 'turn-to-camera'}, 'necklace').motion == 'turn-to-camera'

    # --- the reveal rule --------------------------------------------------------------
    # A non-revealing motion, with the piece not clearly visible, must be forced to a
    # revealing one for THAT category.
    for category in product.CATEGORIES:
        non_reveal = next(key for key, spec in MOTIONS[category].items()
                          if not spec.reveals)
        for visibility in ('partial', 'hidden'):
            forced = parse({'motion': non_reveal, 'piece_visible': visibility}, category)
            assert MOTIONS[category][forced.motion].reveals, (category, visibility)
        # 'clear' must NOT force a change — the director saw the piece fine already.
        kept = parse({'motion': non_reveal, 'piece_visible': 'clear'}, category)
        assert kept.motion == non_reveal, category

    # A motion that already reveals is left alone, not swapped for a different one.
    for category in product.CATEGORIES:
        reveal_key = _reveal_motion_key(category)
        kept = parse({'motion': reveal_key, 'piece_visible': 'hidden'}, category)
        assert kept.motion == reveal_key, category

    # --- clean_action --------------------------------------------------------------
    good = clean_action('she turns her hand slowly, bringing the ring into full view',
                        'ring')
    assert good is not None
    assert clean_action('', 'ring') is None
    assert clean_action('   ', 'ring') is None
    assert clean_action(' '.join(['word'] * (MAX_ACTION_WORDS + 1)), 'ring') is None
    assert clean_action(' '.join(['word'] * MAX_ACTION_WORDS), 'ring') is not None
    for banned in BANNED_ACTION_WORDS:
        assert clean_action(f'she reveals a {banned} detail', 'ring') is None, banned
    for phrase in CONTRADICTION_PHRASES:
        assert clean_action(f'she does a {phrase} motion', 'ring') is None, phrase
    assert clean_action('she turns to profile and holds', 'earrings') is None
    assert clean_action('she turns to profile and holds', 'ring') is not None

    # An imperative action (an instruction TO her, not a description OF her) must be
    # rejected — a 2026-09-27 spike showed the director answering "gently lift chin..."
    # instead of third-person prose.
    turn_prose = MOTIONS['necklace']['turn-to-camera'].prose
    imperative_example = 'lift her chin and tilt the pendant toward the light'
    assert clean_action(imperative_example, 'necklace', turn_prose) is None, \
        'an imperative action should be rejected'
    print(f'GREEN (before corruption): imperative rejected -> '
         f'{clean_action(imperative_example, "necklace", turn_prose) is None}')

    original_imperatives = IMPERATIVE_START_WORDS
    IMPERATIVE_START_WORDS = frozenset()
    try:
        broken = clean_action(imperative_example, 'necklace', turn_prose)
        try:
            assert broken is None
        except AssertionError:
            print(f'RED (expected): imperative guard failed once the verb list is '
                 f'emptied -> accepted {broken!r}')
        else:
            raise AssertionError('emptying IMPERATIVE_START_WORDS should have let '
                                 'the imperative action through')
    finally:
        IMPERATIVE_START_WORDS = original_imperatives

    restored = clean_action(imperative_example, 'necklace', turn_prose)
    assert restored is None
    print(f'GREEN (after restore): imperative rejected -> {restored is None}')

    # A third-person action that just echoes the chosen motion's own prose back adds
    # nothing new and must be rejected too, so the template falls back to one beat
    # instead of "First X. Then, X again."
    echo_example = 'she turns toward the camera, bringing the pendant into the frame'
    assert clean_action(echo_example, 'necklace', turn_prose) is None, \
        'an action that mostly repeats the motion prose should be rejected'
    # A genuinely distinct action (what the light does on the piece) must still pass.
    assert clean_action('light glints across the pave as her chin lifts', 'necklace',
                        turn_prose) is not None

    # --- render(): both models x both durations x every category -------------------
    description = 'rose gold circular pendant necklace with diamond pave and chain'
    for category in product.CATEGORIES:
        plan = parse({}, category)
        for model in MODELS:
            for duration in DURATIONS:
                prompt, negative = render(plan, model, duration, description,
                                          'pondicherry')
                assert FIDELITY_LOCK in prompt, (category, model, duration)
                assert len(prompt) <= MAX_PROMPT_CHARS, (category, model, duration,
                                                         len(prompt))
                if model == 'kling':
                    assert negative == NEGATIVE, (category, duration)
                else:
                    assert negative is None, (category, duration)

    # Bad model/duration are rejected before any string work.
    plan = parse({}, 'ring')
    for bad_model in ('sora', ''):
        try:
            render(plan, bad_model, 5, description, 'pondicherry')
        except ValueError as error:
            assert bad_model in repr(error) or not bad_model
        else:
            raise AssertionError('unknown model should be rejected')
    try:
        render(plan, 'kling', 7, description, 'pondicherry')
    except ValueError as error:
        assert '7' in str(error)
    else:
        raise AssertionError('unknown duration should be rejected')

    # 10s Seedance is genuinely 3 stages, not 2 stages with a longer middle one.
    ten_s, _ = render(plan, 'seedance', 10, description, 'pondicherry')
    assert len(re.findall(r'\d+-\d+s:', ten_s)) == 3, ten_s
    five_s, _ = render(plan, 'seedance', 5, description, 'pondicherry')
    assert len(re.findall(r'\d+-\d+s:', five_s)) == 2, five_s

    # Seedance-specific amendment: subtitle-free constraints tail, and exactly ONE
    # camera movement for the whole clip (never one per beat, never two combined).
    for seedance_prompt in (five_s, ten_s):
        assert 'subtitle-free' in seedance_prompt, seedance_prompt
        assert len(re.findall(r'\bThe camera\b', seedance_prompt)) == 1, seedance_prompt

    # --- Kling prompting-guide amendments -------------------------------------------
    # A realistic plan: a director-supplied action distinct from the motion's own
    # prose (the common case — a total director failure, tested separately above via
    # the bare parse({}) plans, is the one degenerate case allowed to run longer).
    guide_plan = parse({'motion': 'turn-to-camera',
                        'action': 'light glints across the pave as she settles, chin '
                                 'lifted'}, 'necklace')
    kling_prompt, _ = render(guide_plan, 'kling', 5, description, 'pondicherry')
    place = locations.ALL['pondicherry']
    # Subject anchor comes before the scene clause (fal/Kling guide: subject, then how
    # the scene evolves, then a short scene note last).
    assert kling_prompt.index('The model, wearing') < kling_prompt.index(place.label)
    # The full location paragraph is never pasted in — only the short label + note.
    assert place.scene not in kling_prompt, kling_prompt
    assert len(kling_prompt) <= KLING_TARGET_CHARS, (len(kling_prompt), kling_prompt)

    # --- red-before-green: the lock-presence check must actually be able to fail ----
    plan = parse({}, 'necklace')
    good_prompt, _ = render(plan, 'kling', 5, description, 'pondicherry')
    assert FIDELITY_LOCK in good_prompt
    print(f'GREEN (before corruption): lock present -> {FIDELITY_LOCK in good_prompt}')

    original_lock = FIDELITY_LOCK
    FIDELITY_LOCK = 'CORRUPTED-LOCK-MARKER-NOT-THE-REAL-ONE'
    try:
        broken_prompt, _ = render(plan, 'kling', 5, description, 'pondicherry')
        try:
            assert original_lock in broken_prompt
        except AssertionError:
            print(f'RED (expected): lock-presence check failed with a corrupted '
                 f'lock -> {original_lock in broken_prompt}')
        else:
            raise AssertionError('corrupting FIDELITY_LOCK should have failed the '
                                 'lock-presence check')
    finally:
        FIDELITY_LOCK = original_lock

    restored_prompt, _ = render(plan, 'kling', 5, description, 'pondicherry')
    assert original_lock in restored_prompt
    print(f'GREEN (after restore): lock present -> {original_lock in restored_prompt}')

    print('motion ok')


if __name__ == '__main__':
    demo()
