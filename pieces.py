"""The product library: every photograph a workspace has uploaded, kept and reusable.

A jeweller shoots the same forty SKUs over and over — a new location this season, a
different model next quarter. Before this, each of those was a fresh upload of a
photograph the system already had, because the piece id came back to one browser tab and
was never written down anywhere a person could get at it again.

Two things follow from a piece having an owner:

  A shoot can start from the library instead of from a file picker.

  /api/shoots can check that the piece belongs to the workspace paying for the shoot. It
  could not before: piece_id arrived as free text and was resolved straight to a path.

    .venv/bin/python pieces.py        # self-check against the live database
"""

import pathlib

import db
import storage

# Where /api/pieces puts an upload. One definition, because the library resolves keys
# for rows recovered from job history and must look in the same place the writer used.
KEY_PREFIX = 'uploads/'


def create(piece_id: str, workspace_id: str, user_id: str, s3_key: str,
           category: str, type: str = '', description: str = '', sku: str = '') -> None:
    """Record an upload. Idempotent: a repeated id updates what was read off the photo.

    The same file uploaded twice is two pieces, deliberately — the customer may have
    photographed the ring again because the first shot was poor, and the second is not a
    correction of the first, it is a different photograph.
    """
    db.query(
        """INSERT INTO pieces (id, workspace_id, user_id, s3_key, category, type,
                               description, sku)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO UPDATE
                   SET category = EXCLUDED.category, type = EXCLUDED.type,
                       description = EXCLUDED.description""",
        (piece_id, workspace_id, user_id, s3_key, category, type,
         description.strip(), sku.strip() or None))


def owned(piece_id: str, workspace_id: str) -> dict | None:
    """The piece, if this workspace owns it. None otherwise — including "never seen".

    Callers treat None as "not yours", which is also the right answer for an id that was
    uploaded before this table existed and never reached a shoot: it is unrecoverable
    either way, and telling a stranger which of the two it is leaks the id space.
    """
    return db.query(
        """SELECT * FROM pieces
            WHERE id = %s AND workspace_id = %s AND archived_at IS NULL""",
        (piece_id, workspace_id), one=True)


def recent(workspace_id: str, limit: int = 200) -> list[dict]:
    """The library, most recently useful first.

    Ordered by last use rather than upload: the piece shot this morning belongs above one
    uploaded a year ago and never touched.
    """
    return db.query(
        """SELECT * FROM pieces
            WHERE workspace_id = %s AND archived_at IS NULL
            ORDER BY COALESCE(last_used_at, created_at) DESC
            LIMIT %s""",
        (workspace_id, limit))


def touch(piece_id: str, workspace_id: str, sku: str = '') -> None:
    """Mark a piece as used, and take the SKU if the shoot carried one.

    The SKU is typed at shoot time, not at upload time, which is the only moment the
    customer names the piece at all. Without this the library would show every product
    under its description forever, even for someone who codes every SKU they own.
    COALESCE keeps an existing name: a later shoot left blank does not erase it.
    """
    db.query(
        """UPDATE pieces
              SET last_used_at = now(), sku = COALESCE(NULLIF(%s, ''), sku)
            WHERE id = %s AND workspace_id = %s""",
        (sku.strip(), piece_id, workspace_id))


def archive(piece_id: str, workspace_id: str) -> bool:
    """Hide a piece from the library. The file and any shoots made from it stay.

    Deleting the row would orphan every job that points at it, and the images a customer
    paid for are the last thing that should disappear because they tidied a list.
    """
    row = db.query(
        """UPDATE pieces SET archived_at = now()
            WHERE id = %s AND workspace_id = %s AND archived_at IS NULL
        RETURNING id""",
        (piece_id, workspace_id), one=True)
    return row is not None


def key_for(piece: dict) -> str | None:
    """The S3 key of the original, resolving and remembering it for recovered rows.

    Rows backfilled from job history have no s3_key: the file extension is not knowable
    from SQL. Rather than carry that unknown forever, the first read looks the key up and
    writes it back, so the S3 call happens once per legacy piece and never again.
    """
    if piece.get('s3_key'):
        return piece['s3_key']
    found = storage.find(f'{KEY_PREFIX}{piece["id"]}.')
    if found:
        db.query('UPDATE pieces SET s3_key = %s WHERE id = %s', (found, piece['id']))
    return found


def card(piece: dict) -> dict:
    """One piece as the picker wants it: a name, what it is, and somewhere to see it.

    The name is the customer's own SKU when they gave one. They all have a coding scheme
    already and none of them want a second one — so when there is no SKU the fallback is
    the description, not an invented "Product #4".
    """
    key = key_for(piece)
    name = (piece.get('sku') or '').strip() or (piece.get('description') or '').strip()
    return {
        'piece_id': piece['id'],
        'name': name or piece['category'].title(),
        'sku': piece.get('sku') or '',
        'category': piece['category'],
        'type': piece.get('type') or '',
        'description': piece.get('description') or '',
        'image': storage.presign(key) if key else None,
        'created_at': piece['created_at'].isoformat() if piece.get('created_at') else None,
        'last_used_at': (piece['last_used_at'].isoformat()
                         if piece.get('last_used_at') else None),
    }


def suffix_of(filename: str | None) -> str:
    """The extension to store an upload under, defaulting rather than raising."""
    return pathlib.Path(filename or 'upload.jpg').suffix or '.jpg'


def demo() -> None:
    """Round-trip one piece through the real table, then remove it.

    Against the live database on purpose: the parts worth checking are the ON CONFLICT
    and the workspace scoping, and neither of those exists in a mock.
    """
    workspace = db.query('SELECT id FROM workspaces LIMIT 1', one=True)
    user = db.query('SELECT id FROM users LIMIT 1', one=True)
    assert workspace and user, 'need one workspace and one user to check against'
    ws, other = str(workspace['id']), '00000000-0000-0000-0000-000000000000'

    create('zzcheck00001', ws, str(user['id']), 'uploads/zzcheck00001.jpg',
           'ring', description='a check')
    assert owned('zzcheck00001', ws), 'the owner cannot see their own piece'
    assert owned('zzcheck00001', other) is None, 'a piece leaked across workspaces'

    create('zzcheck00001', ws, str(user['id']), 'uploads/zzcheck00001.jpg',
           'earrings', description='read again')
    again = owned('zzcheck00001', ws)
    assert again['category'] == 'earrings', 'a repeated upload did not update the read'

    assert any(p['id'] == 'zzcheck00001' for p in recent(ws)), 'missing from the library'
    touch('zzcheck00001', ws)
    assert owned('zzcheck00001', ws)['last_used_at'], 'touch did not stamp last use'

    assert archive('zzcheck00001', ws), 'archive found nothing to archive'
    assert owned('zzcheck00001', ws) is None, 'an archived piece is still visible'
    assert not archive('zzcheck00001', ws), 'archive is not idempotent'

    db.query('DELETE FROM pieces WHERE id = %s', ('zzcheck00001',))
    print('pieces: create, scope, upsert, list, touch and archive all behave')


if __name__ == '__main__':
    demo()
