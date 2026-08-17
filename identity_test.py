"""Can we lock the chosen model's face without wrecking the product?

The gallery proved the cast is currently decorative: passing the face as prompt text
alone re-rolls a different woman every time, and foreign locations drag her away from
Indian entirely. This tests putting the cast portrait into image_urls instead.

The risk is known: in pave_test, adding a SECOND reference image degraded the earring.
So each variant is judged on two axes — is it the same face, and is it still the product.
"""

import json
import pathlib

import higgsfield_client

import hf
import locations
import shoot
from pave_test import CONTROL

PRODUCT_PHOTO = pathlib.Path('clientphoto/50D5L1DOUAGA2A_1.jpg')
MODEL_KEY = 'aditi'
# Santorini was one of the locations that drifted European — a fair stress test.
LOCATION_KEY = 'santorini'

# Restating ethnicity at the END of the prompt, after the location has had its say.
LATE_ANCHOR = (
    ' The model is Indian, with South Asian features and warm wheatish skin — this must '
    'hold regardless of the location behind her.'
)


def variants(product_url: str, face_url: str) -> dict:
    return {
        # control: what the gallery did — product image only, face as text
        'text-only': ([product_url], False),
        # face image added as a second reference
        'face-image': ([product_url, face_url], False),
        # face image plus ethnicity restated after the location description
        'face-image-anchored': ([product_url, face_url], True),
    }


def main() -> None:
    cast = json.loads(pathlib.Path('assets/cast/cast.json').read_text())
    face_path = cast[MODEL_KEY]['file']

    product_url = hf.upload(PRODUCT_PHOTO)
    face_url = hf.upload(face_path)
    print(f'product -> {product_url}\nface    -> {face_url}\n')

    for name, (urls, anchored) in variants(product_url, face_url).items():
        _prompt, arguments = shoot.build(urls, MODEL_KEY, LOCATION_KEY, CONTROL)
        if anchored:
            arguments['prompt'] += LATE_ANCHOR

        estimate = hf.estimate(f'/{shoot.MODEL_PATH}', arguments)
        print(f'{name}: {len(urls)} ref(s), {estimate["credits"]} credits')

        result = higgsfield_client.subscribe(shoot.MODEL_PATH, arguments=arguments)
        output = hf.output_urls(result)
        if not output:
            print(f'  FAILED status={result.get("status")}')
            continue
        for saved in hf.download(output, 'out/identity', prefix=name):
            print(f'  {saved}')


if __name__ == '__main__':
    main()
