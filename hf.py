"""Thin Higgsfield API client for the photoshoot app.

Wraps the pieces the official SDK gets wrong or does not expose:
  - upload()   the SDK's upload_file sends an empty x-amz-tagging header, which
               breaks the presigned S3 signature. This sends the headers as returned.
  - estimate() not exposed by the SDK at all; costs nothing and gates spend.
Generation itself goes through the SDK's subscribe(), which works fine.
"""

import json
import mimetypes
import os
import pathlib
import time
import urllib.error
import urllib.request

BASE = 'https://platform.higgsfield.ai'
# Cloudflare in front of the API rejects urllib's default User-Agent with "error code: 1010".
UA = 'curl/8.7.1'


def _credentials() -> str:
    key = os.environ.get('HF_KEY')
    if not key:
        raise RuntimeError('HF_KEY not set (expected "<key-id>:<key-secret>")')
    return key


ATTEMPTS = 4


def _request(method: str, url: str, *, body=None, headers=None, auth=True):
    """Upload/estimate/download only.

    Every caller here is safe to retry. Generation submits are NOT routed through this —
    they have no idempotency key, so a blind retry after an ambiguous timeout can pay for
    the same shoot twice.
    """
    merged = {'User-Agent': UA, **(headers or {})}
    if auth:
        merged['Authorization'] = f'Key {_credentials()}'
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        merged.setdefault('Content-Type', 'application/json')

    last_error = None
    for attempt in range(ATTEMPTS):
        request = urllib.request.Request(url, data=body, headers=merged, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                if not raw or 'json' not in response.headers.get('Content-Type', ''):
                    return response.status, None
                return response.status, json.loads(raw)
        except urllib.error.HTTPError as error:
            raw = error.read()
            if error.code >= 500 and attempt < ATTEMPTS - 1:
                last_error = error
                time.sleep(2 ** attempt)
                continue
            try:
                return error.code, json.loads(raw or b'{}')
            except json.JSONDecodeError:
                return error.code, {'detail': raw.decode(errors='replace')[:400]}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            # Connection reset mid-upload is common enough to be worth surviving.
            last_error = error
            if attempt == ATTEMPTS - 1:
                break
            time.sleep(2 ** attempt)

    raise RuntimeError(f'{method} {url.split("?")[0]} failed after '
                       f'{ATTEMPTS} attempts: {last_error}')


def upload(path) -> str:
    """Upload a local file, return the public URL to pass as image_url/image_urls."""
    path = pathlib.Path(path)
    content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'

    status, ticket = _request(
        'POST', f'{BASE}/files/generate-upload-url', body={'content_type': content_type}
    )
    if status != 200:
        raise RuntimeError(f'generate-upload-url failed ({status}): {ticket}')

    # Every header from upload_headers must be replayed verbatim or the signature fails.
    status, error = _request(
        'PUT',
        ticket['upload_url'],
        body=path.read_bytes(),
        headers={**ticket['upload_headers'], 'User-Agent': UA},
        auth=False,
    )
    if status not in (200, 201):
        raise RuntimeError(f'S3 upload failed ({status}): {error}')

    return ticket['public_url']


def estimate(model_path: str, arguments: dict) -> dict:
    """Cost preview. Spends nothing — call before any generation."""
    status, body = _request('POST', f'{BASE}/estimate{model_path}', body=arguments)
    if status != 200:
        raise RuntimeError(f'estimate failed ({status}): {body}')
    return body


def download(urls, directory, prefix: str = 'shot'):
    """Save generated output locally. Higgsfield deletes it after ~7 days."""
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, url in enumerate(urls):
        suffix = pathlib.Path(url.split('?')[0]).suffix or '.jpg'
        path = directory / f'{prefix}-{index}{suffix}'
        path.write_bytes(_fetch_bytes(url))
        saved.append(path)
    return saved


def _fetch_bytes(url: str) -> bytes:
    last_error = None
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except Exception as error:
            last_error = error
            if attempt == ATTEMPTS - 1:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f'download failed after {ATTEMPTS} attempts: {last_error}')


def output_urls(result: dict) -> list[str]:
    """Pull image URLs out of a completed result, whichever shape it used."""
    if result.get('images'):
        return [image['url'] for image in result['images']]
    if result.get('image'):
        return [result['image']['url']]
    return []


def demo() -> None:
    """Self-check: round-trip an upload and confirm the URL is publicly fetchable."""
    sample = pathlib.Path('out/shot-0.png')
    assert sample.exists(), 'run generate.py first to produce a sample image'

    url = upload(sample)
    assert url.startswith('https://'), url

    status, _ = _request('GET', url, auth=False)
    assert status == 200, f'uploaded file not fetchable: {status}'

    cost = estimate('/higgsfield-ai/soul/standard', {'prompt': 'test'})
    assert 'credits' in cost, cost

    print(f'upload ok -> {url}')
    print(f'estimate ok -> {cost}')


if __name__ == '__main__':
    demo()
