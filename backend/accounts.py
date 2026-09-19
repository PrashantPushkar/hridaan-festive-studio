"""Public verified accounts and durable entitlements, using Studio's existing SQLite DB."""
import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from urllib.parse import urlparse

from email_validator import validate_email, EmailNotValidError
import phonenumbers
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

router=APIRouter()
connect=None
PRODUCTION=False
EVENTS={'festive_studio_signup_started','festive_studio_signup_completed','festive_studio_email_verified','festive_studio_generation_started','festive_studio_generation_completed','festive_studio_generation_failed','festive_studio_free_limit_reached','festive_studio_cross_sell_clicked'}


def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def now(): return int(time.time())


def initialize(factory, production=False):
    global connect,PRODUCTION
    connect=factory;PRODUCTION=production
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS accounts (
          id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, full_name TEXT NOT NULL,
          phone TEXT NOT NULL, email_verified INTEGER NOT NULL DEFAULT 0,
          marketing_consent INTEGER NOT NULL DEFAULT 0, consent_at INTEGER NOT NULL,
          consent_version TEXT NOT NULL, created_at INTEGER NOT NULL, last_activity INTEGER,
          source TEXT NOT NULL DEFAULT 'Festive Studio', utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
          free_generation_used INTEGER NOT NULL DEFAULT 0 CHECK(free_generation_used IN (0,1)),
          generation_count INTEGER NOT NULL DEFAULT 0, last_generation_at INTEGER,
          reserved_run TEXT, early_access_at INTEGER, last_email_at INTEGER);
        CREATE TABLE IF NOT EXISTS account_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS verification_tokens (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER);
        CREATE TABLE IF NOT EXISTS account_rate_limits (bucket TEXT NOT NULL, stamp INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS account_rate_bucket ON account_rate_limits(bucket,stamp);
        CREATE TABLE IF NOT EXISTS studio_events (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at INTEGER NOT NULL);
        ''')
        # Startup recovery uses the greeting status reconciled by the existing app.
        # A ready AI image consumes the credit even if the previous process died before finalization.
        db.execute("UPDATE accounts SET free_generation_used=1,generation_count=generation_count+1,last_generation_at=? WHERE reserved_run IN (SELECT id FROM greetings WHERE status IN ('ready_for_review','approved','rejected') AND json_extract(payload,'$.image_source')='ai')",(now(),))
        db.execute("UPDATE accounts SET reserved_run=NULL WHERE reserved_run IS NOT NULL AND reserved_run NOT IN (SELECT id FROM greetings WHERE status IN ('queued','generating'))")
        db.execute('DELETE FROM account_sessions WHERE expires_at<?',(now(),))
        db.execute('DELETE FROM verification_tokens WHERE expires_at<?',(now(),))


def event(name, db=None):
    if name not in EVENTS: return
    if db is not None: db.execute('INSERT INTO studio_events(name,created_at) VALUES (?,?)',(name,now()))
    else:
        with connect() as db: event(name,db)


def throttle(key, limit, seconds):
    bucket=digest(key)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM account_rate_limits WHERE stamp<?',(now()-86400,))
        n=db.execute('SELECT count(*) FROM account_rate_limits WHERE bucket=? AND stamp>?',(bucket,now()-seconds)).fetchone()[0]
        if n>=limit: raise HTTPException(429,'Please wait before trying again.')
        db.execute('INSERT INTO account_rate_limits VALUES (?,?)',(bucket,now()))


def ip(request):
    # Do not trust arbitrary forwarding headers. Configure trusted proxies at the server boundary.
    return request.client.host if request.client else 'unknown'


def normalize_email(value):
    try: return validate_email(value,check_deliverability=False).normalized.casefold()
    except EmailNotValidError: raise HTTPException(422,'Enter a valid email address.') from None


def current_user(request, required=True):
    token=request.session.get('account_session','')
    user=None
    if token:
        with connect() as db:
            user=db.execute('SELECT a.* FROM accounts a JOIN account_sessions s ON a.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(digest(token),now())).fetchone()
            if user: db.execute('UPDATE accounts SET last_activity=? WHERE id=?',(now(),user['id']))
    if not user and required: raise HTTPException(401,'Sign in to your Hridaan Labs account.')
    return dict(user) if user else None


def public_user(user):
    if not user: return None
    return {key:user[key] for key in ['id','full_name','email','email_verified','marketing_consent','free_generation_used','generation_count','reserved_run','early_access_at']}


def policy_ready():
    return not PRODUCTION or (os.environ.get('ACCOUNT_POLICIES_REVIEWED')=='true' and all(os.environ.get(k,'').startswith('https://') for k in ['PRIVACY_POLICY_URL','TERMS_URL']))


def mail_ready():
    return all(os.environ.get(k) for k in ['SMTP_HOST','SMTP_USERNAME','SMTP_PASSWORD','MAIL_FROM','PUBLIC_BASE_URL'])


def send_email(recipient, token):
    base=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
    parsed=urlparse(base)
    if parsed.scheme!='https' and not (not PRODUCTION and parsed.hostname in {'localhost','127.0.0.1'}):
        raise RuntimeError('A secure public base URL is required')
    message=EmailMessage()
    message['Subject']='Your Hridaan Labs sign-in link'
    message['From']='Hridaan Labs <'+os.environ['MAIL_FROM']+'>'
    message['To']=recipient
    if os.environ.get('MAIL_REPLY_TO'):
        message['Reply-To']=os.environ['MAIL_REPLY_TO']
    message.set_content('Verify your email and sign in to Festive Studio by Hridaan Labs.\n\n'+base+'/verify?token='+token+'\n\nThis link expires in 20 minutes and can be used once. If you did not request it, ignore this email. No marketing subscription is created by signing in.')
    context=ssl.create_default_context()
    port=int(os.environ.get('SMTP_PORT','465'))
    if port==465:
        with smtplib.SMTP_SSL(os.environ['SMTP_HOST'],port,timeout=20,context=context) as server:
            server.login(os.environ['SMTP_USERNAME'],os.environ['SMTP_PASSWORD']);server.send_message(message)
    elif port==587:
        with smtplib.SMTP(os.environ['SMTP_HOST'],port,timeout=20) as server:
            server.ehlo();server.starttls(context=context);server.ehlo()
            server.login(os.environ['SMTP_USERNAME'],os.environ['SMTP_PASSWORD']);server.send_message(message)
    else: raise RuntimeError('SMTP must use TLS port 465 or 587')


async def issue_link(user):
    token=secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT last_email_at FROM accounts WHERE id=?',(user['id'],)).fetchone()
        if row['last_email_at'] and now()-row['last_email_at']<60: return
        db.execute('UPDATE accounts SET last_email_at=? WHERE id=?',(now(),user['id']))
        db.execute('UPDATE verification_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL',(now(),user['id']))
        db.execute('INSERT INTO verification_tokens VALUES (?,?,?,NULL)',(digest(token),user['id'],now()+1200))
    try: await asyncio.to_thread(send_email,user['email'],token)
    except Exception:
        with connect() as db:
            db.execute('DELETE FROM verification_tokens WHERE token_hash=?',(digest(token),))
        raise HTTPException(503,'The verification email could not be sent. Please try again shortly.') from None


class Registration(BaseModel):
    full_name:str=Field(min_length=2,max_length=100)
    email:str=Field(max_length=254)
    country:str=Field(min_length=2,max_length=2)
    phone:str=Field(min_length=4,max_length=30)
    marketing_consent:bool=False
    utm_source:str=Field(default='',max_length=100)
    utm_medium:str=Field(default='',max_length=100)
    utm_campaign:str=Field(default='',max_length=100)


class EmailInput(BaseModel): email:str=Field(max_length=254)
class EventInput(BaseModel): name:str=Field(max_length=60)
class ConsentInput(BaseModel): marketing_consent:bool


@router.get('/api/accounts/options')
def options():
    return {'countries':[{'code':code,'calling_code':phonenumbers.country_code_for_region(code)} for code in sorted(phonenumbers.SUPPORTED_REGIONS)],'mail_ready':mail_ready(),'registration_ready':policy_ready() and mail_ready(),'privacy_url':os.environ.get('PRIVACY_POLICY_URL','https://hridaanlabs.com/privacy'),'terms_url':os.environ.get('TERMS_URL',''),'policies_ready':policy_ready()}


@router.post('/api/accounts/register',status_code=202)
async def register(payload:Registration,request:Request):
    throttle('register:'+ip(request),5,3600)
    if not policy_ready(): raise HTTPException(503,'Registration is not open yet. Please check back shortly.')
    if not mail_ready(): raise HTTPException(503,'Email sign-in is being prepared. Please check back shortly.')
    email=normalize_email(payload.email)
    full_name=payload.full_name.strip()
    if len(full_name)<2 or any(ord(c)<32 for c in full_name): raise HTTPException(422,'Enter your full name.')
    try:
        if payload.country not in phonenumbers.SUPPORTED_REGIONS: raise ValueError()
        phone=phonenumbers.parse(payload.phone,payload.country)
        if not phonenumbers.is_valid_number(phone): raise ValueError()
        phone=phonenumbers.format_number(phone,phonenumbers.PhoneNumberFormat.E164)
    except (ValueError,phonenumbers.NumberParseException): raise HTTPException(422,'Enter a valid phone number for the selected country.') from None
    attribution={key:getattr(payload,key) if re.fullmatch(r'[a-zA-Z0-9_. -]{0,100}',getattr(payload,key)) else '' for key in ['utm_source','utm_medium','utm_campaign']}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT * FROM accounts WHERE email=?',(email,)).fetchone()
        if existing: user=dict(existing)
        else:
            uid=secrets.token_hex(16)
            db.execute('INSERT INTO accounts(id,email,full_name,phone,marketing_consent,consent_at,consent_version,created_at,utm_source,utm_medium,utm_campaign) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(uid,email,full_name,phone,int(payload.marketing_consent),now(),'2026-09-18-product-updates',now(),attribution['utm_source'],attribution['utm_medium'],attribution['utm_campaign']))
            event('festive_studio_signup_completed',db)
            user={'id':uid,'email':email}
    throttle('email:'+email,5,3600)
    await issue_link(user)
    return {'message':'Check your email. Use the secure link to verify your email and sign in.'}


@router.post('/api/accounts/email-link',status_code=202)
async def email_link(payload:EmailInput,request:Request):
    throttle('email-link:'+ip(request),10,3600)
    if not mail_ready(): raise HTTPException(503,'Email sign-in is being prepared. Please check back shortly.')
    email=normalize_email(payload.email);throttle('email:'+email,5,3600)
    with connect() as db: user=db.execute('SELECT * FROM accounts WHERE email=?',(email,)).fetchone()
    if user: await issue_link(dict(user))
    return {'message':'If an account exists, a sign-in link has been sent. Please check your email.'}


@router.get('/verify')
def verify(request:Request,token:str=''):
    throttle('verify:'+ip(request),30,3600)
    if not 32<=len(token)<=128: return RedirectResponse('/?verification=invalid',status_code=303)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM verification_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at>?',(digest(token),now())).fetchone()
        if not row: return RedirectResponse('/?verification=invalid',status_code=303)
        db.execute('UPDATE verification_tokens SET used_at=? WHERE token_hash=?',(now(),digest(token)))
        previous=db.execute('SELECT email_verified FROM accounts WHERE id=?',(row['user_id'],)).fetchone()
        db.execute('UPDATE accounts SET email_verified=1,last_activity=? WHERE id=?',(now(),row['user_id']))
        session=secrets.token_urlsafe(32)
        db.execute('INSERT INTO account_sessions VALUES (?,?,?)',(digest(session),row['user_id'],now()+30*86400))
        if not previous['email_verified']: event('festive_studio_email_verified',db)
    request.session.clear();request.session['account_session']=session
    return RedirectResponse('/?verified=1',status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


def logout(request):
    token=request.session.get('account_session','')
    with connect() as db: db.execute('DELETE FROM account_sessions WHERE token_hash=?',(digest(token),))
    request.session.clear()


@router.post('/api/accounts/early-access')
def early_access(request:Request):
    user=current_user(request)
    with connect() as db: db.execute('UPDATE accounts SET early_access_at=COALESCE(early_access_at,?) WHERE id=?',(now(),user['id']))
    return {'message':'You are on the early access list. We will email you when more creations are available.'}


@router.put('/api/accounts/consent')
def consent(payload:ConsentInput,request:Request):
    user=current_user(request)
    with connect() as db: db.execute('UPDATE accounts SET marketing_consent=?,consent_at=? WHERE id=?',(int(payload.marketing_consent),now(),user['id']))
    return {'ok':True}


@router.post('/api/accounts/events',status_code=202)
def client_event(payload:EventInput,request:Request):
    throttle('events:'+ip(request),60,3600)
    # Completion and entitlement events are emitted only by the backend.
    if payload.name not in {'festive_studio_signup_started','festive_studio_cross_sell_clicked'}: raise HTTPException(422,'Unsupported event.')
    event(payload.name)
    return {'ok':True}


def reserve(db,user_id,run_id):
    row=db.execute('SELECT email_verified,free_generation_used,reserved_run FROM accounts WHERE id=?',(user_id,)).fetchone()
    if not row or not row['email_verified']: raise HTTPException(403,'Verify your email before generating.')
    if row['free_generation_used']: raise HTTPException(403,"You've used your free Festive Studio creation.")
    if row['reserved_run']: raise HTTPException(409,'A creation is already in progress. Please wait for it to finish.')
    result=db.execute('UPDATE accounts SET reserved_run=? WHERE id=? AND free_generation_used=0 AND reserved_run IS NULL',(run_id,user_id))
    if result.rowcount!=1: raise HTTPException(409,'A creation is already in progress.')


def settle(db,run):
    if not run.get('account_mode'): return
    success=run['status']=='ready_for_review' and run.get('image_source')=='ai'
    result=db.execute('UPDATE accounts SET free_generation_used=CASE WHEN ? THEN 1 ELSE free_generation_used END,generation_count=generation_count+?,last_generation_at=CASE WHEN ? THEN ? ELSE last_generation_at END,reserved_run=NULL,last_activity=? WHERE id=? AND reserved_run=?',(success,int(success),success,now(),now(),run['owner'],run['id']))
    if result.rowcount:
        event('festive_studio_generation_completed' if success else 'festive_studio_generation_failed',db)
        if success: event('festive_studio_free_limit_reached',db)

