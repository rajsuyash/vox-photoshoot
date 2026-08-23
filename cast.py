"""Generate the fixed cast of Indian models the demo picks from.

soul/standard has no seed parameter, so a face cannot be reproduced from a prompt.
The cast is therefore generated once and the resulting images are persisted — those
files become the reference images every later shoot conditions on.

    .venv/bin/python cast.py --pilot     # 2 faces, check quality before committing
    .venv/bin/python cast.py             # the full cast
"""

import concurrent.futures
import json
import pathlib
import sys

import hf
import providers

CAST_DIR = pathlib.Path('assets/cast')
MANIFEST = CAST_DIR / 'cast.json'

# These portraits are fed into every shoot as the face reference, so the ears must be
# empty — a cast face wearing its own earrings can bleed into the client's product shot.
# "no jewellery" was ignored twice; the positive phrasing below is what actually holds,
# the same lesson as the phantom necklace in locations.py.
BASE_BRIEF = (
    'Photorealistic beauty portrait of a smiling {description}. '
    'She is laughing warmly with a wide genuine open smile showing her teeth, eyes '
    'crinkling, radiating happiness and confidence. '
    'Bright soft evenly diffused studio lighting from a large softbox, clean and airy, '
    'pale cream background. Her skin is fair and luminous with a natural matte finish. '
    'She wears an elegant pale blush silk blouse with a high round neckline and '
    'elbow length sleeves that fully cover both shoulders and upper arms. '
    'Her earlobes are completely bare and empty, her neck is bare, and she wears '
    'absolutely nothing on her ears, neck, nose or hair. '
    'Sharp focus, healthy skin with natural texture, shot on an 85mm lens at f/2, '
    'head and shoulders, high end Indian jewellery brand advertising campaign.'
)

# Lighting and expression were both ignored when they sat at the end of the prompt.
# Moving them to the front fixed it — this model weights early tokens heavily, which is
# the same reason the product description has to stay short.
NEGATIVE_LOOK = (
    ' No harsh orange or amber rim lighting, no heavy bronzer, no oily sheen, '
    'no bare shoulders, no strapless top, no serious or neutral expression.'
)

# Deliberately spread across region, age and skin tone — a jewellery brand casts for
# the customer they want, and one "generic Indian model" would not cover the range.
# Editorial casting: young adult professional fashion models, all clearly adult.
# The model renders faces older than briefed, so these read mid-to-late twenties even
# though they are written at 22-27 — do not lower these numbers to chase a younger look.
# Regional range and skin tone range are kept: an Indian jewellery brand sells across
# the country, and gold reads very differently on different complexions.
# Matched to the client's own campaign imagery: fair to light wheatish skin with warm
# golden undertones, full soft-glam makeup, warm smiling expressions, styled volume hair.
# The earlier brief asked for a neutral agency test shot, which is why those faces read
# as casting polaroids rather than the finished campaign look the brand actually runs.
MODEL_LOOK = (
    'She is a beautiful young adult Indian fashion model with fair to light wheatish '
    'skin with warm golden undertones, luminous and radiant, high cheekbones, a defined '
    'jawline, large expressive dark eyes, full soft-glam commercial makeup with groomed '
    'defined brows, warm neutral eyeshadow, subtle kohl, highlighted cheekbones and '
    'glossy nude-pink lips, and a slim elegant figure'
)

# Hair is the main thing that tells one thumbnail from another, so it varies per model
# rather than defaulting to the same centre-parted sleek look on all eight.
EXPRESSION = (
    'Warm genuine smile, joyful and confident, looking straight at the camera'
)

CAST = [
    ('aditi', f'a 24 year old Indian woman. {MODEL_LOOK}, her long dark brown hair worn '
              'in soft loose glossy waves falling over one shoulder. {EXPRESSION}'),
    ('meera', f'a 25 year old Indian woman. {MODEL_LOOK}, her hair in a soft elegant '
              'high bun with a few loose face-framing strands. {EXPRESSION}'),
    ('kavya', f'a 23 year old Indian woman. {MODEL_LOOK}, her long hair in a sleek high '
              'ponytail with volume at the crown. {EXPRESSION}'),
    ('priya', f'a 26 year old Indian woman. {MODEL_LOOK}, her hair in a glossy blow-dried '
              'shoulder length bob with a deep side parting. {EXPRESSION}'),
    ('simran', f'a 25 year old Indian woman. {MODEL_LOOK}, her very long thick hair worn '
               'loose and straight with a centre parting and soft shine. {EXPRESSION}'),
    ('tara', f'a 24 year old Indian woman. {MODEL_LOOK}, her hair in a romantic low '
             'chignon with soft curled tendrils at the temples. {EXPRESSION}'),
    ('ananya', f'a 27 year old Indian woman. {MODEL_LOOK}, her long hair in loose '
               'bouncy curls with plenty of volume. {EXPRESSION}'),
    ('nisha', f'a 23 year old Indian woman. {MODEL_LOOK}, her hair in a half-up style '
              'with soft waves and a light fringe. {EXPRESSION}'),
]


# --- the editorial cast ---------------------------------------------------------------
#
# WHY THIS LOOKS NOTHING LIKE MODEL_LOOK ABOVE:
#
# The first eight entries share one MODEL_LOOK string, so they are one woman with eight
# hairstyles — same skin, same cheekbones, same jaw, same eyes, same makeup. The comment
# on MODEL_LOOK claims a spread across region and skin tone; the string itself pins
# everyone to "fair to light wheatish". The code and its comment disagreed, and the
# comment lost.
#
# Casting a magazine editorial means distinct FACES, not distinct hair. Each entry below
# writes its own bone structure, skin tone, eye shape and nose, because those are what
# the eye actually reads at thumbnail size. Skin tone range is not decoration either:
# yellow gold reads completely differently on deep brown skin than on fair skin, and a
# jeweller needs to see their piece on the customer they are selling to.
#
# WHAT THE FIRST VERSION OF THIS LIST GOT WRONG:
#
# Distinct was read as ordinary. Entries asked for "bare clean skin with minimal makeup",
# "matte skin with a bare face", "a small gap between her front teeth", "visible fine
# lines at the eyes" — and the brief below asked for no retouching under flat even light.
# That is a passport photo, and the whole set came back looking like ID card scans of
# passers-by rather than the cast of a jewellery campaign. Six were unusable.
#
# So: every entry says MODEL, the face keeps its own bone structure and skin tone, and
# nothing in here asks for a bare face or an unstyled head. Distinctive bone structure
# is what makes a model photograph well; a bare face is just an unfinished one.
#
# Expressions here are soft and closed-mouth on purpose. These portraits are the identity
# reference for every shoot, and composition.EXPRESSIONS now lets a client ask for a
# serious campaign — a reference image grinning broadly fights that request.
EDITORIAL = [
    ('laila',
     'a 26 year old Kashmiri model, very fair skin with cool pink undertones and a fine '
     'scatter of freckles across her nose, pale green-hazel eyes, a fine straight nose, '
     'sharp high cheekbones and a narrow sculpted jaw, long dark chestnut hair blow-dried '
     'straight past her shoulders with a centre parting and a high gloss finish'),
    ('zoya',
     'a 29 year old Hyderabadi model, olive skin with neutral undertones and a luminous '
     'finish, heavy lidded dark eyes under strong straight brows, a long elegant aquiline '
     'nose, sculpted cheekbones and a sharp chin, jet black hair pulled back into a '
     'polished low knot'),
    ('mercy',
     'a 23 year old model from Nagaland in northeast India, light golden skin with cool '
     'undertones and a soft glow, striking hooded monolid eyes set wide apart, high flat '
     'cheekbones, a small straight nose and a delicate jaw, glossy black hair in a sharp '
     'blunt chin length bob'),
    ('paromita',
     'a 27 year old Bengali model, warm wheatish skin with a dewy finish, a soft oval '
     'face with a small pointed chin, wide set dark doe eyes, a small rounded nose, very '
     'long thick wavy black hair worn loose and glossy'),
    ('gurleen',
     'a 25 year old Punjabi model, fair skin with golden undertones, a strong square jaw '
     'and broad forehead, full groomed brows over deep set brown eyes, a straight nose '
     'with a slightly rounded tip, long dark brown hair in a sleek high shine straight '
     'fall'),
    ('ira',
     'a 22 year old Marathi model, medium wheatish skin, a heart shaped face with a wide '
     'forehead narrowing to a small chin, bright round dark eyes, a short neatly upturned '
     'nose, dark hair in a textured shoulder length shag with a soft full fringe'),
    ('sarayu',
     'a 26 year old Telugu model, rich medium brown skin with golden undertones and a '
     'radiant sheen, a broad open face with wide high cheekbones, large expressive dark '
     'eyes with a natural upward tilt, a softly flared nose, black hair in a polished '
     'low bun'),
    ('tenzin',
     'a 24 year old Ladakhi model, fair skin with cool rose undertones, warm dark brown '
     'eyes with a soft epicanthic fold, broad flat cheekbones, a small nose and a wide '
     'gentle mouth, straight jet black hair worn in two sleek low plaits'),
    ('ayesha',
     'a 30 year old Lucknawi model, pale wheatish skin with cool undertones, an oval face '
     'with a high smooth forehead, long dark lashes over grey-brown eyes, a fine narrow '
     'nose and a small full mouth, dark hair in a sleek middle parted low chignon'),
    ('meher',
     'a 25 year old Parsi model from Mumbai, light olive skin, a delicate narrow face '
     'with a prominent straight nose and a pointed chin, large light brown eyes, high '
     'arched brows, dark wavy hair in a glossy side parted fall'),
    ('shreya',
     'a 21 year old Indian model, medium wheatish skin, a youthful oval face with full '
     'cheeks, wide dark eyes, a small neat nose and a rosebud mouth, long glossy dark '
     'brown hair with a blunt cut and a centre parting'),
    ('nandini',
     'a 34 year old Indian model, warm honey toned skin, a composed mature face with high '
     'defined cheekbones and a firm jaw, calm dark eyes, a straight nose, a few silver '
     'strands threaded through dark hair worn in a low sleek twist, elegant and self '
     'possessed'),
    ('leela',
     'a 23 year old Indian model, fair wheatish skin, a soft oval face with a gentle jaw, '
     'unusually large round dark eyes, a small straight nose, dark brown hair in defined '
     'glossy ringlet curls falling around her face'),
    ('anouk',
     'a 28 year old model of mixed Indian and French heritage, light golden skin, a fine '
     'boned face with a sharp narrow nose and hollow cheeks, pale grey-green eyes, thin '
     'arched brows, light brown hair with sun lightened ends in a sleek low ponytail'),

    # Added after the first six were cut. Same rule: a face a casting director would
    # book, written as bone structure rather than as a mood.
    ('ishani',
     'a 24 year old Odia model, deep golden brown skin with a radiant sheen, an '
     'exceptionally long neck and elegant sloping shoulders, wide almond eyes under '
     'arched brows, razor sharp cheekbones and a narrow tapered jaw, jet black hair '
     'pulled into a high sleek ponytail'),
    ('tanvi',
     'a 23 year old Gujarati model, warm fair skin with a honey glow, upturned feline '
     'eyes with a natural lift at the outer corner, a fine straight nose, high wide '
     'cheekbones and a small pointed chin, dark hair in a glossy deep side parted '
     'blow-dry'),
    ('aaliya',
     'a 26 year old model from Delhi, light wheatish skin with neutral undertones, a tall '
     'angular face with a long straight nose and a defined square jaw, deep set dark eyes '
     'under thick straight brows, dark hair scraped into a clean centre parted low bun'),
    ('nyra',
     'a 22 year old Goan model of mixed Indian and Portuguese heritage, sun warmed light '
     'bronze skin, hazel-brown eyes, a fine upturned nose, softly hollowed cheeks and a '
     'wide full mouth, dark honey brown hair falling in loose beachy waves'),
    ('sanaya',
     'a 27 year old Sindhi model, warm honey toned skin with a luminous finish, a heart '
     'shaped face with high round cheekbones, large dark eyes with a heavy lash line, a '
     'small straight nose and full lips, thick dark hair in a voluminous middle parted '
     'blow-out'),
    ('vedika',
     'a 25 year old Mangalorean model, deep bronze skin with warm red undertones and a '
     'polished glow, an oval face with strong high cheekbones, long tilted dark eyes, a '
     'straight narrow nose, black hair in a sleek wet-look middle parting'),
    ('mahika',
     'a 21 year old Assamese model, light golden skin with cool undertones, a small '
     'delicately boned face with a pointed chin, wide round dark eyes with a soft fold, a '
     'small straight nose, glossy black hair cut to a blunt collarbone length'),
    ('rhea',
     'a 28 year old model from Chennai, rich dark brown skin with cool undertones and a '
     'luminous sheen, an elongated sculpted face with very high cheekbones and a long '
     'neck, large deep set dark eyes, a softly broad nose and a full wide mouth, black '
     'hair in a close cropped tapered crop'),
]

# The soft, closed-mouth expression these portraits carry. Deliberately not the wide
# open smile of the original eight: the portrait is an identity reference, and one
# grinning at the camera argues with a client who has asked for a serious campaign.
EDITORIAL_EXPRESSION = (
    'Her expression is calm and composed with a very faint soft closed-mouth smile, '
    'lips together, looking directly into the lens.'
)

# The editorial cast needs its own brief. BASE_BRIEF hardcodes "laughing warmly with a
# wide genuine open smile showing her teeth" and NEGATIVE_LOOK then forbids a neutral
# expression outright — so appending a calm expression to the description did nothing,
# and the pilot came back grinning. That was right for the original eight and is wrong
# now: composition.EXPRESSIONS lets a client ask for a serious campaign, and a reference
# portrait laughing at the camera argues with the request.
# The word MODEL and the words JEWELLERY BRAND have to be in the first line. This model
# weights early tokens heavily — the same reason lighting and expression had to move to
# the front of BASE_BRIEF — and the previous version buried the casting context behind
# "editorial beauty portrait of a 26 year old Kashmiri woman", which it read as a request
# for a photograph of a person rather than a booked model on a paid shoot.
#
# "natural skin texture with visible pores and no heavy retouching" is gone on purpose.
# It was there to avoid plastic AI skin and it worked, but combined with flat even light
# it produced twenty-eight documentary headshots. Professional retouching and a beauty
# key light are what separate a campaign face from a photo of someone at the DMV; the
# skin still reads real because the description asks for a finish, not for porcelain.
EDITORIAL_BRIEF = (
    'Photorealistic beauty campaign portrait of a professional fashion model, booked '
    'and paid for a luxury Indian jewellery brand advertising shoot, signed to a top '
    'modelling agency. The model is {description} '
    # Describing the rig put the rig in the picture: the first pilot came back with the
    # softbox and the reflector both in frame, above and below the model. Light is
    # described by what it does to the face now, never by the equipment that makes it.
    'Polished beauty campaign lighting, soft and directional from just above the lens '
    'with bright clean catchlights in the eyes and gentle sculpting shadows under the '
    'cheekbones, on a pale neutral seamless background. '
    'Full professional editorial makeup by a beauty team, sculpted groomed brows, '
    'defined lashes, subtle contour, highlighted cheekbones and a polished lip. Hair '
    'styled and finished by a professional hair stylist. '
    'She wears a plain pale blush silk top with a high round neckline and elbow length '
    'sleeves that fully cover both shoulders and upper arms. '
    'Her earlobes are completely bare and empty, her neck is bare, and she wears '
    'absolutely nothing on her ears, neck, nose or hair. '
    'Sharp focus, flawless luminous skin retouched to campaign standard while keeping '
    'natural texture, shot on an 85mm lens at f/2, tightly cropped head and shoulders '
    'filling the frame, high end jewellery brand advertising campaign.'
)

# A brand mark appeared bottom-right on a pilot face — the model inventing a logo for
# the fictional campaign it thinks it is shooting. It has to be forbidden explicitly:
# these portraits become the identity reference for every shoot, so a watermark would
# follow the face into the client's paid images.
EDITORIAL_NEGATIVE = (
    ' No text, no watermark, no logo, no brand name, no signature and no lettering '
    'anywhere in the frame. No jewellery of any kind. No heavy bronzer, no oily sheen, '
    'no bare shoulders, no strapless top. Not a passport photo, not an ID card photo, '
    'not a casting polaroid, not a candid snapshot of an ordinary person, no flat '
    'lifeless lighting, no tired or blank expression. No studio equipment anywhere in '
    'the frame: no softbox, no reflector, no light stand, no umbrella, no camera, no '
    'table, no desk and no furniture.'
)

CAST = CAST + [
    (name, f'{look}. {EDITORIAL_EXPRESSION}') for name, look in EDITORIAL
]

# What the picker card shows, and what the filters run on.
#
# The card used to print the first clause of the prompt — "a 24 year old Indian woman.
# She is a…" truncated mid-word, thirty times over. That is prompt text on display: it
# tells a jeweller nothing they can choose on, and it makes the product look unfinished.
#
# These are declared rather than parsed back out of the prose. Reading structure out of
# a prompt string has been a bug in this codebase three separate times (see LEARNINGS,
# "Substring traps") and there is no reason to make it four. `hair` is what the card
# reads; `hair_group` is what the filter matches, because "Blow-dried bob" and "Chin
# length bob" are the same choice to a customer and different strings to a computer.
LOOKS = {
    # The original eight share one look and differ only in hair — see MODEL_LOOK.
    'aditi':    ('Indian', 24, 'fair', 'Loose waves', 'long'),
    'meera':    ('Indian', 25, 'fair', 'High bun', 'tied'),
    'kavya':    ('Indian', 23, 'fair', 'High ponytail', 'tied'),
    'priya':    ('Indian', 26, 'fair', 'Bob', 'short'),
    'simran':   ('Indian', 25, 'fair', 'Straight', 'long'),
    'tara':     ('Indian', 24, 'fair', 'Low chignon', 'tied'),
    'ananya':   ('Indian', 27, 'fair', 'Loose curls', 'long'),
    'nisha':    ('Indian', 23, 'fair', 'Half-up, fringe', 'long'),

    'laila':    ('Kashmiri', 26, 'fair', 'Straight', 'long'),
    'zoya':     ('Hyderabadi', 29, 'wheatish', 'Low knot', 'tied'),
    'mercy':    ('Naga', 23, 'fair', 'Chin-length bob', 'short'),
    'paromita': ('Bengali', 27, 'wheatish', 'Loose waves', 'long'),
    'gurleen':  ('Punjabi', 25, 'fair', 'Sleek', 'long'),
    'ira':      ('Marathi', 22, 'wheatish', 'Shoulder shag', 'short'),
    'sarayu':   ('Telugu', 26, 'medium', 'Low bun', 'tied'),
    'tenzin':   ('Ladakhi', 24, 'fair', 'Twin plaits', 'tied'),
    'ayesha':   ('Lucknawi', 30, 'wheatish', 'Low chignon', 'tied'),
    'meher':    ('Parsi', 25, 'wheatish', 'Side-parted', 'long'),
    'shreya':   ('Indian', 21, 'wheatish', 'Blunt cut', 'long'),
    'nandini':  ('Indian', 34, 'wheatish', 'Silver low twist', 'tied'),
    'leela':    ('Indian', 23, 'fair', 'Ringlet curls', 'long'),
    'anouk':    ('Indian and French', 28, 'fair', 'Low ponytail', 'tied'),
    'ishani':   ('Odia', 24, 'medium', 'High ponytail', 'tied'),
    'tanvi':    ('Gujarati', 23, 'fair', 'Side-parted', 'long'),
    'aaliya':   ('Delhi', 26, 'wheatish', 'Low bun', 'tied'),
    'nyra':     ('Goan', 22, 'medium', 'Beachy waves', 'long'),
    'sanaya':   ('Sindhi', 27, 'wheatish', 'Blow-out', 'long'),
    'vedika':   ('Mangalorean', 25, 'deep', 'Wet-look', 'long'),
    'mahika':   ('Assamese', 21, 'fair', 'Blunt collarbone', 'short'),
    'rhea':     ('Chennai', 28, 'deep', 'Cropped', 'short'),
}

# Four buckets, because gold reads differently on each and four is what a person can
# hold in their head at one decision point.
SKIN_TONES = [('fair', 'Fair'), ('wheatish', 'Wheatish'),
              ('medium', 'Medium'), ('deep', 'Deep')]
HAIR_GROUPS = [('long', 'Long'), ('short', 'Short'), ('tied', 'Tied back')]


def card(key: str) -> dict:
    """What /api/models sends the picker for one face. Empty dict for an unknown key."""
    look = LOOKS.get(key)
    if look is None:
        return {}
    origin, age, skin, hair, hair_group = look
    return {'name': key.title(), 'origin': origin, 'age': age,
            'skin': skin, 'hair': hair, 'hair_group': hair_group}


ARGUMENTS = {'aspect_ratio': '3:4'}


WORKERS = 6


def generate(entries) -> dict:
    CAST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    todo = [(n, d) for n, d in entries if n not in manifest]

    def one(pair):
        name, description = pair
        # Through providers.get() rather than a hardcoded Higgsfield call: the app runs
        # on fal, and a cast generated on a second provider account is a second account
        # to keep funded for no benefit.
        editorial = name in {n for n, _ in EDITORIAL}
        brief = EDITORIAL_BRIEF if editorial else BASE_BRIEF
        negative = EDITORIAL_NEGATIVE if editorial else NEGATIVE_LOOK
        urls = providers.get().generate(
            brief.format(description=description) + negative,
            aspect_ratio=ARGUMENTS['aspect_ratio'], quality='high', num_images=1)
        if not urls:
            raise RuntimeError('no images returned')
        [path] = hf.download(urls, CAST_DIR, prefix=name)
        return name, {'description': description, 'file': str(path)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(one, pair): pair[0] for pair in todo}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                name, entry = future.result()
            except Exception as error:        # noqa: BLE001 - one face, not the run
                print(f'{name}: FAILED {error}')
                continue
            manifest[name] = entry
            # Written per face: twenty generations is long enough that a crash at
            # eighteen must not discard seventeen.
            MANIFEST.write_text(json.dumps(manifest, indent=1))
            print(f'{name}: {entry["file"]}')

    return manifest


def _check_looks() -> None:
    """Every generated face has a card, every card's tags are ones the picker offers.

    A face without a LOOKS entry renders as a nameless tile with no filters matching it,
    which is a silent hole in the picker rather than a crash — so it gets an assertion.

        .venv/bin/python cast.py --check
    """
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    tones = {tone for tone, _ in SKIN_TONES}
    groups = {group for group, _ in HAIR_GROUPS}

    assert not [k for k in manifest if k not in LOOKS], 'generated face with no LOOKS entry'
    assert not [k for k in LOOKS if k not in {n for n, _ in CAST}], 'LOOKS names no one'
    for name, (_origin, age, skin, _hair, group) in LOOKS.items():
        assert skin in tones, f'{name}: unknown skin tone {skin!r}'
        assert group in groups, f'{name}: unknown hair group {group!r}'
        assert 18 <= age <= 60, f'{name}: implausible age {age}'
    assert card('nobody') == {}, 'an unknown key must not fabricate a card'
    print(f'{len(LOOKS)} faces tagged, {len(manifest)} generated, all consistent')


def main() -> None:
    if '--check' in sys.argv:
        return _check_looks()
    entries = CAST[:2] if '--pilot' in sys.argv else CAST
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    pending = [e for e in entries if e[0] not in manifest]
    print(f'{len(pending)} faces to generate, about ${len(pending) * 0.15:.2f} at fal')
    if not pending:
        return
    if '--yes' not in sys.argv and input('run? [y/N] ').strip().lower() != 'y':
        sys.exit('aborted')
    generate(pending)


if __name__ == '__main__':
    main()
