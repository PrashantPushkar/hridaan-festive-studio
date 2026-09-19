import asyncio
import base64
from io import BytesIO
import httpx
from PIL import Image, ImageChops
import pytest
from backend import app as studio, costs, personalization, ai_client
from test_studio import isolated, wait_run


def logo_data():
    out=BytesIO();Image.new('RGBA',(100,50),(223,31,63,255)).save(out,format='PNG');return base64.b64encode(out.getvalue()).decode()


def test_personal_logo_date_workflow(isolated):
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as client:
            await client.get('/api/session')
            r=await client.post('/api/greetings',json={'festival_id':38,'brand':'Acme','sender_type':'company','logo_base64':logo_data(),'persona':'heritage','greeting_date':'2026-09-15','date_placement':'both','use_ai':False})
            assert r.status_code==202
            run=await wait_run(client,r.json()['id'])
            assert run['has_logo'] and 'logo_base64' not in run and 'festival_snapshot' not in run
            assert run['cost']['status']=='template' and run['cost']['inr']==0
            assert run['message'].endswith('15 Sep 2026')
            image=Image.open(BytesIO((await client.get('/api/greetings/'+run['id']+'/image.png')).content)).convert('RGB')
            assert image.getpixel((image.width-100,90))==(223,31,63)
            revised=await client.put('/api/greetings/'+run['id'],json={**{k:run[k] for k in ['headline','tagline','message','subline']},'greeting_date':'2026-09-16','date_placement':'text'})
            assert revised.status_code==200
            changed=revised.json()
            assert changed['date_is_custom'] and changed['message'].endswith('16 Sep 2026') and '15 Sep 2026' not in changed['message']
            assert changed['cost']==run['cost'] and changed['has_logo']
            r=await client.post('/api/greetings',json={'festival_id':38,'brand':'','sender_type':'individual','persona':'humorous','date_placement':'image','use_ai':False})
            anonymous=await wait_run(client,r.json()['id'])
            assert anonymous['subline']=='' and anonymous['brand']=='' and not anonymous['has_logo']
            assert 'Hridaan' not in anonymous['message'] and 'from' not in anonymous['message'].lower()
            r=await client.post('/api/greetings',json={'festival_id':38,'brand':'Prashant','sender_type':'individual','use_ai':False})
            person=await wait_run(client,r.json()['id'])
            assert person['subline']=='With warm wishes, Prashant'
            assert 'Prashant' in person['message']
    asyncio.run(flow())


def test_upload_and_option_validation(isolated):
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as client:
            await client.get('/api/session')
            assert len((await client.get('/api/options')).json()['personas'])==9
            for payload in [{'logo_base64':'invalid'},{'logo_base64':base64.b64encode(b'<svg onload="bad"/>').decode()},{'persona':'unknown'},{'greeting_date':'2026-02-30'},{'sender_type':'individual','logo_base64':logo_data()}]:
                r=await client.post('/api/greetings',json={'festival_id':0,'use_ai':False,**payload})
                assert r.status_code==422
            r=await client.post('/api/greetings',content=b'x'*(3*1024*1024+1),headers={'Content-Type':'application/json'})
            assert r.status_code==413
    asyncio.run(flow())


def test_costs_include_cached_tokens_and_missing_usage():
    ledger=[];token=costs.ledger_context.set(ledger)
    try:
        text=costs.start_call('gpt-4.1-mini','text');costs.finish_call(text,{'usage':{'input_tokens':1000,'input_tokens_details':{'cached_tokens':200},'output_tokens':100}})
        assert text['usd']==pytest.approx(.0005)
        image=costs.start_call('gpt-image-2','image');costs.finish_call(image,{'usage':{'input_tokens':100,'input_tokens_details':{'text_tokens':100,'image_tokens':0},'output_tokens':1000}})
        assert image['usd']==pytest.approx(.0305)
        fx={'usd_inr':90,'date':'2026-09-18','source':'test'}
        result=costs.summary(ledger,True,fx)
        assert result['inr']==pytest.approx(2.79) and result['status']=='calculated'
        costs.start_call('gpt-image-2','image')
        assert costs.summary(ledger,True,fx)['status']=='partial'
        assert costs.summary([],True,None)['status']=='partial'
        assert costs.summary([],True,None)['inr'] is None
        unknown=costs.start_call('other-model','text');costs.finish_call(unknown,{'usage':{'input_tokens':1,'output_tokens':2}})
        assert unknown['usd'] is None
    finally:costs.ledger_context.reset(token)


def test_concurrent_usage_isolation_and_persona():
    async def job(name):
        ledger=[];token=costs.ledger_context.set(ledger)
        c=ai_client.creative_context.set(personalization.instructions({'brand':name,'sender_type':'individual','persona':'futuristic'}))
        try:
            await asyncio.to_thread(costs.start_call,'gpt-4.1-mini','text')
            await asyncio.sleep(.01)
            assert len(ledger)==1
            assert name in ai_client.personalize('Style')
            return ledger
        finally:costs.ledger_context.reset(token);ai_client.creative_context.reset(c)
    async def run():
        a,b=await asyncio.gather(job('Alice'),job('Bob'))
        assert a is not b and a[0] is not b[0]
    asyncio.run(run())


def test_date_overlay_and_persona_change_pixels():
    from backend import festival_agents as engine
    run={'brand':'','sender_type':'individual','persona':'modern','greeting_date':'2026-09-15','date_placement':'image'}
    festival=personalization.festival_for(run,engine.FESTIVALS_2026[38])
    with_date=Image.open(BytesIO(engine.render_greeting_image(festival,'Happy Engineers Day',subline='')))
    festival['date_label']=''
    without=Image.open(BytesIO(engine.render_greeting_image(festival,'Happy Engineers Day',subline='')))
    assert ImageChops.difference(with_date,without).getbbox()
    heritage=personalization.festival_for({**run,'persona':'heritage'},engine.FESTIVALS_2026[38])
    assert heritage['palette']!=festival['palette']

def test_text_disabled_skips_all_text_api_calls(isolated, monkeypatch):
    from backend import festival_agents as engine
    async def forbidden(*args, **kwargs):
        raise AssertionError('Text API must not run')
    calls=[]
    def image_only(prompt,size):
        calls.append(prompt)
        entry=costs.start_call('gpt-image-2','image')
        costs.finish_call(entry,{'usage':{'input_tokens':10,'input_tokens_details':{'text_tokens':10,'image_tokens':0},'output_tokens':100}})
        output=BytesIO();Image.new('RGB',(100,150),'navy').save(output,format='PNG');return output.getvalue()
    monkeypatch.setattr(engine,'run_greeting_text_agent',forbidden)
    monkeypatch.setattr(engine,'run_greeting_blueprint_agent',forbidden)
    monkeypatch.setattr(engine,'run_image_prompt_agent',forbidden)
    monkeypatch.setattr(engine,'_call_openai_image_api',image_only)
    monkeypatch.setattr(costs,'exchange_rate',lambda:{'usd_inr':90,'date':'2026-09-18','source':'test'})
    monkeypatch.setenv('OPENAI_API_KEY','test-placeholder')
    assert studio.Generate(festival_id=0).generate_text is True
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app),base_url='http://test') as client:
            await client.get('/api/session')
            response=await client.post('/api/greetings',json={'festival_id':38,'brand':'','sender_type':'individual','use_ai':True,'generate_text':False})
            assert response.status_code==202
            run=await wait_run(client,response.json()['id'])
            assert run['status']=='ready_for_review' and run['image_source']=='ai'
            assert run['warnings']==[] and run['tagline']==''
            assert run['message']=="Happy Engineers' Day."
            assert len(run['cost']['calls'])==1 and run['cost']['calls'][0]['kind']=='image'
            assert run['cost']['status']=='calculated' and len(calls)==1
    asyncio.run(flow())

