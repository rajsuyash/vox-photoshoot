"""Vox Photo-Shoot demo server.

Three steps for the client: upload the piece, pick a model and a location, generate.
Everything a photographer would decide is preset in locations.py.

    .venv/bin/uvicorn app:app --reload --port 8000
"""

import json
import pathlib
import shutil
import threading
import uuid

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import locations
import shoot
from pave_test import CONTROL as DEFAULT_PRODUCT

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


def framing_of(image: str) -> str:
    """'/media/.../kavya-kerala-backwaters-hero-0.png' -> 'hero'."""
    return image.rsplit('/', 1)[-1].rsplit('-', 2)[1]


def merge_images(existing: list[str], fresh: list[str]) -> list[str]:
    """A reshoot replaces only the framings it regenerated, keeping the rest."""
    regenerated = {framing_of(image) for image in fresh}
    kept = [image for image in existing if framing_of(image) not in regenerated]
    order = list(locations.FRAMINGS)
    return sorted(kept + fresh,
                  key=lambda image: order.index(framing_of(image))
                  if framing_of(image) in order else len(order))


def run_shoot(job_id: str, product_path: pathlib.Path, model_key: str,
              location_key: str, product: str, framings=None) -> None:
    try:
        saved, failures = shoot.shoot([product_path], model_key, location_key, product,
                                      framings=framings, out_dir=SHOOTS / job_id)
        if not saved:
            raise RuntimeError(
                '; '.join(f'{framing}: {reason}' for framing, reason in failures)
                or 'generation returned no images'
            )
        with JOBS_LOCK:
            existing = list(JOBS.get(job_id, {}).get('images') or [])
        fresh = [f'/media/{path}' for path in saved]
        set_job(job_id, status='completed', images=merge_images(existing, fresh),
                # A partial shoot must say so — the UI offers a reshoot per frame.
                warnings=[f'{framing} could not be generated ({reason})'
                          for framing, reason in failures])
    except Exception as error:
        # Surfaced to the UI rather than swallowed — a silent spinner is worse than a
        # visible failure during a live client demo.
        set_job(job_id, status='failed', error=str(error))


@app.post('/api/shoots')
async def create_shoot(
    background: BackgroundTasks,
    product: UploadFile,
    model_key: str = Form(...),
    location_key: str = Form(...),
    description: str = Form(DEFAULT_PRODUCT),
):
    if model_key not in shoot.load_cast():
        raise HTTPException(400, f'unknown model {model_key}')
    if location_key not in locations.ALL:
        raise HTTPException(400, f'unknown location {location_key}')

    job_id = uuid.uuid4().hex[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(product.filename or 'upload.jpg').suffix or '.jpg'
    product_path = UPLOADS / f'{job_id}{suffix}'
    with product_path.open('wb') as handle:
        shutil.copyfileobj(product.file, handle)

    set_job(job_id, status='running', images=[], error=None,
            model=model_key, location=location_key)
    background.add_task(run_shoot, job_id, product_path, model_key,
                        location_key, description)
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

    matches = sorted(UPLOADS.glob(f'{job_id}.*'))
    if not matches:
        raise HTTPException(410, 'original upload is no longer available')

    set_job(job_id, status='running', error=None)
    background.add_task(run_shoot, job_id, matches[0], job['model'],
                        job['location'], DEFAULT_PRODUCT, [framing])
    return {'job_id': job_id, 'status': 'running', 'framing': framing}


@app.get('/api/shoots/{job_id}')
def get_shoot(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, 'no such shoot')
    return JSONResponse({'job_id': job_id, **job})


@app.get('/media/{path:path}')
def media(path: str):
    # Confined to the project directory so a crafted path cannot escape it.
    root = pathlib.Path.cwd().resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise HTTPException(404, 'not found')
    return FileResponse(resolved)


app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
