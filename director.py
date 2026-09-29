"""The creative director: brief -> 3 concepts -> a validated storyboard.

Two Anthropic calls, both forced into our own vocabulary by a JSON schema (the same
pattern as video.py's _ask_director and app.py's _ask_for_composition): generate_concepts
gives the jeweller 3 directions to choose from, generate_storyboard turns the chosen one
into 7-9 shots plus an end card. No provider imports, no database — ads_api.py runs this
inside a job and persists the result through storyboard.py.

The validator (validate_storyboard) enforces the "merged Director rules" measured off two
real reference ads (see the plan's Phase 0 findings): shot counts, durations, where the
product has to be visible and for how long, transitions, and the one standout camera move.
Every rule is numbered once, in RULES below, so an error message and this file's own demo()
both point at the same definition.

    .venv/bin/python director.py     # validator self-check, no network
"""

import json
import time

MODEL = 'claude-sonnet-5-5'        # creative work; fidelity checks stay on Haiku (video.py)

# Expected output size, in characters of streamed JSON text, used only to turn "how much
# has the model written" into a progress fraction (never a promise of exact size).
# Concepts: 3 short concepts x ~6 fields, measured against real replies. Storyboard:
# measured off two real saved boards (board_story.json/board_showcase.json, ~11.0-11.5KB
# each) — see the plan's Phase 0 findings for the sample source.
EXPECTED_CONCEPTS_CHARS = 1400
EXPECTED_STORYBOARD_CHARS = 11000

# A soft-only retry (see generate_storyboard) is only worth the customer's extra wait
# while the job is still young — past this, waiting through a second full model call for
# a style nit costs more than accepting the board with a visible warning.
SOFT_RETRY_BUDGET_S = 90

VALID_MODES = ('story', 'showcase')

SHOT_TYPES = ('wide', 'medium', 'close', 'macro', 'insert')
CAMERA_MOVES = ('static', 'rack_focus', 'slow_push', 'slow_pull', 'orbit', 'pan', 'drift',
                'crane_rise')
PRODUCT_VISIBILITY = ('none', 'small', 'medium', 'hero')
MOTION_INTENSITY = ('low', 'medium', 'high')
TRANSITIONS = ('cut', 'dissolve')

# --- rule constants, numbered once so an error string and demo() share one definition ---
SHOT_DURATION_RANGE = (1, 6)              # rule 3
LONG_SHOT_RANGE = (3, 6)                  # rule 5
END_CARD_FRACTION_RANGE = (0.12, 0.18)    # rule 4
DURATION_TOLERANCE_S = 1                  # rule 2
FIRST_PRODUCT_WINDOW_S = 5                # rule 7
MAX_DISSOLVES = 1                         # rule 8
STANDOUT_MOVES = {'crane_rise', 'orbit'}
MAX_STANDOUT_MOVES = 1                    # rule 10
# story = product clearly visible 15-25% of runtime, one standout move late; showcase =
# 35-45%, a macro-insert montage of multiple pieces (plan's "two reference ads" section).
# Both bands were measured off a SINGLE reference ad with one product on screen — a real
# campaign showing N pieces (a necklace + earrings, a full set) legitimately keeps more of
# them in frame at once, so the upper bound rises with the product count rather than
# hard-failing a multi-piece campaign against a one-piece reference.
MODE_PRODUCT_BAND = {'story': (0.15, 0.25), 'showcase': (0.35, 0.45)}
PRODUCT_BAND_HEADROOM_PER_EXTRA_PRODUCT = 0.10       # +10pp per product beyond the first
PRODUCT_BAND_MAX_UPPER = 0.60


def product_band(mode: str, product_count: int) -> tuple[float, float]:
    """(lo, hi) for rule 9, scaled by how many distinct products the campaign has. The
    lower bound never moves — more products showing MORE isn't a floor concern — only the
    upper bound rises, capped at PRODUCT_BAND_MAX_UPPER."""
    lo, hi = MODE_PRODUCT_BAND.get(mode, MODE_PRODUCT_BAND['story'])
    extra = max(0, product_count - 1)
    hi = min(PRODUCT_BAND_MAX_UPPER, hi + PRODUCT_BAND_HEADROOM_PER_EXTRA_PRODUCT * extra)
    return lo, hi

# severity: 'hard' = structural — a board that still breaks one of these after the one
# retry is unusable (wrong length, credits paid for the wrong number of shots, an id that
# doesn't exist) and raises DirectorError. 'soft' = style guidance measured off two single
# reference ads — worth a retry attempt but never worth failing a job the customer waited
# 1-3 minutes for; see generate_storyboard's accept-with-warnings path.
RULES = {
    1: {'severity': 'hard', 'text':
        'the right number of shots (scaled for the target duration) plus exactly one '
        'end card, last'},
    2: {'severity': 'hard', 'text':
        'shot durations (incl. the end card) sum to the target duration within '
        f'{DURATION_TOLERANCE_S}s'},
    3: {'severity': 'hard', 'text':
        f'each ordinary shot is {SHOT_DURATION_RANGE[0]}-{SHOT_DURATION_RANGE[1]}s'},
    4: {'severity': 'hard', 'text': 'the end card is 12-18% of the total runtime'},
    5: {'severity': 'soft', 'text':
        f'one shot {LONG_SHOT_RANGE[0]}-{LONG_SHOT_RANGE[1]}s long, past the midpoint '
        'of the runtime'},
    6: {'severity': 'soft', 'text': 'the first shot is a medium lifestyle shot'},
    7: {'severity': 'hard', 'text':
        f'a hero product macro/insert appears in the first {FIRST_PRODUCT_WINDOW_S}s'},
    8: {'severity': 'hard', 'text':
        f'transitions are cut, with at most {MAX_DISSOLVES} dissolve'},
    9: {'severity': 'soft', 'text': "the product-visible share matches the concept's mode"},
    10: {'severity': 'soft', 'text': 'at most one standout camera move (crane_rise/orbit)'},
    11: {'severity': 'hard', 'text': 'every character_id/product_id is one of the ones given'},
    # Not named by either list in the brief — treated as hard: it's a technical/format
    # constraint on the SAME footing as rule 2 (durations summing correctly), not a style
    # judgement, so a board that fails it is malformed rather than merely off-style.
    12: {'severity': 'hard', 'text': 'every duration is a multiple of 0.5s'},
}


class DirectorError(Exception):
    """The storyboard still broke the rules after one retry. .errors is the list."""

    def __init__(self, errors: list[str]):
        super().__init__('; '.join(errors))
        self.errors = errors


def _reply_json(reply) -> dict:
    """Parse a structured-output reply's JSON payload, or raise DirectorError for
    anything short of a clean, complete text reply. claude-sonnet-5-5 runs adaptive
    thinking whenever `effort` is left unset, and thinking tokens count against
    max_tokens — so a reply can come back truncated mid-JSON, refused outright, or with
    no text block at all. All three have happened for real; generate_concepts and
    generate_storyboard retry once on any DirectorError this raises, same as a
    validate_storyboard failure."""
    if reply.stop_reason == 'max_tokens':
        raise DirectorError(['model output was truncated (max_tokens)'])
    if reply.stop_reason == 'refusal':
        details = getattr(reply, 'stop_details', None)
        category = getattr(details, 'category', None) if details else None
        explanation = getattr(details, 'explanation', None) if details else None
        extra = f' ({category}: {explanation})' if category or explanation else ''
        raise DirectorError([f'model refused the request{extra}'])
    text = ''.join(block.text for block in reply.content if block.type == 'text')
    if not text:
        raise DirectorError([f'no text in model reply (stop_reason={reply.stop_reason!r})'])
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise DirectorError([f'model reply was not valid JSON ({e}): {text[:200]!r}']) from e


def _stream_json_reply(stream_ctx, expected_chars: int, on_progress=None,
                       writing_message: str = 'writing…') -> dict:
    """Run one `client.messages.stream(...)` call, reporting real progress through
    on_progress(stage, fraction, message) as content actually streams in, then return
    the parsed JSON payload (via _reply_json — still raises DirectorError for a
    truncated/refused/malformed reply).

    Stages: "thinking" (indeterminate — only fires if the model actually emits thinking
    deltas; fraction stays None, the UI shows an animated bar, not a number) and
    "writing" (fraction = characters streamed so far / expected_chars, capped at 0.95 —
    the true 100% only happens once the caller has actually persisted the result, which
    is after this function returns).
    """
    chars = 0
    with stream_ctx as stream:
        for event in stream:
            if event.type != 'content_block_delta':
                continue
            delta_type = getattr(event.delta, 'type', None)
            if delta_type == 'thinking_delta':
                if on_progress:
                    on_progress('thinking', None, 'thinking it through…')
            elif delta_type == 'text_delta':
                chars += len(event.delta.text)
                if on_progress:
                    fraction = min(0.95, chars / expected_chars) if expected_chars else None
                    on_progress('writing', fraction, writing_message)
        reply = stream.get_final_message()
    return _reply_json(reply)


def expected_shot_range(target_duration: float) -> tuple[int, int]:
    """(min, max) ordinary shots (the end card is separate) for a target duration.

    7-9 is the reference ads' own count, for their own 20-30s runtime. Outside that
    window the ad is built from roughly the same ~2.6s median shot (the two references'
    own average), scaled by how much of the runtime isn't the end card (~85%), with a
    +/-1 shot tolerance so the validator isn't brittle to rounding.
    """
    if 20 <= target_duration <= 30:
        return (7, 9)
    body = target_duration * (1 - sum(END_CARD_FRACTION_RANGE) / 2)
    expected = round(body / 2.6)
    return (max(3, expected - 1), max(4, expected + 1))


# --- JSON schemas: force the model into our own vocabulary, never free text for an enum ---

CONCEPT_ITEM_SCHEMA = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string'},
        'core_idea': {'type': 'string'},
        'emotional_hook': {'type': 'string'},
        'visual_world': {'type': 'string'},
        'story_arc': {'type': 'string'},
        'tagline': {'type': 'string'},
        'mode': {'type': 'string', 'enum': list(VALID_MODES)},
    },
    'required': ['name', 'core_idea', 'emotional_hook', 'visual_world', 'story_arc',
                 'tagline', 'mode'],
    'additionalProperties': False,
}

CONCEPTS_SCHEMA = {
    'type': 'object',
    # No minItems/maxItems here (the API only allows array minItems of 0 or 1, never
    # maxItems) — generate_concepts checks the count is exactly 3 itself.
    'properties': {'concepts': {'type': 'array', 'items': CONCEPT_ITEM_SCHEMA}},
    'required': ['concepts'],
    'additionalProperties': False,
}

# Anthropic's structured-output grammar caps *optional* object properties at 24 across
# the whole schema (see _assert_schema_supported) — so every property here is required,
# and a field that doesn't apply to a given shot is filled with "" (plain text) or null
# (the enums below, via anyOf) rather than omitted.
_OPTIONAL_TEXT = {'type': 'string',
                  'description': 'Empty string if this does not apply to the shot.'}


def _optional_enum(values: tuple) -> dict:
    """An enum field that may not apply to a shot (e.g. shot_type on the end card) — the
    model sends null instead of a value; "" isn't a valid member of `values`."""
    return {'anyOf': [{'type': 'string', 'enum': list(values)}, {'type': 'null'}]}


# One object covers both a real shot and the end card: the end card only ever fills
# brand_text/tagline, and letting the rest sit empty is simpler than a oneOf keyed on
# `kind` inside a JSON-schema structured-output call.
SHOT_SPEC_SCHEMA = {
    'type': 'object',
    'properties': {
        'purpose': _OPTIONAL_TEXT,
        'scene_description': _OPTIONAL_TEXT,
        'emotional_beat': _OPTIONAL_TEXT,
        'character_action': _OPTIONAL_TEXT,
        'facial_expression': _OPTIONAL_TEXT,
        'product_interaction': _OPTIONAL_TEXT,
        'product_visibility': _optional_enum(PRODUCT_VISIBILITY),
        'environment': _OPTIONAL_TEXT,
        'shot_type': _optional_enum(SHOT_TYPES),
        'camera_angle': _OPTIONAL_TEXT,
        'camera_move': _optional_enum(CAMERA_MOVES),
        'lens': _OPTIONAL_TEXT,
        'depth_of_field': _OPTIONAL_TEXT,
        'lighting': _OPTIONAL_TEXT,
        'time_of_day': _OPTIONAL_TEXT,
        'wardrobe': _OPTIONAL_TEXT,
        'props': _OPTIONAL_TEXT,
        'motion_intensity': _optional_enum(MOTION_INTENSITY),
        'dialogue': _OPTIONAL_TEXT,
        'voiceover': _OPTIONAL_TEXT,
        'ambient_sound': _OPTIONAL_TEXT,
        'sound_effects': _OPTIONAL_TEXT,
        'music_cue': _OPTIONAL_TEXT,
        'on_screen_text': _OPTIONAL_TEXT,
        'text_placement': _OPTIONAL_TEXT,
        'transition_out': _optional_enum(TRANSITIONS),
        'brand_text': _OPTIONAL_TEXT,
        'tagline': _OPTIONAL_TEXT,
    },
    'required': [
        'purpose', 'scene_description', 'emotional_beat', 'character_action',
        'facial_expression', 'product_interaction', 'product_visibility', 'environment',
        'shot_type', 'camera_angle', 'camera_move', 'lens', 'depth_of_field', 'lighting',
        'time_of_day', 'wardrobe', 'props', 'motion_intensity', 'dialogue', 'voiceover',
        'ambient_sound', 'sound_effects', 'music_cue', 'on_screen_text', 'text_placement',
        'transition_out', 'brand_text', 'tagline',
    ],
    'additionalProperties': False,
}

SHOT_SCHEMA = {
    'type': 'object',
    'properties': {
        'kind': {'type': 'string', 'enum': ['shot', 'end_card']},
        'duration': {'type': 'number'},
        'character_ids': {'type': 'array', 'items': {'type': 'string'}},
        'product_ids': {'type': 'array', 'items': {'type': 'string'}},
        'spec': SHOT_SPEC_SCHEMA,
    },
    'required': ['kind', 'duration', 'character_ids', 'product_ids', 'spec'],
    'additionalProperties': False,
}

STORYBOARD_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'visual_style': {'type': 'string'},
        'palette': {'type': 'string'},
        'emotional_arc': {'type': 'string'},
        'music_direction': {'type': 'string'},
        # No minItems here for the same reason as CONCEPTS_SCHEMA — validate_storyboard's
        # rule 1 (expected_shot_range) already enforces the real minimum in code.
        'shots': {'type': 'array', 'items': SHOT_SCHEMA},
    },
    'required': ['title', 'visual_style', 'palette', 'emotional_arc', 'music_direction',
                 'shots'],
    'additionalProperties': False,
}

# Stated in both system prompts. Never optional: a generated ad that echoes a real
# competitor's campaign is a legal problem, and one that redraws the jewellery is a
# fidelity problem the customer paid to avoid.
_HOUSE_RULES = (
    'Never copy or closely imitate the creative of a real advertising campaign you '
    'recognise (a specific brand\'s ad, tagline or shot sequence) — invent an original '
    'idea for THIS brief. The jewellery itself must read as exactly the piece described '
    '(and, where a reference photo exists, exactly as photographed) in every shot it '
    'appears in — never redesigned, recoloured or restyled by the story.'
)


def _brief_text(brief: dict) -> str:
    fields = [
        ('Brand', brief.get('brand')), ('Campaign goal', brief.get('goal')),
        ('Audience', brief.get('audience')), ('Platform', brief.get('platform')),
        ('Duration', f"{brief.get('duration')}s" if brief.get('duration') else None),
        ('Aspect', brief.get('aspect')), ('Mood', brief.get('mood')),
    ]
    return '\n'.join(f'{label}: {value}' for label, value in fields if value)


def _products_text(products: list[dict]) -> str:
    if not products:
        return 'No products given yet — invent nothing; the storyboard step requires them.'

    def label(p):
        sku, description = p.get('sku'), p.get('description')
        if sku and description:                        # both known -- give the director both
            return f'{sku} ({description})'
        return sku or description or p.get('name') or p.get('category', 'a piece')

    return '\n'.join(
        f"- id={p['id']}: {label(p)}"
        f"{' — ' + p['fidelity_instructions'] if p.get('fidelity_instructions') else ''}"
        for p in products)


def _characters_text(characters: list[dict]) -> str:
    if not characters:
        return 'No characters cast yet — the storyboard may describe someone generically.'
    return '\n'.join(f"- id={c['id']}: {c.get('name', 'unnamed')} — "
                     f"{c.get('description', '')}" for c in characters)


def _concepts_system_prompt() -> str:
    return (
        'You are a senior advertising creative director for a jewellery brand, pitching '
        'exactly 3 distinct concepts for a short video ad. Each concept declares a mode: '
        "'story' (the product is clearly visible for 15-25% of the runtime, with one "
        "standout camera move late in the ad) or 'showcase' (35-45%, built around a "
        'macro-insert montage of the pieces). Vary the 3 concepts in mode and emotional '
        f'register, not just in wording. {_HOUSE_RULES}'
    )


def generate_concepts(brief: dict, products: list[dict], characters: list[dict],
                      on_progress=None) -> list[dict]:
    """3 concepts (name, core_idea, emotional_hook, visual_world, story_arc, tagline,
    mode), forced into our schema so mode is always one of VALID_MODES. One retry —
    on a wrong count, or on anything _reply_json rejects (truncated/refused/malformed,
    see its docstring) — before raising DirectorError.

    on_progress(stage, fraction, message), if given, is called through the stream (see
    _stream_json_reply) plus at "checking" (after the count is verified) and, on a
    retry, "retrying — <why>" with the fraction reset to 0 for the second pass.
    """
    import anthropic

    user = (f'Brief:\n{_brief_text(brief)}\n\nProducts:\n{_products_text(products)}\n\n'
            f'Characters:\n{_characters_text(characters)}\n\n'
            'Propose 3 concepts for this ad.')
    client = anthropic.Anthropic()

    def ask(u: str) -> list[dict]:
        stream_ctx = client.messages.stream(
            model=MODEL, max_tokens=16000, system=_concepts_system_prompt(),
            messages=[{'role': 'user', 'content': u}],
            output_config={'format': {'type': 'json_schema', 'schema': CONCEPTS_SCHEMA},
                          'effort': 'medium'})
        payload = _stream_json_reply(stream_ctx, EXPECTED_CONCEPTS_CHARS, on_progress,
                                     'writing 3 concepts…')
        return payload['concepts']

    def attempt(u: str) -> tuple[list[dict] | None, str | None]:
        try:
            concepts = ask(u)
        except DirectorError as e:
            return None, str(e)
        if len(concepts) != 3:
            return concepts, f'returned {len(concepts)} concepts, expected exactly 3'
        return concepts, None

    concepts, error = attempt(user)
    if on_progress:
        on_progress('checking', 0.95, 'checking the concepts…')
    if error:
        if on_progress:
            on_progress(f'retrying — the first draft {error}', 0.0, 'trying again…')
        retry_user = (f'{user}\n\nYour previous reply failed: {error}. Return exactly '
                      '3 concepts as valid JSON matching the schema.')
        concepts, error = attempt(retry_user)
        if error:
            raise DirectorError([f'generate_concepts failed after retry: {error}'])
    return concepts


def _storyboard_system_prompt(mode: str, product_count: int = 1) -> str:
    lo, hi = product_band(mode, product_count)
    return (
        'You are a senior advertising creative director turning one chosen concept into '
        'a shot-by-shot storyboard for a short jewellery video ad. Follow these rules '
        'exactly:\n'
        f'1. {expected_shot_range(20)[0]}-{expected_shot_range(20)[1]} shots for a '
        '20-30s ad (scale proportionally outside that range), plus exactly one end_card '
        'shot, last.\n'
        f'2. Shot durations (incl. the end card) must sum to the target duration within '
        f'{DURATION_TOLERANCE_S}s, and every duration is a multiple of 0.5s.\n'
        f'3. Each ordinary shot is {SHOT_DURATION_RANGE[0]}-{SHOT_DURATION_RANGE[1]}s.\n'
        f'4. The end card is 12-18% of the runtime: freeze the last frame, with the '
        'brand and a tagline as one centred block (spec.brand_text/spec.tagline).\n'
        f'5. Include one longer {LONG_SHOT_RANGE[0]}-{LONG_SHOT_RANGE[1]}s take past the '
        'midpoint of the runtime.\n'
        '6. Open on a medium lifestyle shot (shot_type=medium).\n'
        f'7. Put a hero product macro or insert shot (shot_type in macro/insert, '
        f'product_visibility=hero) within the first {FIRST_PRODUCT_WINDOW_S}s.\n'
        f'8. transition_out is cut everywhere, except at most {MAX_DISSOLVES} dissolve.\n'
        f'9. The product must be clearly visible (product_visibility medium or hero) for '
        f'{int(lo * 100)}-{int(hi * 100)}% of the total runtime — this concept is '
        f"'{mode}' mode.\n"
        '10. Mostly subtle camera movement; at most one standout move '
        '(camera_move=crane_rise or orbit) anywhere from the midpoint to just before the '
        'end card.\n'
        '11. Only use the character_ids and product_ids given to you.\n'
        'Every spec field is required by the schema, but not every field applies to '
        'every shot: leave text fields (dialogue, voiceover, on_screen_text, etc.) as '
        '"" and shot_type/camera_move/product_visibility/motion_intensity/'
        "transition_out as null wherever they don't apply (e.g. most of them, on the "
        f'end card). One palette/grade for the whole ad, stated once (palette). '
        f'{_HOUSE_RULES}'
    )


def _normalise_spec(spec: dict) -> dict:
    """Drop the ""/null filler the schema now forces the model to send for a field
    that doesn't apply to this shot — everywhere else in this codebase (validate_
    storyboard, storyboard.py's stored jsonb, the editor UI) an absent key and an
    empty one mean the same thing, so store only the ones that actually say something."""
    return {k: v for k, v in spec.items() if v not in ('', None)}


def _ask_storyboard(system: str, user: str, on_progress=None) -> dict:
    import anthropic

    stream_ctx = anthropic.Anthropic().messages.stream(
        model=MODEL, max_tokens=64000, system=system,
        messages=[{'role': 'user', 'content': user}],
        output_config={'format': {'type': 'json_schema', 'schema': STORYBOARD_SCHEMA},
                      'effort': 'medium'})
    board = _stream_json_reply(stream_ctx, EXPECTED_STORYBOARD_CHARS, on_progress,
                               'writing the storyboard…')
    for s in board.get('shots') or []:
        s['spec'] = _normalise_spec(s.get('spec') or {})
    return board


def _reply_error(message: str) -> dict:
    """A _reply_json/_ask_storyboard failure (truncated/refused/malformed) wrapped as a
    HARD error, in the same {'rule', 'severity', 'message'} shape validate_storyboard
    returns — there is no partial board to check style rules against, so it can only
    ever be hard."""
    return {'rule': None, 'severity': 'hard', 'message': message}


def generate_storyboard(brief: dict, concept: dict, products: list[dict],
                        characters: list[dict], on_progress=None) -> tuple[dict, list[dict]]:
    """A storyboard for `concept`, plus any SOFT (style) warnings still open on it —
    returns (board, warnings).

    HARD errors (structural: wrong shot count/timing, an unknown id, more than one
    dissolve, no early hero shot, a malformed reply) get one retry — sent back with any
    SOFT errors too, so the retry can fix style at the same time — then raise
    DirectorError if hard errors remain: the board is unusable, not merely off-style.

    SOFT-only errors (style guidance measured off single reference ads, e.g. the
    product-visible-share band — see RULES) never fail the job. If the first attempt has
    soft-only errors, retry once more, but only while the call is still young
    (< SOFT_RETRY_BUDGET_S elapsed) — past that, a customer who already waited gets the
    board back with a warning rather than a longer wait for a style nit. Either way the
    board is ACCEPTED with `warnings` set to whatever soft errors are still open.
    """
    mode = concept.get('mode') if concept.get('mode') in VALID_MODES else 'story'
    target_duration = float(brief.get('duration') or brief.get('target_duration') or 25)
    product_ids = [str(p['id']) for p in products]
    character_ids = [str(c['id']) for c in characters]

    system = _storyboard_system_prompt(mode, len(product_ids))
    user = (
        f'Brief:\n{_brief_text(brief)}\n\nChosen concept: {concept.get("name", "")}\n'
        f'Core idea: {concept.get("core_idea", "")}\nEmotional hook: '
        f'{concept.get("emotional_hook", "")}\nVisual world: '
        f'{concept.get("visual_world", "")}\nStory arc: {concept.get("story_arc", "")}\n'
        f'Tagline: {concept.get("tagline", "")}\n\nTarget duration: {target_duration}s\n\n'
        f'Products (use these ids only):\n{_products_text(products)}\n\n'
        f'Characters (use these ids only):\n{_characters_text(characters)}\n\n'
        'Build the storyboard.'
    )

    def attempt(prompt_user: str) -> tuple[dict | None, list[dict]]:
        try:
            board = _ask_storyboard(system, prompt_user, on_progress)
        except DirectorError as e:
            return None, [_reply_error(msg) for msg in e.errors]
        if on_progress:
            on_progress('checking rules', 0.95, 'checking the storyboard against the rules…')
        return board, validate_storyboard(board, brief, mode, product_ids, character_ids)

    def retry(reason: str, feedback: list[dict]) -> tuple[dict | None, list[dict]]:
        if on_progress:
            on_progress(f'retrying — {reason}', 0.0, 'building a corrected storyboard…')
        retry_user = (f'{user}\n\nYour previous storyboard broke these rules:\n'
                      + '\n'.join(f"- {e['message']}" for e in feedback)
                      + '\n\nFix ALL of them and return a complete corrected storyboard.')
        return attempt(retry_user)

    started = time.monotonic()
    board, errors = attempt(user)
    hard, soft = hard_errors(errors), soft_errors(errors)

    if hard:
        reason = f"the first draft broke {len(hard)} rule{'s' if len(hard) != 1 else ''}"
        board, errors = retry(reason, hard + soft)
        hard, soft = hard_errors(errors), soft_errors(errors)
        if hard:
            raise DirectorError(error_messages(errors))
        return board, soft

    if soft and (time.monotonic() - started) < SOFT_RETRY_BUDGET_S:
        reason = (f"the first draft broke {len(soft)} style rule"
                  f"{'s' if len(soft) != 1 else ''}")
        board, errors = retry(reason, soft)
        hard, soft = hard_errors(errors), soft_errors(errors)
        if hard:              # the retry itself introduced a structural break
            raise DirectorError(error_messages(errors))
        return board, soft

    return board, soft


_SCHEMA_ALWAYS_UNSUPPORTED = ('maxItems', 'uniqueItems', 'minLength', 'maxLength',
                              'pattern', 'minimum', 'maximum', 'exclusiveMinimum',
                              'exclusiveMaximum', 'multipleOf')

# Anthropic's own limit, hit for real once already (28 optional properties on
# SHOT_SPEC_SCHEMA -> 400 "too many optional parameters"). Checked generically here;
# _assert_all_required below is this codebase's own, stricter policy (zero optional).
MAX_OPTIONAL_PROPERTIES = 24


def _count_optional_properties(schema) -> int:
    """Sum, over every object anywhere in `schema`, the properties not in that object's
    own `required` list."""
    if isinstance(schema, list):
        return sum(_count_optional_properties(s) for s in schema)
    if not isinstance(schema, dict):
        return 0
    total = 0
    if schema.get('type') == 'object':
        properties = schema.get('properties') or {}
        required = set(schema.get('required') or [])
        total += sum(1 for p in properties if p not in required)
        total += sum(_count_optional_properties(sub) for sub in properties.values())
    if 'items' in schema:
        total += _count_optional_properties(schema['items'])
    for key in ('anyOf', 'allOf'):
        if key in schema:
            total += _count_optional_properties(schema[key])
    return total


def _assert_schema_supported(schema, path: str = '$') -> None:
    """Raise ValueError naming the path to the first keyword Anthropic's structured
    output doesn't support (see director.py's module docstring / the plan): any of
    _SCHEMA_ALWAYS_UNSUPPORTED, minItems other than 0 or 1, more than
    MAX_OPTIONAL_PROPERTIES optional properties across the whole schema, or an object
    missing additionalProperties: False. Doesn't chase $ref/$defs or recursive schemas
    — this module doesn't use them."""
    if path == '$':
        total = _count_optional_properties(schema)
        if total > MAX_OPTIONAL_PROPERTIES:
            raise ValueError(f'{path}: {total} optional properties across the schema, '
                             f'the API allows at most {MAX_OPTIONAL_PROPERTIES}')

    if isinstance(schema, list):
        for i, sub in enumerate(schema):
            _assert_schema_supported(sub, f'{path}[{i}]')
        return
    if not isinstance(schema, dict):
        return

    for key in _SCHEMA_ALWAYS_UNSUPPORTED:
        if key in schema:
            raise ValueError(f'{path}: unsupported keyword {key!r}')
    if 'minItems' in schema and schema['minItems'] not in (0, 1):
        raise ValueError(f'{path}: unsupported minItems={schema["minItems"]!r} '
                         '(only 0 or 1 is supported)')
    if schema.get('type') == 'object':
        if schema.get('additionalProperties') is not False:
            raise ValueError(f'{path}: object missing additionalProperties: False')
        for prop, sub in (schema.get('properties') or {}).items():
            _assert_schema_supported(sub, f'{path}.{prop}')
    if 'items' in schema:
        _assert_schema_supported(schema['items'], f'{path}[]')
    for key in ('anyOf', 'allOf'):
        if key in schema:
            _assert_schema_supported(schema[key], f'{path}.{key}')


def _assert_all_required(schema, path: str = '$') -> None:
    """This codebase's own stricter policy, on top of _assert_schema_supported: every
    schema we send must have ZERO optional properties, not just <=24 — the 28-optional-
    properties 400 is exactly what an "it's under the limit for now" schema risks the
    next time a field is added. Raise ValueError naming the first object with any
    property missing from its own `required` list."""
    if isinstance(schema, list):
        for i, sub in enumerate(schema):
            _assert_all_required(sub, f'{path}[{i}]')
        return
    if not isinstance(schema, dict):
        return

    if schema.get('type') == 'object':
        properties = schema.get('properties') or {}
        required = set(schema.get('required') or [])
        optional = [p for p in properties if p not in required]
        if optional:
            raise ValueError(f'{path}: optional properties not allowed: {optional}')
        for prop, sub in properties.items():
            _assert_all_required(sub, f'{path}.{prop}')
    if 'items' in schema:
        _assert_all_required(schema['items'], f'{path}[]')
    for key in ('anyOf', 'allOf'):
        if key in schema:
            _assert_all_required(schema[key], f'{path}.{key}')


def _is_multiple_of_half(value: float) -> bool:
    return abs(round(value * 2) - value * 2) < 1e-6


def hard_errors(errors: list[dict]) -> list[dict]:
    return [e for e in errors if e['severity'] == 'hard']


def soft_errors(errors: list[dict]) -> list[dict]:
    return [e for e in errors if e['severity'] == 'soft']


def error_messages(errors: list[dict]) -> list[str]:
    return [e['message'] for e in errors]


def validate_storyboard(board: dict, brief: dict, mode: str, product_ids: list[str],
                        character_ids: list[str] | None = None) -> list[dict]:
    """Every broken rule from RULES, as `{'rule', 'severity', 'message'}` dicts — HARD
    (structural: wrong length, an id that doesn't exist, malformed timing) or SOFT (style
    guidance measured off single reference ads). Empty list means the board is clean;
    hard_errors()/soft_errors()/error_messages() are the usual ways to read the result —
    see generate_storyboard for how each severity is handled. `character_ids=None` skips
    rule 11's character half (used when the caller has not resolved them, e.g. a
    from-scratch character)."""
    errors: list[dict] = []
    target_duration = float(brief.get('duration') or brief.get('target_duration') or 25)
    shots = board.get('shots') or []

    def fail(n: int, detail: str) -> None:
        rule = RULES[n]
        errors.append({'rule': n, 'severity': rule['severity'],
                       'message': f'[{n}] {rule["text"]}: {detail}'})

    real_shots = [s for s in shots if s.get('kind') == 'shot']
    end_cards = [s for s in shots if s.get('kind') == 'end_card']

    # --- rule 1: shot count + exactly one end card, last -----------------------------
    lo, hi = expected_shot_range(target_duration)
    if not (lo <= len(real_shots) <= hi):
        fail(1, f'{len(real_shots)} shots, expected {lo}-{hi}')
    if len(end_cards) != 1:
        fail(1, f'{len(end_cards)} end_card shots, expected exactly 1')
    elif shots[-1].get('kind') != 'end_card':
        fail(1, 'the end_card is not the last shot')

    if not shots:
        return errors          # nothing further can be checked against an empty board

    # --- rule 2: total duration within tolerance, rule 12: halves only ---------------
    total = sum(float(s.get('duration') or 0) for s in shots)
    if abs(total - target_duration) > DURATION_TOLERANCE_S:
        fail(2, f'durations sum to {total}s, target is {target_duration}s')
    for i, s in enumerate(shots):
        if not _is_multiple_of_half(float(s.get('duration') or 0)):
            fail(12, f'shot {i} duration {s.get("duration")} is not a multiple of 0.5s')

    # --- rule 3: ordinary shot length ------------------------------------------------
    for i, s in enumerate(real_shots):
        d = float(s.get('duration') or 0)
        if not (SHOT_DURATION_RANGE[0] <= d <= SHOT_DURATION_RANGE[1]):
            fail(3, f'shot {i} is {d}s, expected {SHOT_DURATION_RANGE[0]}-'
                    f'{SHOT_DURATION_RANGE[1]}s')

    # --- rule 4: end card fraction ----------------------------------------------------
    if end_cards and total > 0:
        fraction = float(end_cards[-1].get('duration') or 0) / total
        if not (END_CARD_FRACTION_RANGE[0] <= fraction <= END_CARD_FRACTION_RANGE[1]):
            fail(4, f'the end card is {fraction:.0%} of the runtime, expected '
                    f'{END_CARD_FRACTION_RANGE[0]:.0%}-{END_CARD_FRACTION_RANGE[1]:.0%}')

    # --- start times (derived, mirrors storyboard.get_version's SQL window) ----------
    start_times: list[float] = []
    running = 0.0
    for s in shots:
        start_times.append(running)
        running += float(s.get('duration') or 0)
    midpoint = total / 2

    # --- rule 5: one long take past the midpoint --------------------------------------
    has_long_take_past_mid = any(
        LONG_SHOT_RANGE[0] <= float(s.get('duration') or 0) <= LONG_SHOT_RANGE[1]
        and start_times[i] >= midpoint
        for i, s in enumerate(shots) if s.get('kind') == 'shot')
    if not has_long_take_past_mid:
        fail(5, 'no shot of 3-6s starts at or past the midpoint of the runtime')

    # --- rule 6: opening shot ----------------------------------------------------------
    first_spec = (real_shots[0].get('spec') or {}) if real_shots else {}
    if real_shots and first_spec.get('shot_type') != 'medium':
        fail(6, f'the first shot is shot_type={first_spec.get("shot_type")!r}, '
                "expected 'medium'")

    # --- rule 7: hero product macro/insert in the first N seconds --------------------
    has_early_hero = any(
        start_times[i] < FIRST_PRODUCT_WINDOW_S
        and (s.get('spec') or {}).get('shot_type') in ('macro', 'insert')
        and (s.get('spec') or {}).get('product_visibility') == 'hero'
        for i, s in enumerate(shots) if s.get('kind') == 'shot')
    if not has_early_hero:
        fail(7, f'no macro/insert hero shot starts before {FIRST_PRODUCT_WINDOW_S}s')

    # --- rule 8: transitions -----------------------------------------------------------
    dissolves = sum(1 for s in real_shots if (s.get('spec') or {}).get('transition_out')
                    == 'dissolve')
    if dissolves > MAX_DISSOLVES:
        fail(8, f'{dissolves} dissolve transitions, at most {MAX_DISSOLVES} allowed')

    # --- rule 9: product-visible share, banded by mode and scaled by product count ----
    lo_band, hi_band = product_band(mode, len(product_ids))
    if total > 0:
        visible = sum(float(s.get('duration') or 0) for s in real_shots
                      if (s.get('spec') or {}).get('product_visibility') in
                      ('medium', 'hero'))
        fraction = visible / total
        if not (lo_band <= fraction <= hi_band):
            fail(9, f'the product is visible {fraction:.0%} of the runtime, {mode!r} '
                    f'mode expects {lo_band:.0%}-{hi_band:.0%}')

    # --- rule 10: at most one standout camera move -------------------------------------
    standouts = sum(1 for s in real_shots
                    if (s.get('spec') or {}).get('camera_move') in STANDOUT_MOVES)
    if standouts > MAX_STANDOUT_MOVES:
        fail(10, f'{standouts} standout camera moves (crane_rise/orbit), at most '
                 f'{MAX_STANDOUT_MOVES} allowed')

    # --- rule 11: every id referenced was actually given ------------------------------
    allowed_products = set(product_ids)
    used_products = {pid for s in shots for pid in (s.get('product_ids') or [])}
    unknown_products = used_products - allowed_products
    if unknown_products:
        fail(11, f'unknown product_ids: {sorted(unknown_products)}')
    if character_ids is not None:
        allowed_characters = set(character_ids)
        used_characters = {cid for s in shots for cid in (s.get('character_ids') or [])}
        unknown_characters = used_characters - allowed_characters
        if unknown_characters:
            fail(11, f'unknown character_ids: {sorted(unknown_characters)}')

    return errors


def demo() -> None:
    """Validator self-check: a hand-built valid board passes; breaking each rule one at
    a time produces that rule's own error. No network — generate_concepts/
    generate_storyboard are not exercised here."""
    product_ids = ['prod-1']
    character_ids = ['char-1']
    brief = {'duration': 24}
    mode = 'story'

    def shot(duration, shot_type, visibility, camera_move='static',
            transition_out='cut', products=None, characters=('char-1',)):
        return {'kind': 'shot', 'duration': duration,
                'character_ids': list(characters), 'product_ids': list(products or []),
                'spec': {'shot_type': shot_type, 'product_visibility': visibility,
                         'camera_move': camera_move, 'transition_out': transition_out}}

    def end_card(duration):
        return {'kind': 'end_card', 'duration': duration, 'character_ids': [],
                'product_ids': [], 'spec': {'brand_text': 'ACME', 'tagline': 'Forever'}}

    def valid_board():
        return {
            'title': 'Golden hour', 'visual_style': 'warm', 'palette': 'gold and amber',
            'emotional_arc': 'longing to joy', 'music_direction': 'soft strings',
            'shots': [
                shot(3, 'medium', 'small'),                                    # 0-3
                shot(2, 'macro', 'hero', products=['prod-1']),                 # 3-5
                shot(2.5, 'close', 'small'),                                   # 5-7.5
                shot(2, 'medium', 'medium', products=['prod-1']),              # 7.5-9.5
                shot(3, 'wide', 'none'),                                       # 9.5-12.5
                shot(3, 'close', 'small', camera_move='crane_rise'),           # 12.5-15.5
                shot(5, 'medium', 'small', transition_out='dissolve'),         # 15.5-20.5
                end_card(3.5),                                                # 20.5-24
            ],
        }

    board = valid_board()
    errors = validate_storyboard(board, brief, mode, product_ids, character_ids)
    assert errors == [], errors
    assert hard_errors(errors) == [] and soft_errors(errors) == [], errors

    # --- rule 1: shot count / end card ------------------------------------------------
    broken = valid_board()
    broken['shots'] = broken['shots'][:3] + [broken['shots'][-1]]     # too few real shots
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 1 for e in errs), errs

    broken = valid_board()
    broken['shots'][-1] = shot(3.5, 'medium', 'small')     # end_card replaced by a shot
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 1 for e in errs), errs

    # --- rule 2: duration sum ----------------------------------------------------------
    broken = valid_board()
    broken['shots'][0]['duration'] = 10
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 2 for e in errs), errs

    # --- rule 3: ordinary shot length --------------------------------------------------
    broken = valid_board()
    broken['shots'][0]['duration'] = 8
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 3 for e in errs), errs

    # --- rule 4: end card fraction ------------------------------------------------------
    broken = valid_board()
    broken['shots'][-1]['duration'] = 1
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 4 for e in errs), errs

    # --- rule 5: long take past the midpoint --------------------------------------------
    broken = valid_board()
    broken['shots'][5]['duration'] = 1     # was the 3s crane_rise shot past the midpoint
    broken['shots'][6]['duration'] = 7     # was the 5s dissolve shot (also >6, so this
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)      # fixture
    assert any(e['rule'] == 5 for e in errs), errs                    # also trips rule 3, HARD
    assert any(e['rule'] == 5 and e['severity'] == 'soft' for e in errs), errs   # rule 5 is SOFT

    # --- rule 6: opening shot ------------------------------------------------------------
    broken = valid_board()
    broken['shots'][0]['spec']['shot_type'] = 'wide'
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 6 for e in errs), errs
    assert soft_errors(errs) and not hard_errors(errs), errs   # rule 6 is SOFT

    # --- rule 7: early hero macro --------------------------------------------------------
    broken = valid_board()
    broken['shots'][1]['spec']['shot_type'] = 'wide'
    broken['shots'][1]['spec']['product_visibility'] = 'none'
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 7 for e in errs), errs

    # --- rule 8: transitions --------------------------------------------------------------
    broken = valid_board()
    broken['shots'][2]['spec']['transition_out'] = 'dissolve'
    broken['shots'][4]['spec']['transition_out'] = 'dissolve'
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 8 for e in errs), errs

    # --- rule 9: product-visible share, banded by mode -------------------------------------
    broken = valid_board()
    for s in broken['shots']:
        if s['kind'] == 'shot':
            s['spec']['product_visibility'] = 'none'
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 9 for e in errs), errs          # also trips rule 7, HARD (no
    assert any(e['rule'] == 9 and e['severity'] == 'soft' for e in errs), errs  # hero shot
    # left) — this fixture drops ALL visibility, rule 9 is SOFT regardless

    # --- rule 10: standout camera moves --------------------------------------------------
    broken = valid_board()
    broken['shots'][3]['spec']['camera_move'] = 'orbit'
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 10 for e in errs), errs
    assert soft_errors(errs) and not hard_errors(errs), errs   # rule 10 is SOFT

    # --- rule 11: unknown ids --------------------------------------------------------------
    broken = valid_board()
    broken['shots'][0]['product_ids'] = ['not-a-real-product']
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 11 for e in errs), errs

    broken = valid_board()
    broken['shots'][0]['character_ids'] = ['not-a-real-character']
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 11 for e in errs), errs
    # character_ids=None skips that half of rule 11 rather than failing closed.
    errs_no_chars = validate_storyboard(broken, brief, mode, product_ids, None)
    assert not any('character_ids' in e['message'] for e in errs_no_chars), errs_no_chars

    # --- rule 12: halves only ---------------------------------------------------------------
    broken = valid_board()
    broken['shots'][0]['duration'] = 2.3
    errs = validate_storyboard(broken, brief, mode, product_ids, character_ids)
    assert any(e['rule'] == 12 for e in errs), errs
    assert hard_errors(errs), errs   # rule 12 is HARD (this codebase's own decision)

    # --- the SAME board passes 'story' (already proven above) but fails 'showcase' --------
    # (its 17% visible share is well under showcase's 35-45% band) — proves the band is
    # actually keyed off `mode`, not a fixed number.
    errs = validate_storyboard(valid_board(), brief, 'showcase', product_ids, character_ids)
    assert any(e['rule'] == 9 for e in errs), 'a story-band board should fail showcase mode'

    # --- product_band: upper bound rises 10pp per extra product, capped at 60%, lower --
    # --- bound never moves; a 2-product campaign showing the product 50% of the time ---
    # --- (the real failure this was added for) must now pass 'story' mode -------------
    assert product_band('story', 1) == (0.15, 0.25)
    assert product_band('story', 2) == (0.15, 0.35)
    assert product_band('story', 3) == (0.15, 0.45)
    assert product_band('story', 10) == (0.15, 0.60), 'must cap at 60%'
    assert product_band('showcase', 1) == (0.35, 0.45)
    assert product_band('showcase', 3) == (0.35, 0.60), 'must cap at 60%'

    # A board at ~29% product-visible share (shot 0 bumped from 'small' to 'medium',
    # +3s of the 24s runtime on top of valid_board()'s baseline 4s) fails the 1-product
    # story band (15-25%) but clears the 2-product band (15-35%, +10pp) — the real bug
    # report's shape (a necklace+earrings campaign hard-failing against a single-product
    # reference band), fixed by widening the band rather than by tightening the boards.
    two_products = ['prod-1', 'prod-2']
    shifted_board = valid_board()
    shifted_board['shots'][0]['spec']['product_visibility'] = 'medium'
    errs_one_product = validate_storyboard(shifted_board, brief, mode, product_ids,
                                           character_ids)
    assert any(e['rule'] == 9 for e in errs_one_product), errs_one_product
    assert soft_errors(errs_one_product) and not hard_errors(errs_one_product), \
        errs_one_product   # rule 9 is SOFT — never fails the job by itself

    errs_two_products = validate_storyboard(shifted_board, brief, mode, two_products,
                                            character_ids)
    assert not any(e['rule'] == 9 for e in errs_two_products), errs_two_products
    print('director.product_band ok')

    # --- generate_storyboard: severity-aware retry, no network — _ask_storyboard is -----
    # --- monkeypatched at module level (same trick ads_flow_test.py uses for the two ---
    # --- real Anthropic calls: a bare-name call inside this module resolves through ---
    # --- the module globals at call time, so reassigning the global here is visible ---
    # --- to generate_storyboard without it taking the fake as a parameter) -------------
    global _ask_storyboard
    real_ask_storyboard = _ask_storyboard
    concept = {'mode': 'story', 'name': 'x', 'core_idea': '', 'emotional_hook': '',
              'visual_world': '', 'story_arc': '', 'tagline': ''}
    demo_products, demo_characters = [{'id': 'prod-1'}], [{'id': 'char-1'}]

    hard_broken_board = valid_board()
    hard_broken_board['shots'][0]['duration'] = 40         # rule 3 (HARD): way outside 1-6s

    soft_broken_board = valid_board()
    soft_broken_board['shots'][0]['spec']['product_visibility'] = 'medium'  # rule 9 (SOFT)

    calls = []
    _ask_storyboard = lambda system, user, on_progress=None: (
        calls.append(user), hard_broken_board)[1]
    try:
        try:
            generate_storyboard(brief, concept, demo_products, demo_characters)
            assert False, 'a board still HARD-broken after the one retry must raise'
        except DirectorError as e:
            assert any('[3]' in m for m in e.errors), e.errors
        assert len(calls) == 2, f'expected exactly one retry, got {len(calls)} attempts'
    finally:
        _ask_storyboard = real_ask_storyboard
    print('director.generate_storyboard (hard errors raise after one retry) ok')

    # soft-only errors, still within SOFT_RETRY_BUDGET_S: retried once, then ACCEPTED
    # with warnings (never raises) even though the fake keeps returning the same board.
    calls = []
    _ask_storyboard = lambda system, user, on_progress=None: (
        calls.append(user), soft_broken_board)[1]
    try:
        board, warnings = generate_storyboard(brief, concept, demo_products, demo_characters)
        assert board == soft_broken_board, board
        assert warnings and all(w['severity'] == 'soft' for w in warnings), warnings
        assert len(calls) == 2, 'a young soft-only attempt must retry once'
    finally:
        _ask_storyboard = real_ask_storyboard
    print('director.generate_storyboard (soft-only accepted with warnings, retried '
         'while young) ok')

    # soft-only errors, but the call is already OLD (>= SOFT_RETRY_BUDGET_S elapsed) ->
    # accepted on the very first attempt, no second model call spent on a style nit.
    calls = []
    _ask_storyboard = lambda system, user, on_progress=None: (
        calls.append(user), soft_broken_board)[1]
    real_monotonic = time.monotonic
    fake_clock = iter([0.0, SOFT_RETRY_BUDGET_S + 1])
    time.monotonic = lambda: next(fake_clock, SOFT_RETRY_BUDGET_S + 1)
    try:
        board, warnings = generate_storyboard(brief, concept, demo_products, demo_characters)
        assert warnings, warnings
        assert len(calls) == 1, 'an old soft-only attempt must NOT retry'
    finally:
        time.monotonic = real_monotonic
        _ask_storyboard = real_ask_storyboard
    print('director.generate_storyboard (soft-only accepted without retry once old) ok')

    # --- expected_shot_range: in-band is fixed, outside scales -----------------------------
    assert expected_shot_range(25) == (7, 9)
    short_lo, short_hi = expected_shot_range(10)
    assert short_lo < 7, (short_lo, short_hi)
    long_lo, long_hi = expected_shot_range(45)
    assert long_hi > 9, (long_lo, long_hi)

    # --- _assert_schema_supported: every schema we actually send the API must pass, ----
    # --- and the checker itself must go red on both failure modes it exists to catch ---
    _assert_schema_supported(CONCEPTS_SCHEMA)
    _assert_schema_supported(STORYBOARD_SCHEMA)
    try:
        _assert_schema_supported({'type': 'array', 'minItems': 3,
                                  'items': {'type': 'string'}})
        assert False, '_assert_schema_supported should have raised on minItems: 3'
    except ValueError:
        pass
    try:
        _assert_schema_supported({'type': 'object',
                                  'properties': {'a': {'type': 'string'}}})
        assert False, ('_assert_schema_supported should have raised on a missing '
                       'additionalProperties: False')
    except ValueError:
        pass

    # --- _assert_schema_supported's >24-optional-properties check (Anthropic's own ----
    # --- limit — this is the exact shape that broke the real storyboard call) ---------
    too_many_optional = {
        'type': 'object',
        'properties': {f'f{i}': {'type': 'string'} for i in range(25)},
        'required': [],
        'additionalProperties': False,
    }
    try:
        _assert_schema_supported(too_many_optional)
        assert False, ('_assert_schema_supported should have raised on 25 optional '
                       'properties')
    except ValueError:
        pass

    # --- _assert_all_required: our own zero-optional-properties policy ----------------
    _assert_all_required(CONCEPTS_SCHEMA)
    _assert_all_required(STORYBOARD_SCHEMA)
    try:
        _assert_all_required({'type': 'object',
                              'properties': {'a': {'type': 'string'}}, 'required': [],
                              'additionalProperties': False})
        assert False, '_assert_all_required should have raised on an optional property'
    except ValueError:
        pass

    # --- _normalise_spec: ""/null filler dropped, real values kept --------------------
    normalised = _normalise_spec({'shot_type': 'medium', 'camera_move': None,
                                  'dialogue': '', 'lens': '35mm'})
    assert normalised == {'shot_type': 'medium', 'lens': '35mm'}, normalised

    # --- _reply_json: truncated / refused / thinking-only / multi-text-block replies ---
    from types import SimpleNamespace

    truncated = SimpleNamespace(stop_reason='max_tokens', content=[])
    try:
        _reply_json(truncated)
        assert False, '_reply_json should have raised on stop_reason=max_tokens'
    except DirectorError:
        pass

    refused = SimpleNamespace(
        stop_reason='refusal', content=[],
        stop_details=SimpleNamespace(category='policy', explanation='no reason given'))
    try:
        _reply_json(refused)
        assert False, '_reply_json should have raised on stop_reason=refusal'
    except DirectorError as e:
        assert 'policy' in str(e), e

    thinking_then_text = SimpleNamespace(stop_reason='end_turn', content=[
        SimpleNamespace(type='thinking', text='reasoning about the brief...'),
        SimpleNamespace(type='text', text='{"concepts": []}'),
    ])
    assert _reply_json(thinking_then_text) == {'concepts': []}

    two_text_blocks = SimpleNamespace(stop_reason='end_turn', content=[
        SimpleNamespace(type='text', text='{"a": 1, '),
        SimpleNamespace(type='text', text='"b": 2}'),
    ])
    assert _reply_json(two_text_blocks) == {'a': 1, 'b': 2}

    print('director ok')


if __name__ == '__main__':
    demo()
