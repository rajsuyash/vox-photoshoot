"""Higgsfield image generation smoke test / starter.

Usage:
    export HF_KEY="api-key-id:api-key-secret"   # or put it in .env
    .venv/bin/python generate.py "your prompt here"
"""

import os
import pathlib
import sys
import urllib.request

import higgsfield_client

APPLICATION = 'higgsfield-ai/soul/standard'
OUT_DIR = pathlib.Path('out')


def load_dotenv(path: str = '.env') -> None:
    # ponytail: 6-line .env reader, swap for python-dotenv if it ever needs quoting/export/interpolation
    if not os.path.exists(path):
        return
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def generate(prompt: str, resolution: str = '1080p', aspect_ratio: str = '4:3') -> list[str]:
    result = higgsfield_client.subscribe(
        APPLICATION,
        arguments={
            'prompt': prompt,
            'resolution': resolution,
            'aspect_ratio': aspect_ratio,
        },
        on_queue_update=lambda status: print(f'  status: {status}', file=sys.stderr),
    )
    return [image['url'] for image in result['images']]


def download(urls: list[str]) -> list[pathlib.Path]:
    OUT_DIR.mkdir(exist_ok=True)
    paths = []
    for index, url in enumerate(urls):
        suffix = pathlib.Path(url.split('?')[0]).suffix or '.jpg'
        path = OUT_DIR / f'shot-{index}{suffix}'
        urllib.request.urlretrieve(url, path)
        paths.append(path)
    return paths


def main() -> None:
    load_dotenv()
    prompt = ' '.join(sys.argv[1:]) or 'Editorial portrait in soft daylight'
    print(f'prompt: {prompt}', file=sys.stderr)
    for path in download(generate(prompt)):
        print(path)


if __name__ == '__main__':
    main()
