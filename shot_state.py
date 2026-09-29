"""The shot state machine: a pure transition table, no I/O, no provider imports.

A shot's lifecycle: created by the director (draft) -> instructions approved
(ready_for_frame) -> frame generated (frame_generating) -> ok/fail (frame_review /
frame_failed) -> approved (frame_approved) -> clip generated (video_generating) ->
ok/fail (video_review / video_failed) -> approved (video_approved = ready for render).

end_card shots skip every provider stage: they are rendered locally from the last
approved clip's final frame plus branding, so approve_instructions takes them straight
to video_approved.

    .venv/bin/python shot_state.py     # self-check, no DB needed
"""

Event = str
State = str


class IllegalTransition(Exception):
    """Raised for any (state, event) pair not in the table."""


# (state, event) -> new_state, for kind='shot'. A shot may regenerate a frame from
# frame_review, frame_approved, frame_failed, video_review, video_approved or
# video_failed — a customer reshooting a frame after approving the clip is normal, and a
# new frame invalidates whatever clip was built from the old one (see _POST below).
_SHOT_TRANSITIONS: dict[tuple[State, Event], State] = {
    ('draft', 'approve_instructions'): 'ready_for_frame',

    ('ready_for_frame', 'start_frame'): 'frame_generating',
    ('frame_generating', 'frame_done'): 'frame_review',
    ('frame_generating', 'frame_failed'): 'frame_failed',
    ('frame_failed', 'retry'): 'frame_generating',
    ('frame_review', 'approve_frame'): 'frame_approved',

    ('frame_approved', 'start_video'): 'video_generating',
    ('video_generating', 'video_done'): 'video_review',
    ('video_generating', 'video_failed'): 'video_failed',
    ('video_failed', 'retry'): 'video_generating',
    ('video_review', 'approve_video'): 'video_approved',

    # Regenerate a frame (a new variant) from any state that already has one. Doing so
    # drops any video approval built on the old frame — handled as a post-step below,
    # since a transition table can only name ONE destination state per (state, event).
    ('frame_review', 'start_frame'): 'frame_generating',
    ('frame_approved', 'start_frame'): 'frame_generating',
    ('video_review', 'start_frame'): 'frame_generating',
    ('video_approved', 'start_frame'): 'frame_generating',
    ('video_failed', 'start_frame'): 'frame_generating',

    # Regenerate a VIDEO (a new clip variant from the SAME already-approved frame) from
    # any state that already has one — the frame-level counterpart of the two edges
    # above. This does not touch the frame at all (unlike start_frame, which invalidates
    # it), so it only ever rolls back a VIDEO approval, never a frame one.
    ('video_review', 'start_video'): 'video_generating',
    ('video_approved', 'start_video'): 'video_generating',
}

# end_card: the only legal move is the local render, triggered by the same event name as
# a normal shot's first step so callers don't need a kind-specific event vocabulary.
_END_CARD_TRANSITIONS: dict[tuple[State, Event], State] = {
    ('draft', 'approve_instructions'): 'video_approved',
}

ALL_STATES = (
    'draft', 'ready_for_frame', 'frame_generating', 'frame_review', 'frame_approved',
    'frame_failed', 'video_generating', 'video_review', 'video_approved', 'video_failed',
)


def transition(state: State, event: Event, kind: str = 'shot') -> State:
    """The next state for (state, event, kind), or IllegalTransition."""
    table = _END_CARD_TRANSITIONS if kind == 'end_card' else _SHOT_TRANSITIONS
    key = (state, event)
    if key not in table:
        raise IllegalTransition(f'{kind} shot: no transition for state={state!r} '
                                 f'event={event!r}')
    return table[key]


# --- Edit invalidation -------------------------------------------------------------
#
# Editing a shot's spec can go stale relative to what was already generated. The rule is
# "drop to at most the state that still matches reality" — never advance a shot, and
# never touch state for fields that don't affect generation.
#
# One constant maps every editable field to its invalidation group, so the mapping is
# read in one place instead of scattered across `if field in (...)` checks.
FIELD_GROUPS = {
    # Creative/visual/camera fields and the frame prompt affect the still image itself.
    # A shot that has already gone past ready_for_frame is generated against a prompt
    # that no longer matches, so it drops back there (but never ADVANCES a draft shot
    # that hasn't even had its instructions approved yet).
    'creative_direction': 'frame', 'visual_style': 'frame', 'camera_angle': 'frame',
    'camera_move': 'frame', 'lighting': 'frame', 'environment': 'frame',
    'wardrobe': 'frame', 'product_placement': 'frame', 'image_prompt': 'frame',
    'character_ids': 'frame', 'product_ids': 'frame', 'negative_prompt': 'frame',
    # director.py's storyboard-generation spec keys that also describe the still image.
    'purpose': 'frame', 'scene_description': 'frame', 'emotional_beat': 'frame',
    'character_action': 'frame', 'facial_expression': 'frame',
    'product_interaction': 'frame', 'product_visibility': 'frame',
    'shot_type': 'frame', 'lens': 'frame', 'depth_of_field': 'frame',
    'time_of_day': 'frame', 'props': 'frame',

    # Motion prompt and duration only affect the clip built FROM an already-approved
    # frame, so they drop no further than frame_approved. motion_intensity is the one
    # director.py spec key that only changes motion, not the still.
    'motion_prompt': 'video', 'duration': 'video', 'motion_intensity': 'video',

    # Audio and text fields don't feed any provider call in v1 (music is version-level;
    # VO/SFX rendering is a later phase) — editing them changes nothing about state.
    # brand_text/tagline are the end_card's own text fields, same reasoning.
    'dialogue': 'none', 'voiceover': 'none', 'ambient_sound': 'none',
    'sound_effects': 'none', 'music_cue': 'none', 'on_screen_text': 'none',
    'text_placement': 'none', 'transition_in': 'none', 'transition_out': 'none',
    'brand_text': 'none', 'tagline': 'none',
}

# What "at most" means per group, ordered so index comparison gives severity.
_ORDER = {s: i for i, s in enumerate(ALL_STATES)}
_FRAME_CEILING = _ORDER['ready_for_frame']
_VIDEO_CEILING = _ORDER['frame_approved']


def after_edit(state: State, changed_fields: list[str]) -> State:
    """The state after editing `changed_fields`, applying the strictest invalidation.

    Never advances a shot (a draft stays draft — instructions still need approving) and
    never demotes past what the edit actually invalidates.
    """
    groups = {FIELD_GROUPS.get(f, 'frame') for f in changed_fields}  # unknown -> safest
    if not changed_fields or groups <= {'none'}:
        return state

    ceiling = _FRAME_CEILING if 'frame' in groups else _VIDEO_CEILING
    return ALL_STATES[min(_ORDER[state], ceiling)]


def demo() -> None:
    # --- legal edges, one per transition -------------------------------------------
    assert transition('draft', 'approve_instructions') == 'ready_for_frame'
    assert transition('ready_for_frame', 'start_frame') == 'frame_generating'
    assert transition('frame_generating', 'frame_done') == 'frame_review'
    assert transition('frame_generating', 'frame_failed') == 'frame_failed'
    assert transition('frame_failed', 'retry') == 'frame_generating'
    assert transition('frame_review', 'approve_frame') == 'frame_approved'
    assert transition('frame_approved', 'start_video') == 'video_generating'
    assert transition('video_generating', 'video_done') == 'video_review'
    assert transition('video_generating', 'video_failed') == 'video_failed'
    assert transition('video_failed', 'retry') == 'video_generating'
    assert transition('video_review', 'approve_video') == 'video_approved'

    # regenerate-a-frame edges
    for s in ('frame_review', 'frame_approved', 'video_review', 'video_approved',
              'video_failed'):
        assert transition(s, 'start_frame') == 'frame_generating', s

    # regenerate-a-video edges: a new clip variant from the same approved frame, without
    # touching the frame itself.
    for s in ('video_review', 'video_approved'):
        assert transition(s, 'start_video') == 'video_generating', s

    # end_card
    assert transition('draft', 'approve_instructions', kind='end_card') == 'video_approved'

    # --- illegal edges ---------------------------------------------------------------
    illegal = [
        ('draft', 'start_frame'),
        ('ready_for_frame', 'approve_instructions'),
        ('frame_review', 'video_done'),
        ('video_approved', 'approve_video'),
        ('frame_failed', 'approve_frame'),
        ('draft', 'start_video'),
    ]
    for state, event in illegal:
        try:
            transition(state, event)
            raise AssertionError(f'expected IllegalTransition for {state}/{event}')
        except IllegalTransition:
            pass

    for event in ('start_frame', 'start_video', 'frame_done', 'approve_frame'):
        try:
            transition('draft', event, kind='end_card')
            raise AssertionError(f'end_card allowed a provider event: {event}')
        except IllegalTransition:
            pass

    # --- invalidation groups ---------------------------------------------------------
    assert after_edit('frame_approved', ['camera_angle']) == 'ready_for_frame'
    assert after_edit('video_approved', ['image_prompt']) == 'ready_for_frame'
    assert after_edit('frame_review', ['visual_style']) == 'ready_for_frame'
    # never advances a draft
    assert after_edit('draft', ['camera_angle']) == 'draft'
    # already below the frame ceiling: stays put, doesn't jump up
    assert after_edit('ready_for_frame', ['lighting']) == 'ready_for_frame'

    assert after_edit('video_approved', ['motion_prompt']) == 'frame_approved'
    assert after_edit('video_approved', ['duration']) == 'frame_approved'
    assert after_edit('frame_approved', ['duration']) == 'frame_approved'  # already there
    assert after_edit('ready_for_frame', ['duration']) == 'ready_for_frame'  # below ceiling

    for field in ('dialogue', 'voiceover', 'ambient_sound', 'sound_effects', 'music_cue',
                  'on_screen_text', 'text_placement', 'transition_in', 'transition_out'):
        assert after_edit('video_approved', [field]) == 'video_approved', field
        assert after_edit('frame_generating', [field]) == 'frame_generating', field

    # mixed groups: strictest wins
    assert after_edit('video_approved', ['duration', 'camera_angle']) == 'ready_for_frame'
    assert after_edit('video_approved', ['duration', 'music_cue']) == 'frame_approved'

    assert after_edit('draft', []) == 'draft'

    print('shot_state ok')


if __name__ == '__main__':
    demo()
