"""Generate the fixed cast of Indian models the demo picks from.

soul/standard has no seed parameter, so a face cannot be reproduced from a prompt.
The cast is therefore generated once and the resulting images are persisted — those
files become the reference images every later shoot conditions on.

    .venv/bin/python cast.py --pilot     # 2 faces, check quality before committing
    .venv/bin/python cast.py             # the full cast
"""

import json
import pathlib
import sys

import higgsfield_client

import hf

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

ARGUMENTS = {
    'num_images': 1,
    'resolution': '1080p',
    'aspect_ratio': '3:4',
}


def generate(entries) -> list[dict]:
    CAST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

    for name, description in entries:
        if name in manifest:
            print(f'{name}: already cast, skipping')
            continue
        print(f'{name}: generating ...')
        result = higgsfield_client.subscribe(
            'higgsfield-ai/soul/standard',
            arguments={
                'prompt': BASE_BRIEF.format(description=description) + NEGATIVE_LOOK,
                **ARGUMENTS,
            },
        )
        urls = hf.output_urls(result)
        if not urls:
            print(f'  FAILED status={result.get("status")}')
            continue
        [path] = hf.download(urls, CAST_DIR, prefix=name)
        manifest[name] = {'description': description, 'file': str(path)}
        MANIFEST.write_text(json.dumps(manifest, indent=1))
        print(f'  {path}')

    return manifest


def main() -> None:
    entries = CAST[:2] if '--pilot' in sys.argv else CAST
    cost = hf.estimate('/higgsfield-ai/soul/standard',
                       {'prompt': 'x', **ARGUMENTS})
    print(f'{len(entries)} faces x {cost["credits"]} credits = '
          f'{len(entries) * float(cost["credits"]):.2f} credits total')
    if input('run? [y/N] ').strip().lower() != 'y':
        sys.exit('aborted')
    generate(entries)


if __name__ == '__main__':
    main()
