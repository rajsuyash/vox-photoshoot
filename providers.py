"""Image generation backends, behind one interface.

Higgsfield and fal expose the same shape (upload a reference, submit a prompt, poll a
queue) but different model names, aspect vocabularies and resolution tiers. Everything
above this file works in the common vocabulary and never names a provider.

    from providers import get
    provider = get('fal')
    urls = provider.generate(prompt, image_urls=[...], aspect_ratio='4:5')
"""

import os
from dataclasses import dataclass
from typing import Callable

import hf

# Common vocabulary. Providers translate; callers only ever use these.
QUALITY = ('standard', 'high', 'max')


def _ratio_value(ratio: str) -> float:
    width, height = ratio.split(':')
    return int(width) / int(height)


@dataclass(frozen=True)
class Provider:
    name: str
    aspect_ratios: frozenset
    upload: Callable
    _generate: Callable

    def generate(self, prompt: str, image_urls=None, aspect_ratio: str = '3:4',
                 quality: str = 'high', seed=None, num_images: int = 1) -> list[str]:
        if aspect_ratio not in self.aspect_ratios:
            raise ValueError(
                f'{self.name} does not support {aspect_ratio}; '
                f'has {sorted(self.aspect_ratios)}'
            )
        if quality not in QUALITY:
            raise ValueError(f'unknown quality {quality!r}; use one of {QUALITY}')
        return self._generate(prompt, list(image_urls or []), aspect_ratio,
                              quality, seed, num_images)

    def nearest_aspect(self, width: int, height: int) -> str:
        """Closest supported ratio to an image we did not choose the shape of.

        Retouching returns the client's own photograph, so the output has to keep the
        shape they shot — forcing 3:4 on a square product shot crops the piece or pads
        it. Providers only accept a fixed vocabulary, so this picks the closest.
        """
        if width <= 0 or height <= 0:
            raise ValueError(f'bad image size {width}x{height}')
        target = width / height
        return min(
            self.aspect_ratios,
            key=lambda ratio: abs(target - _ratio_value(ratio)),
        )


# --- Higgsfield -------------------------------------------------------------------
# popcorn/auto was the only endpoint on the account that preserved the client's product.

HIGGSFIELD_ASPECTS = frozenset({'1:1', '4:3', '3:4', '3:2', '2:3', '16:9', '9:16'})
HIGGSFIELD_RESOLUTION = {'standard': '720p', 'high': '1600p', 'max': '1600p'}


def _higgsfield_generate(prompt, image_urls, aspect_ratio, quality, seed, num_images):
    import higgsfield_client

    arguments = {
        'prompt': prompt,
        'num_images': num_images,
        'resolution': HIGGSFIELD_RESOLUTION[quality],
        'aspect_ratio': aspect_ratio,
    }
    if image_urls:
        arguments['image_urls'] = image_urls
    if seed is not None:
        arguments['seed'] = seed
    return hf.output_urls(
        higgsfield_client.subscribe('higgsfield-ai/popcorn/auto', arguments=arguments)
    )


# --- fal --------------------------------------------------------------------------
# Nano Banana Pro, which Higgsfield's own API returns 404 for. The /edit endpoint takes
# reference images; the base endpoint is text-only.

FAL_ASPECTS = frozenset({'21:9', '16:9', '3:2', '4:3', '5:4', '1:1',
                         '4:5', '3:4', '2:3', '9:16'})
FAL_RESOLUTION = {'standard': '1K', 'high': '2K', 'max': '4K'}


def _fal_credentials() -> None:
    key = os.environ.get('FAL_KEY')
    if not key:
        raise RuntimeError('FAL_KEY not set')
    os.environ.setdefault('FAL_KEY', key)


def _fal_generate(prompt, image_urls, aspect_ratio, quality, seed, num_images):
    import fal_client

    _fal_credentials()
    model = ('fal-ai/nano-banana-pro/edit' if image_urls
             else 'fal-ai/nano-banana-pro')
    arguments = {
        'prompt': prompt,
        'num_images': num_images,
        'aspect_ratio': aspect_ratio,
        'resolution': FAL_RESOLUTION[quality],
        'output_format': 'png',
    }
    if image_urls:
        arguments['image_urls'] = image_urls
    if seed is not None:
        arguments['seed'] = seed

    result = fal_client.subscribe(model, arguments=arguments)
    return [image['url'] for image in result.get('images', [])]


def _fal_upload(path) -> str:
    import fal_client

    _fal_credentials()
    return fal_client.upload_file(str(path))


PROVIDERS = {
    'higgsfield': Provider(
        name='higgsfield',
        aspect_ratios=HIGGSFIELD_ASPECTS,
        upload=hf.upload,
        _generate=_higgsfield_generate,
    ),
    'fal': Provider(
        name='fal',
        aspect_ratios=FAL_ASPECTS,
        upload=_fal_upload,
        _generate=_fal_generate,
    ),
}


def get(name: str | None = None) -> Provider:
    """Resolve a provider by name, or from PROVIDER in the environment.

    Defaults to fal: on the same client product it held the earring, kept identity
    across framings, invented no necklace, and offers 4:5. Higgsfield stays available
    as a fallback via PROVIDER=higgsfield.
    """
    name = name or os.environ.get('PROVIDER', 'fal')
    if name not in PROVIDERS:
        raise KeyError(f'unknown provider {name!r}; have {sorted(PROVIDERS)}')
    return PROVIDERS[name]


def demo() -> None:
    assert get('fal').name == 'fal'
    assert get('higgsfield').name == 'higgsfield'

    # 4:5 is the Instagram portrait ratio: fal has it, Higgsfield does not. The
    # difference must surface as a clear error, not a silent substitution.
    assert '4:5' in get('fal').aspect_ratios
    assert '4:5' not in get('higgsfield').aspect_ratios
    try:
        get('higgsfield').generate('x', aspect_ratio='4:5')
    except ValueError as error:
        assert '4:5' in str(error)
    else:
        raise AssertionError('unsupported aspect ratio should be rejected')

    for bad in ('nope', 'FAL'):
        try:
            get(bad)
        except KeyError:
            pass
        else:
            raise AssertionError(f'{bad} should be rejected')

    try:
        get('fal').generate('x', quality='ultra')
    except ValueError as error:
        assert 'ultra' in str(error)
    else:
        raise AssertionError('unknown quality should be rejected')

    # Retouching hands back the client's own photograph, so its shape has to survive.
    fal = get('fal')
    assert fal.nearest_aspect(1000, 1000) == '1:1'
    assert fal.nearest_aspect(1200, 1600) == '3:4'
    assert fal.nearest_aspect(1600, 1200) == '4:3'
    assert fal.nearest_aspect(1080, 1920) == '9:16'
    # A shape nobody supports still has to resolve to something, not blow up.
    assert fal.nearest_aspect(1124, 1020) in fal.aspect_ratios
    for bad in ((0, 100), (100, -1)):
        try:
            fal.nearest_aspect(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{bad} should have been rejected')

    print('providers ok')


if __name__ == '__main__':
    demo()
