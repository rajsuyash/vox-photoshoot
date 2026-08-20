"""Where generated images live.

App Runner's container disk is ephemeral: a redeploy or a restart loses every shoot a
client has run. With S3_BUCKET set, output is copied to S3 and served by presigned URL;
without it, files stay local and are served by the app. Local is the dev default so the
project still runs with no AWS at all.

put() returns the KEY, never a URL, and presign() mints a URL at read time. Storing the
URL was a bug: a presigned link is only valid while the credentials that signed it are,
and on App Runner those are instance-role STS credentials that rotate in hours. The old
seven-day TTL was a promise the platform could not keep, so links died mid-week on work
the client had paid for. The key is permanent; the URL is minted fresh on every read.
"""

import functools
import os
import pathlib

# Deliberately short. The real ceiling is the lifetime of the instance-role session that
# signs the URL, not this number — asking for longer than the session does not extend it.
# Every read path re-presigns, so a short TTL costs nothing.
URL_TTL_SECONDS = 6 * 3600

# Local dev keeps files under out/, and the S3 key is that path minus the prefix, so
# out/<key> and <key> address the same bytes in both modes.
LOCAL_ROOT = pathlib.Path('out')


def bucket() -> str | None:
    return os.environ.get('S3_BUCKET') or None


@functools.lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))


def put(path, key: str) -> str:
    """Store one file and return its key. Never returns a URL — see the module docstring."""
    path = pathlib.Path(path)
    target = bucket()
    if not target:
        # Already on local disk at out/<key>; nothing to copy.
        return key

    content_type = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    _client().upload_file(
        str(path), target, key, ExtraArgs={'ContentType': content_type}
    )
    return key


def presign(key: str) -> str:
    """A URL the browser can fetch right now. Call this at read time, not at write time."""
    target = bucket()
    if not target:
        return f'/media/{(LOCAL_ROOT / key).as_posix()}'
    return _client().generate_presigned_url(
        'get_object',
        Params={'Bucket': target, 'Key': key},
        ExpiresIn=URL_TTL_SECONDS,
    )


def demo() -> None:
    # Without S3_BUCKET the app must keep working, serving from its own /media route.
    previous = os.environ.pop('S3_BUCKET', None)
    try:
        assert bucket() is None
        # put() returns the key in both modes, so callers never branch on storage.
        key = 'shoots/job1/hero-1.png'
        assert put('out/shoots/job1/hero-1.png', key) == key
        # and out/<key> is where that file actually is on disk.
        assert presign(key) == '/media/out/shoots/job1/hero-1.png'
    finally:
        if previous is not None:
            os.environ['S3_BUCKET'] = previous

    # An empty value is not a bucket.
    os.environ['S3_BUCKET'] = ''
    assert bucket() is None
    os.environ.pop('S3_BUCKET')

    # A URL must never be storable as a key: the whole point of the split is that what
    # we persist outlives the credentials that sign it.
    assert 'http' not in put('out/x/y.png', 'x/y.png')
    assert URL_TTL_SECONDS <= 12 * 3600, 'longer than an instance-role session can sign'
    print('storage ok')


if __name__ == '__main__':
    demo()
