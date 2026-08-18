"""Where generated images live.

App Runner's container disk is ephemeral: a redeploy or a restart loses every shoot a
client has run. With S3_BUCKET set, output is copied to S3 and served by presigned URL;
without it, files stay local and are served by the app. Local is the dev default so the
project still runs with no AWS at all.
"""

import functools
import os
import pathlib

# Presigned links must outlive a demo session without becoming permanent hotlinks.
URL_TTL_SECONDS = 7 * 24 * 3600


def bucket() -> str | None:
    return os.environ.get('S3_BUCKET') or None


@functools.lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))


def put(path, key: str) -> str:
    """Store one file and return a URL the browser can fetch.

    Falls back to the local /media route when S3 is not configured.
    """
    path = pathlib.Path(path)
    target = bucket()
    if not target:
        return f'/media/{path.as_posix()}'

    content_type = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    _client().upload_file(
        str(path), target, key, ExtraArgs={'ContentType': content_type}
    )
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
        assert put('out/shoots/x/y.png', 'ignored') == '/media/out/shoots/x/y.png'
    finally:
        if previous is not None:
            os.environ['S3_BUCKET'] = previous

    # An empty value is not a bucket.
    os.environ['S3_BUCKET'] = ''
    assert bucket() is None
    os.environ.pop('S3_BUCKET')
    print('storage ok')


if __name__ == '__main__':
    demo()
