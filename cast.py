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
# Expressions here are soft and closed-mouth on purpose. These portraits are the identity
# reference for every shoot, and composition.EXPRESSIONS now lets a client ask for a
# serious campaign — a reference image grinning broadly fights that request.
EDITORIAL = [
    ('laila',
     'a 26 year old Kashmiri woman, very fair skin with cool pink undertones and a light '
     'scatter of freckles across her nose, pale green-hazel eyes, a fine straight nose, '
     'sharp high cheekbones and a narrow jaw, dark chestnut brown hair falling straight '
     'past her shoulders with a centre parting, bare clean skin with minimal makeup'),
    ('rukmini',
     'a 24 year old Tamil woman, deep rich brown skin with warm red undertones, very '
     'large dark almond eyes, a broad softly rounded nose, full lips and a strong square '
     'jaw, thick black tightly curled hair worn in a high natural halo, glowing skin with '
     'bare lips and defined brows'),
    ('zoya',
     'a 29 year old Hyderabadi woman, olive skin with neutral undertones, heavy lidded '
     'dark eyes under strong straight brows, a long aquiline nose, sculpted cheekbones '
     'and a sharp chin, jet black hair pulled back tight and flat to the head, matte skin '
     'with a bare face'),
    ('mercy',
     'a 23 year old woman from Nagaland in northeast India, light golden skin with cool '
     'undertones, softly hooded monolid eyes set wide apart, high flat cheekbones, a small '
     'straight nose and a delicate jaw, glossy black hair in a blunt chin length bob, '
     'clean skin with a sheer wash of colour'),
    ('paromita',
     'a 27 year old Bengali woman, warm wheatish skin, a soft round face with a small '
     'pointed chin, wide set dark doe eyes with a gentle downturn, a small rounded nose, '
     'very long wavy black hair worn loose, soft dewy skin and bare lips'),
    ('gurleen',
     'a 25 year old Punjabi woman, fair skin with golden undertones, a strong square jaw '
     'and broad forehead, thick full brows over deep set brown eyes, a straight nose with '
     'a slightly rounded tip, dark brown hair worn in a long straight sleek fall, healthy '
     'skin with a clean brushed brow'),
    ('devika',
     'a 31 year old Malayali woman, dusky brown skin with olive undertones, a long oval '
     'face and unusually long neck, calm heavy lidded eyes, a straight narrow nose and '
     'wide full mouth, black curly hair scraped back into a low tight knot, bare glowing '
     'skin'),
    ('ira',
     'a 22 year old Marathi woman, medium wheatish skin, a heart shaped face with a wide '
     'forehead narrowing to a small chin, bright round dark eyes, a short upturned nose '
     'and a small gap between her front teeth, dark hair in a messy shoulder length shag '
     'with a full fringe'),
    ('naina',
     'a 28 year old Rajasthani woman, warm sand toned skin, a long narrow face with '
     'prominent high cheekbones and a strong straight nose, deep set dark eyes, thin '
     'elegant lips, very long black hair in a single thick plait over one shoulder, '
     'bare skin and a strong natural brow'),
    ('sarayu',
     'a 26 year old Telugu woman, rich medium brown skin with golden undertones, a broad '
     'open face with wide cheekbones, large expressive dark eyes with a natural upward '
     'tilt, a softly flared nose, black hair in a loose low bun with wisps at the temple'),
    ('tenzin',
     'a 24 year old Ladakhi woman, fair skin with cool rose undertones and wind flushed '
     'cheeks, warm dark brown eyes with a soft epicanthic fold, broad flat cheekbones, a '
     'small nose and a wide gentle mouth, straight black hair worn in two low plaits'),
    ('ayesha',
     'a 30 year old Lucknawi woman, pale wheatish skin with cool undertones, an oval face '
     'with a high smooth forehead, long dark lashes over grey-brown eyes, a fine narrow '
     'nose and a small full mouth, dark hair in a sleek middle parted low chignon'),
    ('kiran',
     'a 33 year old Indian woman, deep brown skin with cool undertones, an angular '
     'striking face with very sharp cheekbones and a square jaw, close cropped natural '
     'black hair worn very short, long neck, strong straight brows and a wide full mouth, '
     'entirely bare face'),
    ('meher',
     'a 25 year old Parsi woman from Mumbai, light olive skin, a delicate narrow face '
     'with a prominent straight nose and a pointed chin, large light brown eyes set '
     'close, arched brows, dark wavy hair worn in a loose side parted style'),
    ('shreya',
     'a 21 year old Indian woman, medium wheatish skin, a youthful round face with full '
     'cheeks, wide innocent dark eyes, a small button nose and a rosebud mouth, long '
     'glossy dark brown hair with a blunt cut and a centre parting'),
    ('nandini',
     'a 34 year old Indian woman, warm honey toned skin with visible fine lines at the '
     'eyes, a mature composed face with defined cheekbones and a firm jaw, calm dark '
     'eyes, a straight nose, silver strands threaded through dark hair worn in a low '
     'sleek twist, elegant and self possessed'),
    ('amara',
     'a 27 year old woman of mixed Indian and East African heritage, deep warm brown skin, '
     'a broad symmetrical face with very high cheekbones, long almond eyes, a full wide '
     'mouth and a softly broad nose, black hair in long fine braids gathered back'),
    ('yasmin',
     'a 29 year old Indian woman, medium olive skin, a strong androgynous face with a '
     'wide square jaw, straight heavy brows, narrow watchful dark eyes and a long '
     'straight nose, black hair slicked back wet-look off the face, editorial and severe'),
    ('leela',
     'a 23 year old Indian woman, fair wheatish skin, a soft oval face with a gentle jaw, '
     'unusually large round dark eyes, a small straight nose, dark brown hair in loose '
     'natural ringlet curls falling around her face'),
    ('anouk',
     'a 28 year old woman of mixed Indian and French heritage, light golden skin, a fine '
     'boned face with a sharp narrow nose and hollow cheeks, pale grey-green eyes, thin '
     'arched brows, light brown hair with sun lightened ends worn in a low ponytail'),
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
EDITORIAL_BRIEF = (
    'Photorealistic editorial beauty portrait of {description} '
    'Even soft studio lighting from a large softbox with gentle fill, pale neutral '
    'background, clean and modern. '
    'She wears a plain pale blush silk top with a high round neckline and elbow length '
    'sleeves that fully cover both shoulders and upper arms. '
    'Her earlobes are completely bare and empty, her neck is bare, and she wears '
    'absolutely nothing on her ears, neck, nose or hair. '
    'Sharp focus, natural skin texture with visible pores and no heavy retouching, '
    'shot on an 85mm lens at f/2, head and shoulders, high end fashion magazine '
    'editorial portrait.'
)

# A brand mark appeared bottom-right on a pilot face — the model inventing a logo for
# the fictional campaign it thinks it is shooting. It has to be forbidden explicitly:
# these portraits become the identity reference for every shoot, so a watermark would
# follow the face into the client's paid images.
EDITORIAL_NEGATIVE = (
    ' No text, no watermark, no logo, no brand name, no signature and no lettering '
    'anywhere in the frame. No jewellery of any kind. No heavy bronzer, no oily sheen, '
    'no bare shoulders, no strapless top.'
)

CAST = CAST + [
    (name, f'{look}. {EDITORIAL_EXPRESSION}') for name, look in EDITORIAL
]

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


def main() -> None:
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
