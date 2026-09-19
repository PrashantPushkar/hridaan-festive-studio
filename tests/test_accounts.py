import asyncio
from pathlib import Path
import sqlite3
import httpx
import pytest
from PIL import Image
from backend import app as studio, accounts

@pytest.fixture
def account_env(tmp_path,monkeypatch):
    monkeypatch.setattr(studio,'DATA',tmp_path)
    monkeypatch.setattr(studio,'ACCOUNT_MODE',True)
    monkeypatch.setattr(studio,'generation_lock',asyncio.Semaphore(2))
    monkeypatch.setattr(studio,'edit_lock',asyncio.Lock())
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setattr(accounts,'mail_ready',lambda:True)
    monkeypatch.setattr(accounts,'policy_ready',lambda:True)
    sent=[]
    monkeypatch.setattr(accounts,'send_email',lambda email,token:sent.append((email,token)))
    monkeypatch.setattr(studio.costs,'exchange_rate',lambda:None)
    studio.initialize()
    return sent

def registration(email='person@example.com'):
    return dict(full_name='Test Person',email=email,country='IN',phone='+919876543210',marketing_consent=False)

async def signup(client,sent,email='person@example.com'):
    r=await client.post('/api/accounts/register',json=registration(email));assert r.status_code==202,r.text
    r=await client.get('/verify',params={'token':sent[-1][1]});assert r.status_code==303
    return (await client.get('/api/session')).json()['user']

async def finished(client,runid):
    for _ in range(150):
        r=(await client.get('/api/greetings/'+runid)).json()
        if r['status'] not in {'queued','generating'}:return r
        await asyncio.sleep(.01)
    raise AssertionError('Job did not finish')

async def fake_render(run,background=None):
    (studio.DATA/(run['id']+'.png')).write_bytes(b'test-image')
    run['revision']=1


def test_signup_verification_expiry_and_persistent_login(account_env):
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as c:
            assert (await c.get('/api/greetings')).status_code==401
            assert (await c.post('/api/login',json={'password':''})).status_code==404
            bad=registration();bad['phone']='123'
            assert (await c.post('/api/accounts/register',json=bad)).status_code==422
            user=await signup(c,account_env)
            assert user['email_verified']==1 and user['marketing_consent']==0
            token=account_env[-1][1]
            replay=await c.get('/verify',params={'token':token})
            assert replay.headers['location']=='/?verification=invalid'
            with studio.connection() as db:
                row=db.execute('SELECT * FROM accounts').fetchone()
                assert row['phone']=='+919876543210'
                assert db.execute('SELECT token_hash FROM verification_tokens').fetchone()[0]!=token
                db.execute('UPDATE accounts SET last_email_at=0,free_generation_used=1')
            assert (await c.post('/api/logout')).status_code==200
            assert (await c.get('/api/greetings')).status_code==401
            c.cookies.clear()
            await c.post('/api/accounts/email-link',json={'email':'PERSON@example.com'})
            await c.get('/verify',params={'token':account_env[-1][1]})
            after=(await c.get('/api/session')).json()['user']
            assert after['id']==user['id'] and after['free_generation_used']==1
            assert (await c.post('/api/accounts/early-access')).status_code==200
            assert (await c.put('/api/accounts/consent',json={'marketing_consent':True})).status_code==200
            assert (await c.get('/api/session')).json()['user']['marketing_consent']==1
            with studio.connection() as db:db.execute('UPDATE accounts SET last_email_at=0')
            await c.post('/api/accounts/email-link',json={'email':'person@example.com'})
            with studio.connection() as db:db.execute('UPDATE verification_tokens SET expires_at=0')
            assert (await c.get('/verify',params={'token':account_env[-1][1]})).headers['location']=='/?verification=invalid'
    asyncio.run(flow())


def test_atomic_generation_limit_and_isolation(account_env,monkeypatch):
    calls=[]
    async def image(*args,**kwargs):
        calls.append(1);await asyncio.sleep(.08)
        return Image.new('RGBA',(10,10)),None
    monkeypatch.setattr(studio.engine,'generate_ai_background',image)
    monkeypatch.setattr(studio,'render',fake_render)
    async def flow():
        transport=httpx.ASGITransport(app=studio.app)
        async with httpx.AsyncClient(transport=transport,base_url='http://test') as c,httpx.AsyncClient(transport=transport,base_url='http://test') as other:
            await signup(c,account_env)
            payload=dict(festival_id=0,brand='Test',use_ai=True,generate_text=False)
            r1,r2=await asyncio.gather(c.post('/api/greetings',json=payload),c.post('/api/greetings',json=payload))
            assert sorted([r1.status_code,r2.status_code])==[202,409]
            accepted=r1 if r1.status_code==202 else r2
            run=await finished(c,accepted.json()['id'])
            assert run['status']=='ready_for_review'
            user=(await c.get('/api/session')).json()['user']
            assert user['free_generation_used']==1 and user['generation_count']==1 and user['reserved_run'] is None
            assert (await c.post('/api/greetings',json=payload)).status_code==403
            assert len(calls)==1
            await signup(other,account_env,'other@example.com')
            assert (await other.get('/api/greetings/'+run['id'])).status_code==404
            await c.put('/api/greetings/'+run['id'],json=dict(headline='Updated',tagline='',message='Hello',subline='Test'))
            assert (await c.get('/api/session')).json()['user']['generation_count']==1
    asyncio.run(flow())


@pytest.mark.parametrize('failure',['image','render','template'])
def test_failed_attempt_keeps_credit(account_env,monkeypatch,failure):
    async def image(*args,**kwargs):return (None,None) if failure=='image' else (Image.new('RGBA',(10,10)),None)
    async def render(run,background=None):
        if failure=='render':raise RuntimeError('Simulated renderer failure')
        await fake_render(run,background)
    monkeypatch.setattr(studio.engine,'generate_ai_background',image)
    monkeypatch.setattr(studio,'render',render)
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as c:
            await signup(c,account_env)
            r=await c.post('/api/greetings',json=dict(festival_id=0,brand='Test',use_ai=failure!='template',generate_text=False))
            await finished(c,r.json()['id'])
            user=(await c.get('/api/session')).json()['user']
            assert user['free_generation_used']==0 and user['reserved_run'] is None
    asyncio.run(flow())


def test_mail_failure_token_cleanup_and_rate_limit(account_env,monkeypatch):
    def fail(*args):raise OSError('Simulated SMTP failure')
    monkeypatch.setattr(accounts,'send_email',fail)
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as c:
            assert (await c.post('/api/accounts/register',json=registration())).status_code==503
            with studio.connection() as db:
                assert db.execute('SELECT count(*) FROM verification_tokens').fetchone()[0]==0
                assert db.execute('SELECT email_verified FROM accounts').fetchone()[0]==0
            for _ in range(10):await c.post('/api/accounts/email-link',json={'email':'missing@example.com'})
            assert (await c.post('/api/accounts/email-link',json={'email':'missing@example.com'})).status_code==429
    asyncio.run(flow())

