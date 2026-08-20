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
        # Local mode has to honour the same contract S3 does: after put(), the bytes are
        # readable at <key>. Assuming the file was already in the right place was wrong —
        # hf.download names files with its own index suffix, and a reshoot writes into
        # its own job directory, so out/<key> was frequently a path that did not exist
        # and presign() handed back a dead link.
        destination = LOCAL_ROOT / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != path.resolve():
            destination.write_bytes(path.read_bytes())
        return key

    content_type = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    _client().upload_file(
        str(path), target, key, ExtraArgs={'ContentType': content_type}
    )
    return key


def find(prefix: str) -> str | None:
    """The first key under a prefix, or None. The S3 answer to a glob.

    Used to recover an upload whose extension we no longer know, which is what the
    local-disk glob in piece_path did before uploads outlived the container.
    """
    target = bucket()
    if not target:
        matches = sorted(LOCAL_ROOT.glob(f'{prefix}*'))
        return matches[0].relative_to(LOCAL_ROOT).as_posix() if matches else None

    page = _client().list_objects_v2(Bucket=target, Prefix=prefix, MaxKeys=1)
    contents = page.get('Contents') or []
    return contents[0]['Key'] if contents else None


def fetch(key: str, destination) -> pathlib.Path:
    """Bring an object back to local disk so a provider call can read it.

    App Runner's disk is ephemeral, so a piece uploaded before the last deploy is not
    here any more even though the customer's job row still points at it. Without this a
    reshoot of last week's bangle answers 410 forever.
    """
    destination = pathlib.Path(destination)
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    target = bucket()
    if not target:
        source = LOCAL_ROOT / key
        if not source.exists():
            raise FileNotFoundError(key)
        if source.resolve() != destination.resolve():
            destination.write_bytes(source.read_bytes())
        return destination

    _client().download_file(target, key, str(destination))
    return destination


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
        # The source name deliberately differs from the key: hf.download adds its own
        # index suffix and a reshoot writes into its own directory, so local mode has to
        # actually place the bytes rather than assume they are already there.
        source = LOCAL_ROOT / 'selfcheck' / 'hero-1-0.png'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'not really a png')
        key = 'shoots/job1/hero-1.png'
        assert put(source, key) == key
        served = LOCAL_ROOT / key
        assert served.read_bytes() == b'not really a png', 'put() did not place the file'
        assert presign(key) == '/media/out/shoots/job1/hero-1.png'

        # find() is the S3 answer to a glob, and is how an upload is recovered when the
        # extension is not known — the case piece_path hits after a redeploy.
        put(served, 'uploads/abc123.png')
        assert find('uploads/abc123.') == 'uploads/abc123.png'
        assert find('uploads/nothing-here.') is None

        # fetch() must bring it back to a path that does not exist yet. Before this,
        # a reshoot of anything older than the last deploy answered 410 forever.
        gone = LOCAL_ROOT / 'cache' / 'abc123.png'
        gone.unlink(missing_ok=True)
        assert fetch('uploads/abc123.png', gone).read_bytes() == b'not really a png'
        # And must be a no-op when the file is already local, not a re-download.
        gone.write_bytes(b'already here')
        assert fetch('uploads/abc123.png', gone).read_bytes() == b'already here'

        gone.unlink()
        (LOCAL_ROOT / 'uploads' / 'abc123.png').unlink()
        served.unlink()
        source.unlink()
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
