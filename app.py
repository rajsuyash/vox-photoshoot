"""Vox Photo-Shoot demo server.

Three steps for the client: upload the piece, pick a model and a location, generate.
Everything a photographer would decide is preset in locations.py.

    .venv/bin/uvicorn app:app --reload --port 8000
"""

import dataclasses
import json
import pathlib
import shutil
import threading
import uuid

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import locations
import product
import providers
import shoot
import storage

UPLOADS = pathlib.Path('out/uploads')
SHOOTS = pathlib.Path('out/shoots')
STATIC = pathlib.Path('static')

app = FastAPI(title='Vox Photo-Shoot')

# In-memory job store. Fine for a demo; on AWS this becomes a DynamoDB item keyed by
# job id, and the worker becomes a Lambda triggered by the Higgsfield webhook.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def set_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


@app.get('/healthz')
def healthz():
    """App Runner polls this. It checks the cast is present, because a container that
    boots without assets serves an empty picker and looks broken rather than dead.
    """
    cast = shoot.load_cast()
    return {'ok': True, 'provider': providers.get().name,
            'models': len(cast), 'locations': len(locations.ALL)}


@app.get('/api/models')
def list_models():
    cast = shoot.load_cast()
    return [
        {'key': key, 'description': entry['description'],
         'image': f'/media/{entry["file"]}'}
        for key, entry in cast.items()
    ]


@app.get('/api/locations')
def list_locations():
    manifest_path = pathlib.Path('assets/locations/gallery.json')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    return [
        {
            'key': key,
            'label': location.label,
            'region': location.region,
            'image': f'/media/{manifest[key]["file"]}' if key in manifest else None,
        }
        for key, location in locations.ALL.items()
    ]


def framing_of(path) -> str:
    """'.../kavya-kerala-backwaters-hero-0.png' -> 'hero'.

    Takes the FILE, never the URL: presigned S3 links carry a query string, so parsing
    a framing back out of a URL is a trap.
    """
    return pathlib.Path(path).stem.rsplit('-', 1)[0].rsplit('-', 1)[-1]


def merge_images(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """A reshoot replaces only the framings it regenerated, keeping the rest."""
    regenerated = {image['framing'] for image in fresh}
    kept = [image for image in existing if image['framing'] not in regenerated]
    order = list(locations.FRAMINGS)
    return sorted(
        kept + fresh,
        key=lambda image: order.index(image['framing'])
        if image['framing'] in order else len(order),
    )


def run_shoot(job_id: str, product_path: pathlib.Path, model_key: str,
              location_key: str, description: str, category, framings=None,
              options=None) -> None:
    try:
        saved, failures = shoot.shoot([product_path], model_key, location_key,
                                      description, category, framings=framings,
                                      out_dir=SHOOTS / job_id, options=options)
        if not saved:
            raise RuntimeError(
                '; '.join(f'{framing}: {reason}' for framing, reason in failures)
                or 'generation returned no images'
            )
        with JOBS_LOCK:
            existing = list(JOBS.get(job_id, {}).get('images') or [])
        # Copied off the container before anyone can lose them to a restart.
        fresh = [
            {'framing': framing_of(path),
             'url': storage.put(path, f'shoots/{job_id}/{path.name}')}
            for path in saved
        ]
        set_job(job_id, status='completed', images=merge_images(existing, fresh),
                # A partial shoot must say so — the UI offers a reshoot per frame.
                warnings=[f'{framing} could not be generated ({reason})'
                          for framing, reason in failures])
    except Exception as error:
        # Surfaced to the UI rather than swallowed — a silent spinner is worse than a
        # visible failure during a live client demo.
        set_job(job_id, status='failed', error=str(error))


def category_spec(category) -> dict:
    """What the client has to be asked for, once we know what the piece is.

    Only size is universal. Everything else varies: a ring needs a finger and a hand,
    earrings need a sub-type because a stud and an ear cuff sit nowhere near each other,
    and a necklace needs neither.
    """
    return {
        'key': category.key,
        'label': category.label,
        'asks': list(category.asks),
        'scale_label': category.scale_label,
        'sizes': list(product.SIZES),
        'types': product.types_for(category.key),
        'default_type': category.default_type,
    }


@app.get('/api/categories')
def list_categories():
    """Lets the UI re-render its controls when the client corrects the category."""
    return [category_spec(c) for c in product.CATEGORIES.values()]


def piece_path(piece_id: str) -> pathlib.Path:
    matches = sorted(UPLOADS.glob(f'{piece_id}.*'))
    if not matches:
        raise HTTPException(410, 'that upload is no longer available — upload it again')
    return matches[0]


@app.post('/api/pieces')
async def create_piece(upload: UploadFile):
    """Step one: take the photo and read it.

    Split out from the shoot itself because the answer decides which controls the client
    is shown — you cannot ask for a ring's finger before you know it is a ring. The file
    stays on disk under the returned id so the shoot does not re-upload it.
    """
    piece_id = uuid.uuid4().hex[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(upload.filename or 'upload.jpg').suffix or '.jpg'
    path = UPLOADS / f'{piece_id}{suffix}'
    with path.open('wb') as handle:
        shutil.copyfileobj(upload.file, handle)

    piece = product.identify(path)
    # detected is returned, not just logged: a shoot built on the fallback is a shoot
    # of the wrong piece, and the client should see that before the images do.
    return {'piece_id': piece_id, 'category': piece.category.key, 'type': piece.type,
            'description': piece.description, 'detected': piece.detected,
            'spec': category_spec(piece.category)}


@app.post('/api/shoots')
def create_shoot(
    background: BackgroundTasks,
    piece_id: str = Form(...),
    model_key: str = Form(...),
    location_key: str = Form(...),
    # Detection prefills these and the client may correct them, so they arrive from the
    # browser rather than being re-read here.
    category: str = Form(...),
    description: str = Form(...),
    # Unknowable from the photograph. Size is the one that decides whether the piece
    # looks like the client's piece or merely like a plausible one.
    size: str = Form(product.DEFAULT_SIZE),
    type: str = Form(''),
    finger: str = Form('ring'),
    hand: str = Form('right'),
    instructions: str = Form(''),
):
    if model_key not in shoot.load_cast():
        raise HTTPException(400, f'unknown model {model_key}')
    if location_key not in locations.ALL:
        raise HTTPException(400, f'unknown location {location_key}')
    if category not in product.CATEGORIES:
        raise HTTPException(400, f'unknown category {category}')
    if not description.strip():
        raise HTTPException(400, 'the piece needs a description')

    product_path = piece_path(piece_id)
    chosen = product.CATEGORIES[category]
    # Options validates its own fields against the category, so an out-of-range size or
    # a finger on a necklace falls back to the default instead of 400ing a paid shoot.
    options = product.Options(size=size, type=type, finger=finger, hand=hand,
                              instructions=instructions.strip())

    job_id = uuid.uuid4().hex[:12]
    set_job(job_id, status='running', images=[], error=None,
            model=model_key, location=location_key, piece=piece_id,
            category=category, description=description.strip(), options=options)
    background.add_task(run_shoot, job_id, product_path, model_key, location_key,
                        description.strip(), chosen, None, options)
    return {'job_id': job_id, 'status': 'running',
            'expected': len(locations.FRAMINGS)}


@app.post('/api/shoots/{job_id}/reshoot')
def reshoot(job_id: str, background: BackgroundTasks, framing: str):
    """Regenerate a single frame.

    Identity does not hold perfectly across framings, and the model occasionally invents
    a necklace or nose stud. Rather than pretend otherwise, let the user rerun the one
    bad frame for about 1.5 credits instead of discarding the whole shoot.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, 'no such shoot')
    if framing not in locations.FRAMINGS:
        raise HTTPException(400, f'unknown framing {framing}')

    # Reuse everything the original shoot was built from — the detected piece and the
    # client's own size and placement choices. A reshoot that changes any of that is
    # not a reshoot, it is a different photograph.
    category = product.CATEGORIES.get(job.get('category'), product.DEFAULT_CATEGORY)
    description = job.get('description') or product.DEFAULT_PRODUCT
    options = job.get('options') or product.Options()

    set_job(job_id, status='running', error=None)
    background.add_task(run_shoot, job_id, piece_path(job['piece']), job['model'],
                        job['location'], description, category, [framing], options)
    return {'job_id': job_id, 'status': 'running', 'framing': framing}


@app.get('/api/shoots/{job_id}')
def get_shoot(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, 'no such shoot')
    # The job holds a product.Options so a reshoot can reuse it; JSON cannot. Sent as a
    # plain dict rather than dropped, because it is what the shoot was actually built
    # from and the client should be able to see it.
    options = job.get('options')
    return JSONResponse({'job_id': job_id, **job,
                         'options': dataclasses.asdict(options) if options else None})


@app.get('/media/{path:path}')
def media(path: str):
    # Confined to the project directory so a crafted path cannot escape it.
    root = pathlib.Path.cwd().resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise HTTPException(404, 'not found')
    return FileResponse(resolved)


app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
