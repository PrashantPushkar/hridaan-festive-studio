import asyncio
from io import BytesIO
import os
from pathlib import Path
import sys

import httpx
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import app as studio
from backend import festival_agents as engine


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, 'DATA', tmp_path)
    monkeypatch.setattr(studio, 'PASSWORD', '')
    monkeypatch.setattr(studio, 'ACCOUNT_MODE', False)
    monkeypatch.setattr(studio, 'generation_lock', asyncio.Semaphore(2))
    monkeypatch.setattr(studio, 'edit_lock', asyncio.Lock())
    monkeypatch.setenv('DAILY_GENERATION_LIMIT', '20')
    studio.initialize()
    return tmp_path


async def wait_run(client, run_id):
    for _ in range(1200):
        run = (await client.get('/api/greetings/' + run_id)).json()
        if run['status'] not in ('queued', 'generating'):
            return run
        await asyncio.sleep(.05)
    raise AssertionError('Greeting did not complete')


def test_complete_template_flow_and_session_isolation(isolated):
    async def flow():
        transport = httpx.ASGITransport(app=studio.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client, httpx.AsyncClient(transport=transport, base_url='http://test') as other:
            assert (await client.get('/api/session')).json()['authenticated']
            festivals = (await client.get('/api/festivals')).json()
            diwali = next(f for f in festivals if f['name'] == 'Diwali')
            response = await client.post('/api/greetings', json={'festival_id': diwali['id'], 'brand': 'Hridaan Labs', 'channel':'instagram_feed', 'use_ai':False})
            assert response.status_code == 202
            run = await wait_run(client, response.json()['id'])
            assert run['status'] == 'ready_for_review'
            assert run['image_source'] == 'template'
            assert 'owner' not in run
            image = await client.get('/api/greetings/' + run['id'] + '/image.png')
            assert Image.open(BytesIO(image.content)).size == (1080, 1350)
            assert (await other.get('/api/greetings/' + run['id'])).status_code == 404
            assert (await other.get('/api/greetings/' + run['id'] + '/image.png')).status_code == 404
            edited = await client.put('/api/greetings/' + run['id'], json={'headline':'A brighter Diwali', 'tagline':'Light, love and new beginnings', 'message':'Wishing you a beautiful Diwali.', 'subline':'With love from Hridaan Labs'})
            assert edited.status_code == 200
            assert edited.json()['revision'] == 2
            updated = await client.get('/api/greetings/' + run['id'] + '/image.png')
            assert updated.content != image.content
            approved = await client.post('/api/greetings/' + run['id'] + '/approve')
            assert approved.json()['status'] == 'approved'
            history = (await client.get('/api/greetings')).json()
            assert len(history) == 1 and history[0]['headline'] == 'A brighter Diwali'
            assert (await client.post('/api/greetings/' + run['id'] + '/reject')).json()['status'] == 'rejected'
            assert (await client.post('/api/greetings/' + run['id'] + '/approve')).status_code == 409
    asyncio.run(flow())


@pytest.mark.parametrize('channel', list(engine.FORMAT_SPECS))
def test_all_original_export_dimensions(channel):
    festival = dict(engine.FESTIVALS_2026[0], brand='A Personal Brand')
    png = engine.render_greeting_image(festival, 'A bright new beginning', channel=channel, subline='From your friends', tagline='Here is to possibility')
    spec = engine.FORMAT_SPECS[channel]
    assert Image.open(BytesIO(png)).size == (spec['width'], spec['height'])


def test_auth_csrf_validation_and_daily_limit(isolated, monkeypatch):
    async def flow():
        transport = httpx.ASGITransport(app=studio.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            monkeypatch.setattr(studio, 'PASSWORD', 'a-private-studio-password')
            assert (await client.get('/api/greetings')).status_code == 401
            assert (await client.post('/api/login', json={'password':'wrong'})).status_code == 401
            assert (await client.post('/api/login', json={'password':'a-private-studio-password'})).status_code == 200
            assert (await client.get('/api/greetings')).status_code == 200
            assert (await client.post('/api/greetings', json={'festival_id':0, 'use_ai':False}, headers={'Origin':'https://another-site.example'})).status_code == 403
            assert (await client.post('/api/greetings', json={'festival_id':9999, 'use_ai':False})).status_code == 422
            assert (await client.post('/api/greetings', json={'festival_id':0, 'brand':'   ', 'use_ai':False})).status_code == 422
            monkeypatch.setenv('DAILY_GENERATION_LIMIT', '0')
            assert (await client.post('/api/greetings', json={'festival_id':0,'use_ai':False})).status_code == 429
            assert (await client.get('/.env.local')).status_code == 404
            assert (await client.get('/assets/../.env.local')).status_code == 404
    asyncio.run(flow())


def test_ai_failure_is_visible_and_does_not_claim_ai_artwork(isolated, monkeypatch):
    async def no_text(*args, **kwargs):
        raise RuntimeError('Simulated API failure')
    async def no_image(*args, **kwargs):
        return None, None
    monkeypatch.setattr(engine, 'run_greeting_text_agent', no_text)
    monkeypatch.setattr(engine, 'generate_ai_background', no_image)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url='http://test') as client:
            await client.get('/api/session')
            response = await client.post('/api/greetings', json={'festival_id':0, 'use_ai':True})
            run = await wait_run(client, response.json()['id'])
            assert run['status'] == 'ready_for_review'
            assert run['image_source'] == 'template'
            assert len(run['warnings']) == 2
    asyncio.run(flow())

def test_expanded_catalogue_preserves_saved_ids():
    from datetime import date
    expected_names = ["New Year's Day", 'Lohri', 'Makar Sankranti / Pongal', 'Vasant Panchami', 'Republic Day', 'Maha Shivratri', 'Holi', 'Ram Navami', 'Eid al-Fitr', 'Mahavir Jayanti', 'Good Friday', 'Easter Sunday', 'Baisakhi', 'Buddha Purnima', 'Eid al-Adha', 'Muharram', 'Rath Yatra', 'Guru Purnima', 'Independence Day', 'Eid-e-Milad', 'Onam', 'Raksha Bandhan', 'Janmashtami', "Teachers' Day", 'Ganesh Chaturthi', 'Gandhi Jayanti', 'Sharad Navratri Begins', 'Dussehra', 'Karva Chauth', 'Dhanteras', 'Naraka Chaturdashi', 'Diwali', 'Govardhan Puja', 'Bhai Dooj', "Children's Day", 'Chhath Puja', 'Guru Nanak Jayanti', 'Christmas']
    assert [f["name"] for f in engine.FESTIVALS_2026[:38]] == expected_names
    names = [f['name'] for f in engine.FESTIVALS_2026]
    assert len(names) == len(set(names)) == 95
    for festival in engine.FESTIVALS_2026[38:]:
        assert date.fromisoformat(festival['date']).year == 2026
        assert festival['source_urls'] and all(url.startswith('https://') for url in festival['source_urls'])
        assert festival['regions'] and festival['aliases'] and festival['date_note']
    by_name = {f['name']: f for f in engine.FESTIVALS_2026}
    assert by_name["Engineers' Day"]['date'] == '2026-09-15'
    assert by_name['Karnataka Rajyotsava']['date'] == '2026-11-01'
    assert 'Rajotsava' in ' '.join(by_name['Karnataka Rajyotsava']['aliases'])
    assert by_name['Vishwakarma Puja']['date'] == '2026-09-18'
    assert '17 September' in by_name['Vishwakarma Puja']['date_note']
    assert studio.default_headline(by_name['Me-Dam-Me-Phi']) == 'Me-Dam-Me-Phi'


def test_new_occasion_generates_template(isolated):
    async def flow():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url='http://test') as client:
            await client.get('/api/session')
            festivals = (await client.get('/api/festivals')).json()
            occasion = next(f for f in festivals if f['name'] == "Engineers' Day")
            response = await client.post('/api/greetings', json={'festival_id':occasion['id'],'brand':'Hridaan Labs','channel':'instagram_feed','use_ai':False})
            assert response.status_code == 202
            run = await wait_run(client, response.json()['id'])
            assert run['status'] == 'ready_for_review'
            assert run['headline'] == "Happy Engineers' Day"
            assert run['image_source'] == 'template'
    asyncio.run(flow())

