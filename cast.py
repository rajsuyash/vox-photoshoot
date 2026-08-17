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
    'Photorealistic studio headshot and upper body portrait of {description}. '
    'Neutral light grey seamless studio background, soft even beauty lighting, '
    'plain black sleeveless top, hair styled simply and tucked behind both ears. '
    'Polished agency test shot of a working fashion model, poised and confident. '
    'Her earlobes are completely bare and empty, her neck is bare, and she wears '
    'absolutely nothing on her ears, neck, nose or hair. '
    'Sharp focus, natural skin texture with visible pores, shot on 85mm lens, '
    'professional model casting photograph, neutral relaxed expression, looking at camera.'
)

# Deliberately spread across region, age and skin tone — a jewellery brand casts for
# the customer they want, and one "generic Indian model" would not cover the range.
# Editorial casting: young adult professional fashion models, all clearly adult.
# The model renders faces older than briefed, so these read mid-to-late twenties even
# though they are written at 22-27 — do not lower these numbers to chase a younger look.
# Regional range and skin tone range are kept: an Indian jewellery brand sells across
# the country, and gold reads very differently on different complexions.
MODEL_LOOK = (
    'She is a professional adult fashion model with a striking editorial face, high '
    'cheekbones, symmetrical features, clear glowing skin, a defined jawline, a long '
    'neck, and a tall slender runway physique'
)

CAST = [
    ('aditi', f'a 24 year old woman from North India. {MODEL_LOOK}, with fair wheatish '
              'skin and long straight glossy black hair'),
    ('meera', f'a 25 year old woman from South India. {MODEL_LOOK}, with deep brown skin '
              'and thick lustrous wavy black hair'),
    ('kavya', f'a 23 year old woman from Gujarat. {MODEL_LOOK}, with warm honey toned '
              'skin and long softly waved dark brown hair'),
    ('priya', f'a 26 year old woman from Bengal. {MODEL_LOOK}, with medium wheatish skin, '
              'large expressive eyes and long straight dark hair'),
    ('simran', f'a 25 year old Punjabi woman. {MODEL_LOOK}, with fair luminous skin and '
               'very long thick dark hair'),
    ('tara', f'a 24 year old woman from Maharashtra. {MODEL_LOOK}, with golden brown skin '
             'and sleek shoulder length dark hair'),
    ('ananya', f'a 27 year old woman from Tamil Nadu. {MODEL_LOOK}, with rich dark brown '
               'skin and long glossy black hair'),
    ('nisha', f'a 23 year old woman from Northeast India. {MODEL_LOOK}, with light clear '
              'skin and sleek straight black hair'),
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
            arguments={'prompt': BASE_BRIEF.format(description=description), **ARGUMENTS},
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
