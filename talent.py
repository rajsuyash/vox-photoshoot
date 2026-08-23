"""A workspace's own models: described from scratch, or normalised from a photograph.

The thirty in cast.py are the house cast. A jeweller who has a brand ambassador, or who
sells to a customer the house cast does not look like, needs their own — and the shoot
pipeline already takes a face as a reference image, so the only thing missing was a
place to keep one.

WHY AN UPLOAD IS NEVER USED RAW:

A photograph of a real person is wearing her own earrings, her own neckline and her own
lighting. Every one of those bleeds into the shot of the customer's piece — a face
reference wearing jewellery is the exact failure the built-in cast brief was written to
avoid, twice (see LEARNINGS, "the phantom necklace"). So an upload is passed through
the same brief the cast was built with before it becomes a reference: jewellery off,
plain blush top, bare ears and neck, campaign lighting. That costs one generation, and
it is the difference between a usable reference and a contaminated one.

Uploads also carry a consent attestation. Reproducing a person's likeness needs their
express permission under both providers' terms, and a brand ambassador with a signed
release is a different thing from a photograph off the internet.

    .venv/bin/python talent.py        # self-check against the live database
"""

import pathlib
import uuid

import cast
import db
import providers
import storage

KEY_PREFIX = 'talent/'
SOURCES = ('generated', 'uploaded')

# Where the local cache of a fetched portrait lives. Same reasoning as out/uploads: the
# provider needs a file to upload, and the container's disk is a cache, not storage.
LOCAL = pathlib.Path('out/talent')

# What the customer fills in. Everything else about the portrait — the lighting, the
# blush top, the bare ears — comes from cast.EDITORIAL_BRIEF, which is why a described
# model is usable as a reference and a raw photograph is not.
FIELDS = ('age', 'skin', 'origin', 'hair', 'build')


def describe(age: str, skin: str, origin: str, hair: str, build: str = '') -> str:
    """Turn the form into the one sentence locations.compose expects.

    Shaped exactly like a cast.EDITORIAL entry — "a 26 year old Kashmiri model, very
    fair skin, ..." — because it is dropped into the prompt in the same slot and has to
    carry its own opening clause. locations.compose prefixes nothing.
    """
    age = str(age).strip()
    lead = f'a {age} year old {origin.strip()} model' if age else f'a {origin.strip()} model'
    parts = [lead, f'{skin.strip()} skin']
    if hair.strip():
        parts.append(hair.strip())
    if build.strip():
        parts.append(build.strip())
    return ', '.join(part for part in parts if part.strip(' ,'))


def brief(description: str) -> str:
    """The full prompt for one portrait: the customer's description in the house brief.

    Not the customer's words alone. The brief is what forbids jewellery, watermarks and
    bare shoulders, and what asks for the professional-model look the cast was
    regenerated for — a description dropped into an empty prompt produces a snapshot,
    which is what the first version of the built-in cast was and why six of it were
    unusable.
    """
    return cast.EDITORIAL_BRIEF.format(
        description=f'{description}. {cast.EDITORIAL_EXPRESSION}') + cast.EDITORIAL_NEGATIVE


# An uploaded photograph is not described to the model, it is shown to it — so this says
# what to change and what to leave, and says the identity is the part to leave.
NORMALISE = (
    'Photorealistic beauty campaign portrait of the exact woman in the reference '
    'photograph, keeping her face, her bone structure, her skin tone and her hair '
    'exactly as they are — this is the same person, not a similar one. '
    'Restyle only her wardrobe and the setting: she now wears a plain pale blush silk '
    'top with a high round neckline and elbow length sleeves that fully cover both '
    'shoulders and upper arms, on a pale neutral seamless background. '
    'Her earlobes are completely bare and empty, her neck is bare, and she wears '
    'absolutely nothing on her ears, neck, nose or hair. '
    'Polished beauty campaign lighting, soft and directional from just above the lens '
    'with bright clean catchlights in the eyes. '
    'Her expression is calm and composed with a very faint closed-mouth smile, lips '
    'together, looking directly into the lens. '
    'Sharp focus, natural skin texture, 85mm lens at f/2, tightly cropped head and '
    'shoulders filling the frame.'
) + cast.EDITORIAL_NEGATIVE


def portrait(prompt: str, reference: pathlib.Path | None = None) -> pathlib.Path:
    """Generate one portrait and return it on local disk. One image, one credit.

    3:4 to match the built-in cast: these files are read as identity references beside
    the customer's product, and a reference of a different shape crops differently.
    """
    provider = providers.get()
    urls = provider.generate(
        prompt,
        image_urls=[provider.upload(reference)] if reference else None,
        aspect_ratio=cast.ARGUMENTS['aspect_ratio'], quality='high', num_images=1)
    if not urls:
        raise RuntimeError('the provider returned no image')
    import hf
    LOCAL.mkdir(parents=True, exist_ok=True)
    [path] = hf.download(urls, LOCAL, prefix=uuid.uuid4().hex[:12])
    return path


def create(workspace_id: str, user_id: str, name: str, description: str,
           path: pathlib.Path, source: str) -> dict:
    """Store a finished portrait against the workspace. Returns the row."""
    if source not in SOURCES:
        raise ValueError(f'unknown source {source!r}; use one of {SOURCES}')
    talent_id = uuid.uuid4().hex[:12]
    key = f'{KEY_PREFIX}{talent_id}{path.suffix or ".png"}'
    storage.put(path, key)
    return db.query(
        """INSERT INTO talent (id, workspace_id, user_id, name, description,
                               s3_key, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
             RETURNING *""",
        (talent_id, workspace_id, user_id, name.strip() or 'Untitled',
         description.strip(), key, source), one=True)


def owned(talent_id: str, workspace_id: str) -> dict | None:
    """The model, if this workspace owns it. None otherwise — including "never seen"."""
    return db.query(
        """SELECT * FROM talent
            WHERE id = %s AND workspace_id = %s AND archived_at IS NULL""",
        (talent_id, workspace_id), one=True)


def recent(workspace_id: str, limit: int = 200) -> list[dict]:
    """The workspace's models, most recently shot with first."""
    return db.query(
        """SELECT * FROM talent
            WHERE workspace_id = %s AND archived_at IS NULL
            ORDER BY COALESCE(last_used_at, created_at) DESC
            LIMIT %s""",
        (workspace_id, limit))


def touch(talent_id: str, workspace_id: str) -> None:
    db.query('UPDATE talent SET last_used_at = now() WHERE id = %s AND workspace_id = %s',
             (talent_id, workspace_id))


def archive(talent_id: str, workspace_id: str) -> bool:
    row = db.query(
        """UPDATE talent SET archived_at = now()
            WHERE id = %s AND workspace_id = %s AND archived_at IS NULL
        RETURNING id""",
        (talent_id, workspace_id), one=True)
    return row is not None


def face(row: dict) -> dict:
    """The shape shoot.build and shoot.shoot want: a description and a local file.

    Fetched from S3 rather than assumed on disk, for the same reason piece_path does it:
    the container that generated the portrait is not the container running the shoot.
    """
    LOCAL.mkdir(parents=True, exist_ok=True)
    local = LOCAL / pathlib.Path(row['s3_key']).name
    if not local.exists():
        storage.fetch(row['s3_key'], local)
    return {'description': row['description'], 'file': str(local)}


def card(row: dict) -> dict:
    """One model as the picker wants it."""
    return {
        'key': row['id'],
        'name': row['name'],
        'description': row['description'],
        'image': storage.presign(row['s3_key']),
        'source': row['source'],
        'mine': True,
        'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
        'last_used_at': (row['last_used_at'].isoformat()
                         if row.get('last_used_at') else None),
    }


def demo() -> None:
    """Round-trip a row through the real table, and check the prompt builders.

    The prompt builders are checked without a database and without spending anything:
    what matters is that the customer's words land inside the house brief rather than
    replacing it, because that is the difference between a reference and a snapshot.
    """
    written = describe('27', 'deep bronze', 'Tamil', 'a long sleek ponytail')
    assert written.startswith('a 27 year old Tamil model'), written
    assert 'deep bronze skin' in written, written

    full = brief(written)
    assert written in full, 'the description did not survive into the brief'
    assert 'professional fashion model' in full, 'the house brief was not applied'
    assert 'earlobes are completely bare' in full, 'nothing forbade jewellery'
    assert 'no watermark' in full, 'nothing forbade a watermark'
    assert 'same person, not a similar one' in NORMALISE, 'upload may lose identity'
    assert 'earlobes are completely bare' in NORMALISE, 'upload may keep its jewellery'

    import os
    if not os.environ.get('DATABASE_URL'):
        print('talent: prompts ok; DATABASE_URL not set, skipping the table')
        return

    workspace = db.query('SELECT id FROM workspaces LIMIT 1', one=True)
    user = db.query('SELECT id FROM users LIMIT 1', one=True)
    assert workspace and user, 'need one workspace and one user to check against'
    ws, other = str(workspace['id']), '00000000-0000-0000-0000-000000000000'

    row = db.query(
        """INSERT INTO talent (id, workspace_id, user_id, name, description,
                               s3_key, source)
                VALUES ('zztalent0001', %s, %s, 'Check', %s, 'talent/zz.png', 'generated')
             RETURNING *""", (ws, str(user['id']), written), one=True)
    assert owned('zztalent0001', ws), 'the owner cannot see their own model'
    assert owned('zztalent0001', other) is None, 'a model leaked across workspaces'
    assert any(t['id'] == 'zztalent0001' for t in recent(ws)), 'missing from the list'
    assert card(row)['mine'] is True
    touch('zztalent0001', ws)
    assert owned('zztalent0001', ws)['last_used_at'], 'touch did not stamp last use'
    assert archive('zztalent0001', ws), 'archive found nothing to archive'
    assert owned('zztalent0001', ws) is None, 'an archived model is still visible'
    assert not archive('zztalent0001', ws), 'archive is not idempotent'

    db.query("DELETE FROM talent WHERE id = 'zztalent0001'")
    print('talent: prompts, scope, list, touch and archive all behave')


if __name__ == '__main__':
    demo()
