"""Standalone Festive Studio. No Social Hub database or credentials are used."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import uuid
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env.local', override=True)
from . import festival_agents as engine
from . import ai_client
from . import costs, personalization, accounts

DATA = Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))).resolve()
PRODUCTION = os.environ.get('APP_ENV') == 'production'
ACCOUNT_MODE = os.environ.get('STUDIO_AUTH_MODE', 'accounts') != 'legacy'
if PRODUCTION and not ACCOUNT_MODE:
    raise RuntimeError('Production requires verified account authentication.')
PASSWORD = os.environ.get('APP_PASSWORD', '')
OWNER_PREVIEW_TOKEN = os.environ.get('OWNER_PREVIEW_TOKEN', '')
if PRODUCTION and OWNER_PREVIEW_TOKEN and len(OWNER_PREVIEW_TOKEN) < 32:
    raise RuntimeError('OWNER_PREVIEW_TOKEN must contain at least 32 characters.')
SESSION_SECRET = os.environ.get('SESSION_SECRET', '')
if PRODUCTION and len(SESSION_SECRET) < 32:
    raise RuntimeError('Production requires SESSION_SECRET (32+ characters).')
DATA.mkdir(parents=True, exist_ok=True)
if not SESSION_SECRET:
    secret_path = DATA / '.session-secret'
    if not secret_path.exists():
        secret_path.write_text(secrets.token_urlsafe(48), encoding='utf-8')
    SESSION_SECRET = secret_path.read_text(encoding='utf-8')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('festive_studio')
class VerificationLogFilter(logging.Filter):
    def filter(self, record):
        return '/verify?' not in record.getMessage()
logging.getLogger('uvicorn.access').addFilter(VerificationLogFilter())
tasks = set()
generation_lock = asyncio.Semaphore(2)


def connection():
    db = sqlite3.connect(DATA / 'studio.sqlite3', timeout=20)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS greetings (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS greetings_owner_created ON greetings(owner, created)')
        db.execute("UPDATE greetings SET status='failed' WHERE status IN ('queued','generating')")

    accounts.initialize(connection, PRODUCTION)


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield
    for task in list(tasks):
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title='Hridaan Labs · Festive Studio', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site='lax', https_only=PRODUCTION, max_age=60*60*24*30)

app.include_router(accounts.router)


@app.middleware('http')
async def boundaries(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'DELETE'}:
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 3 * 1024 * 1024:
                return Response('Upload is too large. Maximum request size is 3 MB.', status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return Response('Cross-origin changes are not allowed.', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; font-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api') or request.url.path == '/verify':
        response.headers['Cache-Control'] = 'no-store'
    return response


def owner_preview_active(request: Request):
    return bool(request.session.get('owner_preview'))


def owner(request: Request):
    if owner_preview_active(request):
        return 'owner-preview'
    if ACCOUNT_MODE:
        user = accounts.current_user(request)
        if not user['email_verified']:
            raise HTTPException(403, 'Verify your email before using the studio.')
        return user['id']
    if PASSWORD and not request.session.get('authenticated'):
        raise HTTPException(401, 'Please unlock your studio.')
    if 'owner' not in request.session:
        request.session['owner'] = uuid.uuid4().hex
    return request.session['owner']


def read_run(run_id, user):
    with connection() as db:
        row = db.execute('SELECT * FROM greetings WHERE id=? AND owner=?', (run_id, user)).fetchone()
    if row is None:
        raise HTTPException(404, 'Greeting not found.')
    value = json.loads(row['payload'])
    value['status'] = row['status']
    if row['status'] == 'failed' and not value.get('error'):
        value['error'] = 'Generation was interrupted. Please generate a new greeting.'
    return value


def save_run(value, terminal=False):
    with connection() as db:
        db.execute('UPDATE greetings SET status=?, payload=? WHERE id=? AND owner=?', (value['status'], json.dumps(value), value['id'], value['owner']))
        if terminal:
            accounts.settle(db, value)


def public_run(value):
    return {k: v for k, v in value.items() if k not in {'owner', 'festival_snapshot', 'account_mode'}}


class Login(BaseModel):
    password: str = Field(max_length=256)


class OwnerPreview(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class Generate(BaseModel):
    festival_id: int = Field(ge=0, lt=len(engine.FESTIVALS_2026))
    brand: str = Field(default='Hridaan Labs', max_length=48)
    sender_type: Literal['company', 'individual'] = 'company'
    persona: Literal['warm','humorous','formal','traditional','heritage','modern','futuristic','trendy','political'] = 'warm'
    logo_base64: str | None = Field(default=None, max_length=2800000)
    greeting_date: date | None = None
    date_placement: Literal['none','image','text','both'] = 'none'
    channel: Literal['instagram_feed', 'instagram_stories', 'whatsapp_status', 'whatsapp_chat'] = 'instagram_feed'
    use_ai: bool = True
    generate_text: bool = True


class Edit(BaseModel):
    greeting_date: date | None = None
    date_placement: Literal['none','image','text','both'] | None = None
    headline: str = Field(min_length=1, max_length=70)
    tagline: str = Field(max_length=55)
    message: str = Field(min_length=1, max_length=1000)
    subline: str = Field(max_length=80)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/api/session')
def session(request: Request):
    if owner_preview_active(request):
        return {'authenticated':True, 'account_mode':False, 'owner_preview':True, 'user':None, 'password_required':False, 'ai_available':bool(os.environ.get('OPENAI_API_KEY')), 'product':'Festive Studio'}
    if ACCOUNT_MODE:
        user = accounts.current_user(request, required=False)
        return {'authenticated':bool(user), 'account_mode':True, 'user':accounts.public_user(user), 'password_required':False, 'ai_available':bool(os.environ.get('OPENAI_API_KEY')), 'product':'Festive Studio'}
    authenticated = not PASSWORD or request.session.get('authenticated', False)
    if authenticated:
        owner(request)
    return {'authenticated': authenticated, 'password_required': bool(PASSWORD), 'ai_available': bool(os.environ.get('OPENAI_API_KEY')), 'product': 'Festive Studio'}


@app.post('/api/owner-preview')
def start_owner_preview(payload: OwnerPreview, request: Request):
    if not OWNER_PREVIEW_TOKEN or not secrets.compare_digest(payload.token.encode(), OWNER_PREVIEW_TOKEN.encode()):
        raise HTTPException(404, 'Owner preview is unavailable.')
    request.session.clear()
    request.session['owner_preview'] = True
    request.session['owner'] = 'owner-preview'
    return {'ok':True}


login_attempts = {}


@app.post('/api/login')
def login(payload: Login, request: Request):
    if ACCOUNT_MODE:
        raise HTTPException(404, 'Use the email sign-in link.')
    key = request.client.host if request.client else 'unknown'
    now = datetime.now(timezone.utc).timestamp()
    attempts = [stamp for stamp in login_attempts.get(key, []) if now - stamp < 300]
    if len(attempts) >= 10:
        raise HTTPException(429, 'Too many attempts. Please wait five minutes.')
    if PASSWORD and not secrets.compare_digest(payload.password.encode(), PASSWORD.encode()):
        login_attempts[key] = attempts + [now]
        raise HTTPException(401, 'That password did not match.')
    request.session['authenticated'] = True
    owner(request)
    login_attempts.pop(key, None)
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request):
    if ACCOUNT_MODE:
        accounts.logout(request)
        return {'ok':True}
    request.session.pop('authenticated', None)
    return {'ok': True}


@app.get('/api/options')
def options(user=Depends(owner)):
    return {'personas':[{'id':key,'name':value[0],'description':value[1]} for key,value in personalization.PERSONAS.items()], 'calendar_year':2026}


@app.get('/api/festivals')
def festivals(user=Depends(owner)):
    return [dict(f, id=i) for i, f in enumerate(engine.FESTIVALS_2026)]


@app.get('/api/greetings')
def history(user=Depends(owner)):
    with connection() as db:
        ids = db.execute('SELECT id FROM greetings WHERE owner=? ORDER BY created DESC LIMIT 100', (user,)).fetchall()
    return [public_run(read_run(row['id'], user)) for row in ids]


def default_headline(festival):
    if festival.get('headline'):
        return festival['headline']
    if festival['name'] in {'Good Friday', 'Muharram', 'Gandhi Jayanti'}:
        return festival['name']
    return 'Happy ' + festival['name']


async def generate_job(run):
    async with generation_lock:
        run['status'] = 'generating'
        save_run(run)
        context_token = ai_client.brand_context.set(run['brand'])
        creative_token = ai_client.creative_context.set(personalization.instructions(run))
        entries = []
        usage_token = costs.ledger_context.set(entries)
        try:
            festival = personalization.festival_for(run, run.get('festival_snapshot') or engine.FESTIVALS_2026[run['festival_id']])
            background, blueprint = None, None
            run['warnings'] = []
            run['tagline'], run['message'] = personalization.fallback(run, festival)
            if not run.get('generate_text', True):
                run['tagline'] = ''
                run['message'] = run['headline'] + '.'
            run['image_source'] = 'template'
            if run['use_ai']:
                if run.get('generate_text', True):
                    try:
                        text = await engine.run_greeting_text_agent(festival)
                        run['tagline'] = text['tagline'][:55]
                        run['message'] = text['whatsapp_message'][:1000]
                    except Exception:
                        logger.warning('Greeting copy generation failed for %s', run['id'])
                        run['warnings'].append('AI copy was unavailable. An editable starter message is shown.')
                background, blueprint = await engine.generate_ai_background(festival, greeting={'tagline': run['tagline'], 'whatsapp_message': run['message']}, channel=run['channel'], use_text_ai=run.get('generate_text', True))
                if background is None:
                    run['warnings'].append('AI artwork was unavailable. This greeting uses the original illustrated template. Check personal OpenAI billing or access before trying again.')
                else:
                    run['image_source'] = 'ai'
                    await asyncio.to_thread(background.save, DATA / (run['id'] + '-background.png'))
            if run['brand']:
                run['message'] += '\n\n' + run['subline']
            run['message'] = message_with_date(run['message'], run)
            run['blueprint'] = blueprint
            await render(run, background)
            run['status'] = 'ready_for_review'
        except asyncio.CancelledError:
            run.update(status='failed', error='Generation was interrupted. Your free creation is still available.')
            raise
        except Exception:
            logger.exception('Generation failed for %s', run['id'])
            run['status'] = 'failed'
            run['error'] = 'This greeting could not be rendered. Please try again.'
        finally:
            fx = await asyncio.to_thread(costs.exchange_rate) if run['use_ai'] else None
            run['cost'] = costs.summary(entries, run['use_ai'], fx)
            save_run(run, terminal=True)
            costs.ledger_context.reset(usage_token)
            ai_client.creative_context.reset(creative_token)
            ai_client.brand_context.reset(context_token)


def message_with_date(message, run, old_date=None):
    for value in {old_date, run.get('greeting_date')} - {None}:
        suffix = '\n\n' + personalization.date_label(value)
        if message.endswith(suffix): message = message[:-len(suffix)]
    if run.get('date_placement') in {'text','both'} and run.get('greeting_date'):
        message += '\n\n' + personalization.date_label(run['greeting_date'])
    return message


async def render(run, background=None):
    background_file = DATA / (run['id'] + '-background.png')
    if background is None and background_file.exists():
        with Image.open(background_file) as image:
            background = image.convert('RGBA')
    festival = personalization.festival_for(run, run.get('festival_snapshot') or engine.FESTIVALS_2026[run['festival_id']])
    logo_path = DATA / (run['id'] + '-logo.png')
    if run.get('has_logo') and logo_path.exists():
        with Image.open(logo_path) as logo:
            festival['logo_image'] = logo.convert('RGBA')
    png = await asyncio.to_thread(engine.render_greeting_image, festival, run['headline'], subline=run['subline'], tagline=run['tagline'], ai_background=background, channel=run['channel'], blueprint=run.get('blueprint'))
    tmp = DATA / (run['id'] + '.tmp')
    tmp.write_bytes(png)
    tmp.replace(DATA / (run['id'] + '.png'))
    run['revision'] = run.get('revision', 0) + 1


@app.post('/api/greetings', status_code=202)
async def generate(payload: Generate, request: Request, user=Depends(owner)):
    brand = payload.brand.strip()
    if payload.sender_type == 'company' and not brand:
        raise HTTPException(422, 'Enter a company name or choose Individual.')
    if payload.sender_type == 'individual' and payload.logo_base64:
        raise HTTPException(422, 'Logo uploads are available for company greetings.')
    logo = personalization.normalized_logo(payload.logo_base64)
    if payload.use_ai and not os.environ.get('OPENAI_API_KEY'):
        raise HTTPException(503, 'Your personal OpenAI key is not configured. Choose Illustrated template to continue.')
    now = datetime.now(timezone.utc).isoformat()
    festival = engine.FESTIVALS_2026[payload.festival_id]
    run = dict(payload.model_dump(mode='json', exclude={'logo_base64'}), id=uuid.uuid4().hex, owner=user, brand=brand, created_at=now,
               status='queued', festival_name=festival['name'], headline=default_headline(festival),
               subline=(f'From all of us at {brand}' if payload.sender_type == 'company' else f'With warm wishes, {brand}' if brand else ''), tagline='', message='', revision=0, has_logo=bool(logo), festival_snapshot=dict(festival))
    run['account_mode'] = ACCOUNT_MODE and not owner_preview_active(request)
    run['greeting_date'] = payload.greeting_date.isoformat() if payload.greeting_date else festival['date']
    run['date_note'] = festival.get('date_note', '')
    run['date_is_custom'] = run['greeting_date'] != festival['date']
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        pending = db.execute("SELECT count(*) FROM greetings WHERE status IN ('queued','generating')").fetchone()[0]
        if pending >= 4:
            raise HTTPException(429, 'The studio is busy. Please try again after a greeting finishes.')
        count = db.execute('SELECT count(*) FROM greetings WHERE created>=?', (now[:10],)).fetchone()[0]
        if count >= int(os.environ.get('DAILY_GENERATION_LIMIT', '20')):
            raise HTTPException(429, 'Today’s generation limit has been reached.')
        if run['account_mode']:
            accounts.reserve(db, user, run['id'])
            accounts.event('festive_studio_generation_started', db)
        db.execute('INSERT INTO greetings VALUES (?, ?, ?, ?, ?)', (run['id'], user, now, run['status'], json.dumps(run)))
    if logo:
        try:
            (DATA / (run['id'] + '-logo.png')).write_bytes(logo)
        except OSError:
            run.update(status='failed', error='The logo could not be saved. Please try again.')
            save_run(run, terminal=True)
            raise HTTPException(500, 'The logo could not be saved.') from None
    task = asyncio.create_task(generate_job(run.copy()))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return public_run(run)


@app.get('/api/greetings/{run_id}')
def greeting(run_id: str, user=Depends(owner)):
    return public_run(read_run(run_id, user))


edit_lock = asyncio.Lock()


@app.put('/api/greetings/{run_id}')
async def edit(run_id: str, payload: Edit, user=Depends(owner)):
    async with edit_lock:
        run = read_run(run_id, user)
        if run['status'] not in {'ready_for_review', 'approved'}:
            raise HTTPException(409, 'Wait for generation to finish before editing.')
        if not payload.headline.strip() or not payload.message.strip():
            raise HTTPException(422, 'Headline and message cannot be blank.')
        source = run.get('festival_snapshot') or engine.FESTIVALS_2026[run['festival_id']]
        run.setdefault('greeting_date', source['date'])
        old_date = run.get('greeting_date')
        run.update(payload.model_dump(mode='json', exclude_none=True))
        run['message'] = message_with_date(run['message'], run, old_date)
        source = run.get('festival_snapshot') or engine.FESTIVALS_2026[run['festival_id']]
        run['date_is_custom'] = run.get('greeting_date') != source['date']
        run['status'] = 'ready_for_review'
        await render(run)
        save_run(run)
        return public_run(run)


@app.post('/api/greetings/{run_id}/approve')
async def approve(run_id: str, user=Depends(owner)):
    async with edit_lock:
        run = read_run(run_id, user)
        if run['status'] not in {'ready_for_review', 'approved'}:
            raise HTTPException(409, 'This greeting is not ready for approval.')
        run['status'] = 'approved'
        save_run(run)
        return public_run(run)


@app.post('/api/greetings/{run_id}/reject')
async def reject(run_id: str, user=Depends(owner)):
    async with edit_lock:
        run = read_run(run_id, user)
        if run['status'] not in {'ready_for_review', 'approved'}:
            raise HTTPException(409, 'This greeting is not ready for review.')
        run['status'] = 'rejected'
        save_run(run)
        return public_run(run)


@app.get('/api/greetings/{run_id}/image.png')
def download(run_id: str, user=Depends(owner)):
    run = read_run(run_id, user)
    path = DATA / (run['id'] + '.png')
    if not path.exists():
        raise HTTPException(404, 'The image is not ready yet.')
    return FileResponse(path, media_type='image/png', filename=f"festive-studio-{run['id'][:8]}.png", content_disposition_type='inline')


@app.get('/')
def home():
    return FileResponse(ROOT / 'public/index.html')


@app.get('/owner-preview')
def owner_preview_page():
    return FileResponse(ROOT / 'public/owner-preview.html')


app.mount('/assets', StaticFiles(directory=ROOT / 'public'), name='assets')
app.mount('/fonts', StaticFiles(directory=ROOT / 'backend/assets/fonts'), name='fonts')


