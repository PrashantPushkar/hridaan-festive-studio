"""Personal OpenAI adapter for the extracted greeting and art-direction prompts."""
import asyncio
import contextvars
import json
import os
import urllib.error
import urllib.request
from . import costs

brand_context = contextvars.ContextVar('brand', default='Hridaan Labs')
creative_context = contextvars.ContextVar('creative', default='')


def personalize(text):
    return text.replace('Hridaan Labs', brand_context.get() or 'the sender') + '\n\nCURRENT GREETING REQUIREMENTS (override generic brand and style defaults):\n' + creative_context.get()


def get_client():
    if not os.environ.get('OPENAI_API_KEY'):
        raise RuntimeError('The personal OpenAI key has not been configured.')
    return True


def _request(system, user, schema, max_tokens):
    payload = {
        'model': os.environ.get('OPENAI_TEXT_MODEL', 'gpt-4.1-mini'),
        'instructions': personalize(system),
        'input': user,
        'max_output_tokens': max_tokens,
        'store': False,
    }
    if schema:
        payload['text'] = {'format': {'type': 'json_schema', 'name': 'greeting_content', 'strict': True, 'schema': schema}}
    request = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'},
        method='POST',
    )
    entry = costs.start_call(payload['model'], 'text')
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'OpenAI returned HTTP {exc.code}; check the personal project access and billing.') from None
    costs.finish_call(entry, result)
    if result.get('status') != 'completed':
        raise RuntimeError('OpenAI did not finish generating the copy. Please try again.')
    text = ''.join(part.get('text', '') for item in result.get('output', []) for part in item.get('content', []) if part.get('type') == 'output_text')
    if not text:
        raise RuntimeError('No greeting was returned. Try another occasion or wording.')
    return text


async def _create(client, system, user, schema, max_tokens):
    return await asyncio.to_thread(_request, system, user, schema, max_tokens)


def _first_text(response):
    return response


def extract_json_object(text):
    return json.loads(text)

