"""Per-generation usage accounting. Standard API list prices, USD per 1M tokens."""
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import json
import threading
import time
import urllib.request

PRICING_DATE = '2026-09-18'
PRICING_SOURCE = 'https://developers.openai.com/api/docs/pricing'
# Standard synchronous rates, NOT Batch API rates.
RATES = {'gpt-4.1-mini': {'input':.4,'cached':.1,'output':1.6},
         'gpt-image-2': {'text':5,'image':8,'cached_text':1.25,'cached_image':2,'output':30}}
ledger_context = ContextVar('usage_ledger', default=None)
_fx = None
_fx_checked = 0
_fx_lock = threading.Lock()


def start_call(model, kind):
    ledger = ledger_context.get()
    entry = {'model':model,'kind':kind,'usage':None,'usd':None}
    if ledger is not None:
        ledger.append(entry)
    return entry


def finish_call(entry, result):
    usage = result.get('usage')
    entry['usage'] = usage
    model = entry['model']
    key = next((key for key in RATES if model == key or model.startswith(key+'-20')), None)
    if not key or not isinstance(usage, dict):
        return
    try:
        incoming, outgoing = int(usage['input_tokens']), int(usage['output_tokens'])
        if min(incoming, outgoing) < 0: return
        details = usage.get('input_tokens_details') or {}
        cached = int(details.get('cached_tokens', 0))
        rates = RATES[key]
        if entry['kind'] == 'text':
            if not 0 <= cached <= incoming: return
            parts = [(incoming-cached,rates['input']),(cached,rates['cached']),(outgoing,rates['output'])]
        else:
            text, image = int(details['text_tokens']), int(details['image_tokens'])
            if min(text,image) < 0 or text+image != incoming: return
            split = details.get('cached_tokens_details') or {}
            if cached and image:
                ct,ci = int(split['text_tokens']),int(split['image_tokens'])
            else:
                ct,ci = cached,0
            if ct+ci != cached or not 0 <= ct <= text or not 0 <= ci <= image: return
            parts=[(text-ct,rates['text']),(image-ci,rates['image']),(ct,rates['cached_text']),(ci,rates['cached_image']),(outgoing,rates['output'])]
        entry['usd'] = float(sum(Decimal(n)*Decimal(str(rate)) for n,rate in parts)/Decimal(1000000))
        entry['rates_per_million_usd'] = dict(rates)
    except (KeyError,TypeError,ValueError):
        return  # Never turn missing or unfamiliar usage into a zero-cost claim.


def exchange_rate():
    global _fx, _fx_checked
    with _fx_lock:
        if time.time()-_fx_checked < 86400:
            return _fx
        _fx_checked=time.time()
        try:
            with urllib.request.urlopen('https://open.er-api.com/v6/latest/USD',timeout=6) as response:
                data=json.load(response)
            rate=float(data['rates']['INR'])
            if data.get('result') != 'success' or data.get('base_code') != 'USD' or not 1 < rate < 1000: return _fx
            _fx={'usd_inr':rate,'date':datetime.fromtimestamp(data['time_last_update_unix'],timezone.utc).date().isoformat(),'source':'https://www.exchangerate-api.com'}
        except Exception:
            pass
        return _fx


def summary(entries, use_ai, fx):
    complete = not use_ai or bool(entries) and all(e['usd'] is not None for e in entries)
    usd = sum(Decimal(str(e['usd'])) for e in entries if e['usd'] is not None)
    converted = float(usd*Decimal(str(fx['usd_inr']))) if fx else (0.0 if not use_ai else None)
    return {'status':'template' if not use_ai else ('calculated' if complete else 'partial'),
            'usd':float(usd),'inr':converted,'fx':fx,'calls':entries,
            'pricing_date':PRICING_DATE,'pricing_source':PRICING_SOURCE,
            'note':'Calculated from reported usage and published standard rates; excludes tax, billing adjustments and card FX fees. Missing usage is not counted as free.'}

