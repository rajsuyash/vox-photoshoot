"""Donna Photoshoot demo server.

Three steps for the client: upload the piece, pick a model and a location, generate.
Everything a photographer would decide is preset in locations.py.

    .venv/bin/uvicorn app:app --reload --port 8000
"""

import dataclasses
import json
import logging
import os
import pathlib
import secrets
import time
import uuid
from urllib.parse import quote

from fastapi import (BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request,
                     Response, UploadFile)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import admin
import auth
import billing
import credits
import db
import jobs
import locations
import oauth_google
import product
import providers
import retouch
import shoot
import storage

# Named, not the root logger: uvicorn owns the root and App Runner ships whatever lands
# on stdout to CloudWatch, which is where the sign-in failures need to be readable.
log = logging.getLogger('donna')

UPLOADS = pathlib.Path('out/uploads')
SHOOTS = pathlib.Path('out/shoots')
RETOUCHES = pathlib.Path('out/retouches')
STATIC = pathlib.Path('static')

# Largest upload accepted, checked while streaming rather than after. Phone photos are
# 3-8MB; this leaves room for a RAW-ish export without letting a 2GB POST fill the
# container's 1GB of memory and take every running shoot down with it.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# The interactive docs enumerate every money-spending endpoint and its exact parameters.
# Nothing needs them in production.
app = FastAPI(title='Donna Photoshoot',
              docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event('startup')
def boot() -> None:
    """Cap concurrency and bring the schema up to date.

    Sync endpoints and BackgroundTasks land on anyio's threadpool, which defaults to 40.
    Forty concurrent shoots on 0.5 vCPU / 1GB is an OOM, not throughput — each holds
    decoded PNGs in memory. Six keeps the box alive; the surplus waits its turn.
    """
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = 6
    db.migrate()
    auth.sweep_sessions()


# Pages anyone may fetch without a session. Everything else redirects to the login page,
# and every /api route additionally carries the current_session dependency — the gate
# below is for humans typing URLs, the dependency is what actually protects the money.
PUBLIC_PATHS = {'/login.html', '/healthz', '/api/auth/login',
                '/api/auth/google', '/api/auth/google/callback',
                '/api/webhooks/razorpay',
                '/favicon.ico', '/favicon-32x32.png', '/apple-touch-icon.png'}


@app.middleware('http')
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith('/api/'):
        return await call_next(request)
    if auth.lookup(request.cookies.get(auth.COOKIE)) is None:
        # Carry the original path so a deep link survives the detour through login.
        return RedirectResponse(f'/login.html?next={path}', status_code=303)
    return await call_next(request)

@app.post('/api/auth/login')
def login(response: Response, email: str = Form(...), password: str = Form(...)):
    user = auth.authenticate(email, password)
    if user is None:
        # One message for both "no such account" and "wrong password", so the endpoint
        # cannot be used to find out which addresses have accounts.
        raise HTTPException(401, 'that email and password do not match')

    spaces = auth.workspaces_for(user['id'])
    _issue_session(response, str(user['id']), spaces[0]['id'] if spaces else None)
    return {'ok': True, 'workspaces': len(spaces)}


def _issue_session(response, user_id: str, workspace_id: str | None) -> None:
    """Mint a session cookie. One place, so password and Google login cannot drift."""
    token = auth.start_session(user_id, workspace_id)
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite='lax',
        # Secure only where TLS exists, or login breaks on http://localhost.
        secure=bool(storage.bucket()))


@app.get('/api/auth/google')
def google_start():
    """Send the browser to Google, remembering a state we can check on the way back."""
    if not oauth_google.configured():
        raise HTTPException(503, 'Google sign-in is not configured on this deployment')

    state = oauth_google.new_state()
    response = RedirectResponse(oauth_google.auth_url(state), status_code=303)
    response.set_cookie(
        oauth_google.STATE_COOKIE, state, max_age=oauth_google.STATE_TTL_SECONDS,
        httponly=True, samesite='lax', secure=bool(storage.bucket()))
    return response


@app.get('/api/auth/google/callback')
def google_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    """Google's return leg. Everything here fails to the login page, never to a 500.

    A stack trace on this route is a stranger's first impression of the product, and the
    interesting failures — a cancelled consent screen, a stale tab, a replayed link —
    are all ordinary rather than exceptional.
    """
    def back(message: str):
        gone = RedirectResponse(f'/login.html?error={quote(message)}', status_code=303)
        gone.delete_cookie(oauth_google.STATE_COOKIE)
        return gone

    if error or not code:
        # Most often the user pressed Cancel. That is not an error worth a message.
        return back('Google sign-in was cancelled')

    # Checked before the code is spent: a callback that did not originate from a request
    # this browser made is a CSRF attempt, and must cost nothing to reject.
    expected = request.cookies.get(oauth_google.STATE_COOKIE)
    if not expected or not secrets.compare_digest(expected, state):
        return back('That sign-in link expired — please try again')

    try:
        claims = oauth_google.exchange(code)
        result = auth.sign_in_with_google(claims)
    except ValueError as problem:
        log.warning('google sign-in refused: %s', problem)
        return back(str(problem))

    # A brand new account lands with ?welcome=N so the app can say what just happened.
    # N is 0 when the daily trial cap is spent, which the banner explains rather than
    # leaving someone staring at an empty balance wondering what they did wrong.
    destination = f'/?welcome={result["granted"]}' if result['created'] else '/'
    response = RedirectResponse(destination, status_code=303)
    response.delete_cookie(oauth_google.STATE_COOKIE)
    _issue_session(response, result['user_id'], result['workspace_id'])
    if result['created']:
        log.info('new account %s, granted %s credits', claims['email'], result['granted'])
    return response


@app.post('/api/auth/logout')
def logout(response: Response, session: dict = Depends(auth.current_session)):
    auth.end_session_by_hash(session['token_hash'])
    response.delete_cookie(auth.COOKIE)
    return {'ok': True}


@app.get('/api/me')
def me(session: dict = Depends(auth.current_session)):
    """Who am I, which workspace am I looking at, and what else could I switch to."""
    return {
        'email': session['email'],
        'name': session['name'],
        'is_admin': session['is_admin'],
        'workspace': ({'id': str(session['workspace_id']),
                       'name': session['workspace_name']}
                      if session['workspace_id'] else None),
        'workspaces': [{'id': str(w['id']), 'name': w['name'], 'role': w['role']}
                       for w in auth.workspaces_for(session['user_id'])],
    }


@app.post('/api/me/workspace')
def switch_workspace(workspace_id: str = Form(...),
                     session: dict = Depends(auth.current_session)):
    """Switch which workspace this session is looking at.

    Checked against membership, not taken on trust — otherwise any logged-in user could
    point their session at any workspace and spend its credits.
    """
    allowed = {str(w['id']) for w in auth.workspaces_for(session['user_id'])}
    if workspace_id not in allowed:
        raise HTTPException(403, 'not a member of that workspace')
    auth.set_session_workspace(session['token_hash'], workspace_id)
    return {'ok': True}


@app.get('/healthz')
def healthz():
    """App Runner polls this. It checks the cast is present, because a container that
    boots without assets serves an empty picker and looks broken rather than dead.
    """
    cast = shoot.load_cast()
    return {'ok': True, 'provider': providers.get().name,
            'models': len(cast), 'locations': len(locations.ALL)}


@app.get('/api/models')
def list_models(session: dict = Depends(auth.current_session)):
    cast = shoot.load_cast()
    return [
        {'key': key, 'description': entry['description'],
         'image': f'/media/{entry["file"]}'}
        for key, entry in cast.items()
    ]


@app.get('/api/locations')
def list_locations(session: dict = Depends(auth.current_session)):
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


def run_shoot(job_id: str, shoot_id: str, product_path: pathlib.Path,
              model_key: str, location_key: str, description: str, category,
              framings=None, options=None) -> None:
    """Generate, persisting each image the moment it lands.

    shoot_id is the gallery this belongs to: for a reshoot it is the parent, so the new
    frame joins the original set instead of starting a lonely one of its own.
    """
    if not jobs.claim(job_id):
        return          # another container already has it

    framings = list(framings or locations.FRAMINGS)
    # Which go this is at each framing. Drives the seed and the storage key, so a
    # reshoot is a different photograph and cannot overwrite the one it replaces.
    attempts = {name: jobs.next_attempt(shoot_id, name) for name in framings}

    def persist(image):
        key = f"shoots/{shoot_id}/{image['framing']}-{image['attempt']}.png"
        jobs.add_image(job_id, shoot_id, image['framing'], image['attempt'],
                       storage.put(image['path'], key), image['seed'])
        # The heartbeat rides on real work, so it cannot tick while the job is wedged.
        jobs.heartbeat(job_id)

    try:
        saved, failures = shoot.shoot(
            [product_path], model_key, location_key, description, category,
            framings=framings, out_dir=SHOOTS / job_id, options=options,
            attempts=attempts, on_image=persist)
    except Exception as error:
        # Surfaced rather than swallowed — a silent spinner is worse than a visible
        # failure during a live client demo.
        credits.settle(job_id, delivered=jobs.image_count(job_id))
        jobs.finish(job_id, 'failed', error=str(error))
        return

    delivered = len(saved)
    credits.settle(job_id, delivered=delivered)
    jobs.finish(job_id, 'succeeded' if delivered else 'failed',
                failures=[[name, reason] for name, reason in failures],
                error=None if delivered else '; '.join(
                    f'{name}: {reason}' for name, reason in failures)
                    or 'generation returned no images',
                settled_credits=delivered)


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
def list_categories(session: dict = Depends(auth.current_session)):
    """Lets the UI re-render its controls when the client corrects the category."""
    return [category_spec(c) for c in product.CATEGORIES.values()]


def save_upload(upload: UploadFile, path: pathlib.Path) -> None:
    """Stream an upload to disk, refusing anything over the cap.

    Checked while copying, not after: a straight copy would happily write the whole 2GB
    first, and we would be out of disk before we could complain about it.
    """
    written = 0
    with path.open('wb') as handle:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                handle.close()
                path.unlink(missing_ok=True)
                raise HTTPException(
                    413, f'that photo is over {MAX_UPLOAD_BYTES // (1024 * 1024)}MB — '
                         f'please upload a smaller one')
            handle.write(chunk)


def piece_path(piece_id: str) -> pathlib.Path:
    matches = sorted(UPLOADS.glob(f'{piece_id}.*'))
    if not matches:
        raise HTTPException(410, 'that upload is no longer available — upload it again')
    return matches[0]


@app.post('/api/pieces')
async def create_piece(upload: UploadFile,
                       session: dict = Depends(auth.current_session)):
    """Step one: take the photo and read it.

    Split out from the shoot itself because the answer decides which controls the client
    is shown — you cannot ask for a ring's finger before you know it is a ring. The file
    stays on disk under the returned id so the shoot does not re-upload it.
    """
    piece_id = uuid.uuid4().hex[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(upload.filename or 'upload.jpg').suffix or '.jpg'
    path = UPLOADS / f'{piece_id}{suffix}'
    save_upload(upload, path)

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
    detected: str = Form('true'),
    # Minted in the browser when the form is completed. Disabling the button is not a
    # control: it does not survive a slow network, a second tab, or refresh-and-resubmit.
    idempotency_key: str = Form(''),
    session: dict = Depends(auth.current_session),
):
    workspace_id = auth.current_workspace(session)
    if model_key not in shoot.load_cast():
        raise HTTPException(400, f'unknown model {model_key}')
    if location_key not in locations.ALL:
        raise HTTPException(400, f'unknown location {location_key}')
    if category not in product.CATEGORIES:
        raise HTTPException(400, f'unknown category {category}')
    if not description.strip():
        raise HTTPException(400, 'the piece needs a description')
    # product.identify never raises; it falls back to the client's earrings and says so.
    # Free when nobody was paying. Now it would charge three credits for photographs of
    # the wrong piece, so the shoot stops here instead.
    if detected.lower() != 'true':
        raise HTTPException(
            422, 'we could not read that photo, so we cannot shoot it — please confirm '
                 'what the piece is, or upload a clearer picture')

    product_path = piece_path(piece_id)
    chosen = product.CATEGORIES[category]
    # Options validates its own fields against the category, so an out-of-range size or
    # a finger on a necklace falls back to the default instead of 400ing a paid shoot.
    options = product.Options(size=size, type=type, finger=finger, hand=hand,
                              instructions=instructions.strip())
    params = {'model': model_key, 'location': location_key, 'category': category,
              'description': description.strip(),
              'options': dataclasses.asdict(options)}

    cost = credits.COST['shoot']
    try:
        # One transaction: the job and the credits it reserved commit together, or
        # neither does. Either half alone is a way to lose money silently.
        with db.tx() as conn:
            job = jobs.create(workspace_id, session['user_id'], 'shoot',
                              idempotency_key or f'shoot:{uuid.uuid4()}', params,
                              piece_id=piece_id, reserved_credits=cost, conn=conn)
            if job['created']:
                credits.reserve(conn, workspace_id, str(job['id']), cost)
    except credits.Insufficient as short:
        raise HTTPException(402, f'not enough credits — {short}')

    job_id = str(job['id'])
    if job['created']:
        background.add_task(run_shoot, job_id, job_id, product_path, model_key,
                            location_key, description.strip(), chosen, None, options)
    return {'job_id': job_id, 'status': 'running',
            'expected': cost, 'balance': credits.balance(workspace_id)}


def run_retouch(job_id: str, path: pathlib.Path, **options) -> None:
    if not jobs.claim(job_id):
        return
    try:
        saved = retouch.run(path, out_dir=RETOUCHES / job_id, **options)
        for index, image in enumerate(saved, start=1):
            key = f'retouches/{job_id}/{image.name}'
            # 'retouch' rather than a framing name: it is one image, and the reshoot
            # endpoint only accepts real framings, so this cannot be re-rolled there.
            jobs.add_image(job_id, job_id, 'retouch', index,
                           storage.put(image, key), None)
        credits.settle(job_id, delivered=len(saved))
        jobs.finish(job_id, 'succeeded' if saved else 'failed',
                    error=None if saved else 'retouch returned no image',
                    settled_credits=len(saved))
    except Exception as error:
        credits.settle(job_id, delivered=jobs.image_count(job_id))
        jobs.finish(job_id, 'failed', error=str(error))


@app.get('/api/retouch-options')
def retouch_options(session: dict = Depends(auth.current_session)):
    return {'modes': list(retouch.MODES), 'default_mode': retouch.DEFAULT_MODE,
            'backgrounds': list(retouch.BACKGROUNDS),
            'default_background': retouch.DEFAULT_BACKGROUND}


@app.post('/api/retouches')
async def create_retouch(
    background_tasks: BackgroundTasks,
    upload: UploadFile,
    mode: str = Form(retouch.DEFAULT_MODE),
    # Off by default, unlike every competitor: inclusions are what make a stone read as
    # a real stone, and this app exists to return the client's actual piece.
    retouch_stones: bool = Form(False),
    background: str = Form(retouch.DEFAULT_BACKGROUND),
    instructions: str = Form(''),
    idempotency_key: str = Form(''),
    session: dict = Depends(auth.current_session),
):
    workspace_id = auth.current_workspace(session)
    if mode not in retouch.MODES:
        raise HTTPException(400, f'unknown mode {mode}')
    if background not in retouch.BACKGROUNDS:
        raise HTTPException(400, f'unknown background {background}')

    piece = uuid.uuid4().hex[:12]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(upload.filename or 'upload.jpg').suffix or '.jpg'
    path = UPLOADS / f'{piece}{suffix}'
    save_upload(upload, path)

    cost = credits.COST['retouch']
    params = {'mode': mode, 'background': background,
              'retouch_stones': retouch_stones, 'instructions': instructions.strip()}
    try:
        with db.tx() as conn:
            job = jobs.create(workspace_id, session['user_id'], 'retouch',
                              idempotency_key or f'retouch:{uuid.uuid4()}', params,
                              piece_id=piece, reserved_credits=cost, conn=conn)
            if job['created']:
                credits.reserve(conn, workspace_id, str(job['id']), cost)
    except credits.Insufficient as short:
        raise HTTPException(402, f'not enough credits — {short}')

    job_id = str(job['id'])
    if job['created']:
        background_tasks.add_task(run_retouch, job_id, path, mode=mode,
                                  retouch_stones=retouch_stones, background=background,
                                  instructions=instructions.strip())
    return {'job_id': job_id, 'status': 'running', 'expected': cost,
            'balance': credits.balance(workspace_id)}


@app.post('/api/shoots/{job_id}/reshoot')
def reshoot(job_id: str, background: BackgroundTasks, framing: str,
            idempotency_key: str = '',
            session: dict = Depends(auth.current_session)):
    """Regenerate a single frame as a job of its own.

    Its own row, pointing at the shoot via parent_job_id. Previously a reshoot wrote its
    status onto the parent, so one framing rejected by content moderation made a
    customer's three good images read "generation failed".
    """
    workspace_id = auth.current_workspace(session)
    parent = jobs.get(job_id, workspace_id)
    if parent is None:
        raise HTTPException(404, 'no such shoot')
    if framing not in locations.FRAMINGS:
        raise HTTPException(400, f'unknown framing {framing}')

    # Reuse everything the original shoot was built from — the detected piece and the
    # client's own size and placement choices. A reshoot that changes any of that is
    # not a reshoot, it is a different photograph.
    params = parent['params'] or {}
    category = product.CATEGORIES.get(params.get('category'), product.DEFAULT_CATEGORY)
    description = params.get('description') or product.DEFAULT_PRODUCT
    options = product.Options(**(params.get('options') or {}))
    product_path = piece_path(parent['piece_id'])

    cost = credits.COST['reshoot']
    try:
        with db.tx() as conn:
            job = jobs.create(workspace_id, session['user_id'], 'reshoot',
                              idempotency_key or f'reshoot:{uuid.uuid4()}',
                              {**params, 'framing': framing},
                              piece_id=parent['piece_id'], reserved_credits=cost,
                              parent_job_id=job_id, conn=conn)
            if job['created']:
                credits.reserve(conn, workspace_id, str(job['id']), cost)
    except credits.Insufficient as short:
        raise HTTPException(402, f'not enough credits — {short}')

    if job['created']:
        background.add_task(run_shoot, str(job['id']), job_id, product_path,
                            params.get('model'), params.get('location'), description,
                            category, [framing], options)
    return {'job_id': job_id, 'reshoot_id': str(job['id']), 'status': 'running',
            'framing': framing, 'balance': credits.balance(workspace_id)}


# The orphan sweep runs from here rather than a timer: App Runner throttles CPU between
# requests, so a background loop is exactly what fails to run when it is needed. The
# frontend polls this every 3 seconds while anything is in flight.
_last_sweep = 0.0
SWEEP_EVERY = 30.0


def sweep_if_due() -> None:
    global _last_sweep
    if time.monotonic() - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = time.monotonic()
    for orphan in jobs.sweep():
        # Refund what was reserved minus what actually landed. Exact, because images are
        # persisted as each one arrives rather than in a batch at the end.
        credits.settle(str(orphan['id']), delivered=int(orphan['delivered']))


@app.get('/api/credits')
def get_credits(session: dict = Depends(auth.current_session)):
    workspace_id = auth.current_workspace(session)
    return {'balance': credits.balance(workspace_id), 'costs': credits.COST}


@app.get('/api/history')
def get_history(session: dict = Depends(auth.current_session)):
    workspace_id = auth.current_workspace(session)
    return [
        {'job_id': str(row['id']), 'kind': row['kind'], 'status': row['status'],
         'images': int(row['images']), 'params': row['params'],
         'created_at': row['created_at'].isoformat()}
        for row in jobs.history(workspace_id)
    ]


@app.get('/api/shoots/{job_id}')
def get_shoot(job_id: str, session: dict = Depends(auth.current_session)):
    workspace_id = auth.current_workspace(session)
    sweep_if_due()
    job = jobs.get(job_id, workspace_id)
    if job is None:
        raise HTTPException(404, 'no such shoot')

    # A shoot is finished when it and every reshoot hanging off it are finished — the
    # spinner has to keep turning while a reshoot is still running.
    children = db.query(
        "SELECT status, error FROM jobs WHERE parent_job_id = %s ORDER BY created_at",
        (job_id,))
    running = (job['status'] in ('queued', 'running')
               or any(c['status'] in ('queued', 'running') for c in children))
    status = 'running' if running else ('completed' if job['images'] else 'failed')

    return JSONResponse({
        'job_id': job_id,
        'status': status,
        'kind': job['kind'],
        'error': job['error'] or next((c['error'] for c in children if c['error']), None),
        # A partial shoot must say so — the UI offers a reshoot per frame.
        'warnings': [f'{name} could not be generated ({reason})'
                     for name, reason in (job['failures'] or [])],
        'options': (job['params'] or {}).get('options'),
        # Minted here, on every read, from the stored key. The client never sees a key
        # and never holds a URL long enough for it to go stale.
        'images': [{'framing': image['framing'], 'attempt': image['attempt'],
                    'url': storage.presign(image['s3_key'])}
                   for image in job['images']],
        'balance': credits.balance(workspace_id),
    })


# The only two trees /media may serve. Confining to the project directory was not
# enough: the container also holds every .py file, so /media/locations.py handed out
# the prompt system — the actual IP — to anyone who asked.
MEDIA_ROOTS = ('assets', 'out')


@app.get('/api/billing')
def get_billing(session: dict = Depends(auth.current_session)):
    workspace_id = auth.current_workspace(session)
    return {
        'balance': credits.balance(workspace_id),
        'costs': credits.COST,
        'rupees_per_credit': billing.RUPEES_PER_CREDIT,
        'ledger': [
            {'kind': row['kind'], 'delta': row['delta'],
             'balance_after': row['balance_after'], 'note': row['note'],
             'created_at': row['created_at'].isoformat()}
            for row in credits.ledger(workspace_id)
        ],
    }


@app.get('/api/admin/workspaces')
def admin_list(session: dict = Depends(auth.current_session)):
    auth.require_admin(session)
    return [
        {'id': str(row['id']), 'name': row['name'], 'gstin': row['gstin'],
         'members': int(row['members']), 'balance': credits.balance(str(row['id']))}
        for row in admin.overview()
    ]


@app.post('/api/admin/workspaces')
def admin_create(name: str = Form(...), gstin: str = Form(''),
                 billing_email: str = Form(''), owner_email: str = Form(''),
                 credits_grant: int = Form(0, alias='credits'),
                 session: dict = Depends(auth.current_session)):
    """Provision a customer: workspace, owner login, and any starting credits."""
    auth.require_admin(session)
    if not name.strip():
        raise HTTPException(400, 'the workspace needs a name')

    workspace = admin.create_workspace(name, gstin, billing_email)
    workspace_id = str(workspace['id'])
    result = {'id': workspace_id, 'name': workspace['name']}

    if owner_email.strip():
        try:
            account = admin.create_account(owner_email, workspace_id, 'owner')
        except Exception as error:
            # The workspace exists; say why the login did not rather than 500 and leave
            # the admin guessing which half succeeded.
            raise HTTPException(400, f'workspace created, but the owner login failed: '
                                     f'{error}')
        # The only time this is ever visible. It is not recoverable afterwards.
        result |= {'owner': account['email'], 'password': account['password']}

    if credits_grant > 0:
        credits.grant(workspace_id, credits_grant, 'opening balance')
    return result


@app.post('/api/admin/grant')
def admin_grant(workspace_id: str = Form(...), credits_amount: int = Form(..., alias='credits'),
                note: str = Form('granted from admin'),
                session: dict = Depends(auth.current_session)):
    """Add credits by hand.

    Also the goodwill mechanism: "was that bad image the customer's fault" is a human
    decision recorded in the ledger, not a discount rule somebody has to maintain.
    """
    auth.require_admin(session)
    if credits_amount == 0:
        raise HTTPException(400, 'nothing to grant')
    return {'balance': credits.grant(workspace_id, credits_amount, note)}


@app.post('/api/webhooks/razorpay')
async def razorpay_webhook(request: Request):
    """Razorpay calls this. Authenticated by HMAC, not by session.

    Always answers 200 once the signature is good: a non-200 makes Razorpay retry
    forever, and a duplicate delivery is normal rather than an error.
    """
    raw = await request.body()          # the exact bytes, before any parsing
    signature = request.headers.get('x-razorpay-signature', '')
    try:
        result = billing.handle(raw, signature)
    except PermissionError:
        raise HTTPException(400, 'bad signature')
    except Exception as error:          # noqa: BLE001 - never retry-loop on our own bug
        print(f'razorpay webhook failed: {error!r}', flush=True)
        return {'ok': False}
    print(f'razorpay webhook: {result}', flush=True)
    return {'ok': True, **result}


@app.post('/api/admin/invoices')
def admin_invoice(workspace_id: str = Form(...), credits_count: int = Form(..., alias='credits'),
                  session: dict = Depends(auth.current_session)):
    """Raise a GST invoice for a credit pack and email it to the customer."""
    auth.require_admin(session)
    if not billing.configured():
        raise HTTPException(503, 'Razorpay is not configured on this deployment')
    try:
        return billing.raise_invoice(workspace_id, credits_count)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get('/api/packs')
def list_packs(session: dict = Depends(auth.current_session)):
    """What a customer can buy, priced server-side."""
    auth.current_workspace(session)
    return {
        'available': billing.configured(),
        'rupees_per_credit': billing.RUPEES_PER_CREDIT,
        'packs': [{'key': key, 'credits': size,
                   'rupees': billing.price_paise(size) // 100,
                   'shoots': size // credits.COST['shoot']}
                  for key, size in billing.PACKS.items()],
    }


@app.post('/api/checkout')
def checkout(pack: str = Form(...), session: dict = Depends(auth.current_session)):
    """Start a purchase. Returns what Razorpay Checkout needs to open.

    Takes a pack NAME, never an amount or a credit count. The size is resolved from
    billing.PACKS here, so the browser cannot ask for three hundred credits at the
    thirty-credit price — the only thing it gets to choose is which row of the table.
    """
    workspace_id = auth.current_workspace(session)
    if not billing.configured():
        raise HTTPException(503, 'payments are not configured on this deployment')
    if pack not in billing.PACKS:
        raise HTTPException(400, 'no such pack')

    try:
        invoice = billing.raise_invoice(workspace_id, billing.PACKS[pack])
    except ValueError as error:
        raise HTTPException(400, str(error))

    if not invoice.get('order_id'):
        # Checkout cannot open without one. Falling through to the modal with a null
        # order id would fail in the browser with nothing useful in it, so fail here
        # where the hosted page is still a working answer.
        log.error('invoice %s has no order_id; falling back to the hosted page',
                  invoice['invoice_id'])

    return {
        'key_id': os.environ['RAZORPAY_KEY_ID'],   # publishable, not the secret
        'order_id': invoice.get('order_id'),
        'short_url': invoice.get('short_url'),     # the fallback, and the receipt link
        'amount_paise': invoice['gross_paise'],
        'credits': invoice['credits'],
        'name': session.get('workspace_name') or '',
        'email': session.get('email') or '',
    }


@app.get('/api/invoices')
def list_invoices(session: dict = Depends(auth.current_session)):
    workspace_id = auth.current_workspace(session)
    return [
        {'id': row['razorpay_invoice_id'], 'credits': row['credits'],
         'amount': row['amount_paise'] / 100, 'status': row['status'],
         'url': row['short_url'], 'issued_at': row['issued_at'].isoformat(),
         'paid_at': row['paid_at'].isoformat() if row['paid_at'] else None}
        for row in billing.invoices_for(workspace_id)
    ]


@app.get('/media/{path:path}')
def media(path: str, session: dict = Depends(auth.current_session)):
    root = pathlib.Path.cwd().resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise HTTPException(404, 'not found')
    # Compared against the resolved path, so ../ cannot smuggle its way past the prefix.
    relative = resolved.relative_to(root)
    if not relative.parts or relative.parts[0] not in MEDIA_ROOTS:
        raise HTTPException(404, 'not found')
    return FileResponse(resolved)


app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
