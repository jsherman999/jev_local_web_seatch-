"""Fixed provider endpoints; keys are never probed against multiple services."""
import os
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .models import JobRequest

Provider = Literal['openai', 'deepseek', 'groq', 'openrouter', 'google', 'anthropic']
PROVIDERS = {
    'openai': ('OpenAI', 'https://api.openai.com/v1'),
    'deepseek': ('DeepSeek', 'https://api.deepseek.com/v1'),
    'groq': ('Groq', 'https://api.groq.com/openai/v1'),
    'openrouter': ('OpenRouter', 'https://openrouter.ai/api/v1'),
    'google': ('Google', 'https://generativelanguage.googleapis.com/v1beta/openai'),
    'anthropic': ('Anthropic', 'https://api.anthropic.com/v1'),
}


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Provider | None = None


class Rates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class LLMChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Provider
    model: str = Field(min_length=1, max_length=200, pattern=r'^[\w./:+-]+$')
    rates: Rates = Field(default_factory=Rates)


class ComparisonRequest(JobRequest):
    llm: LLMChoice | None = None
    baseline_rates: Rates | None = None


def detect_provider(key):
    for prefix, provider in [('sk-ant-', 'anthropic'), ('sk-or-v1-', 'openrouter'),
                             ('sk-proj-', 'openai'), ('sk-svcacct-', 'openai'),
                             ('gsk_', 'groq'), ('AIza', 'google')]:
        if key.startswith(prefix):
            return provider
    return None


def resolve_provider(key, requested=None):
    if not key or len(key) > 4096 or any(c.isspace() for c in key):
        raise HTTPException(400, 'Enter a valid API key.')
    detected = detect_provider(key)
    if detected and requested and detected != requested:
        raise HTTPException(400, 'Key prefix does not match the selected provider.')
    provider = detected or requested
    if not provider:
        raise HTTPException(400, 'This key format is shared by providers. Choose its provider first.')
    if provider not in PROVIDERS:
        raise HTTPException(400, 'Unsupported provider.')
    return provider


def default_llm():
    base = os.environ.get('TEXT_MODEL_BASE_URL', 'https://api.deepseek.com/v1').rstrip('/')
    provider = next((p for p, (_, url) in PROVIDERS.items() if url == base), None)
    model = os.environ.get('TEXT_MODEL', 'deepseek-chat')
    rates = {'input': 2.5, 'cached': .25, 'output': 15} if provider == 'openai' and model in {
        'gpt-5.4', 'gpt-5.4-2026-03-05'} else {}
    return {'provider': provider, 'label': PROVIDERS[provider][0] if provider else 'Regular LLM',
            'model': model, 'rates': rates}


def headers(provider, key):
    if provider == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    return {'Authorization': 'Bearer ' + key}


async def catalog(key, requested=None):
    provider = resolve_provider(key, requested)
    params, models = {}, []
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        try:
            # Claude paginates; other supported catalogs currently return a full data array.
            for _ in range(100):
                response = await client.get(PROVIDERS[provider][1] + '/models',
                                            headers=headers(provider, key), params=params)
                if not response.is_success:
                    raise HTTPException(400, f'{PROVIDERS[provider][0]} model listing failed '
                                        f'(HTTP {response.status_code}). Check the key and its permissions.')
                data = response.json()
                for item in data['data']:
                    model = item['id']
                    # Keep every catalog entry visible, label clearly non-chat models.
                    unsupported = any(x in model.lower() for x in (
                        'embedding', 'whisper', 'tts-', 'dall-e', 'moderation', 'sora-',
                        'realtime', 'transcribe', 'image', 'deep-research', 'codex'))
                    if provider == 'openai' and ('-pro' in model or model in {'babbage-002', 'davinci-002'}):
                        unsupported = True
                    models.append({'id': model, 'name': item.get('display_name', model),
                                   'supported': not unsupported})
                if not data.get('has_more'):
                    break
                cursor = data.get('last_id')
                if not cursor or params.get('after_id') == cursor:
                    raise ValueError('Invalid pagination')
                params = {'after_id': cursor, 'limit': 100}
            else:
                raise ValueError('Catalog pagination limit reached')
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, 'Could not load the provider model catalog. Try again.') from None
    return {'provider': provider, 'label': PROVIDERS[provider][0],
            'models': sorted({m['id']: m for m in models}.values(), key=lambda m: m['id'])}


def anthropic_completion(key, body):
    """Translate the baseline's chat request without changing the Jev text helper."""
    payload = {'model': body['model'], 'max_tokens': body.get('max_tokens', 2048),
               'system': '\n'.join(m['content'] for m in body['messages'] if m['role'] == 'system'),
               'messages': [m for m in body['messages'] if m['role'] != 'system']}
    with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
        response = client.post(PROVIDERS['anthropic'][1] + '/messages',
                               headers=headers('anthropic', key), json=payload)
        if not response.is_success:
            raise RuntimeError(f'Anthropic request failed (HTTP {response.status_code}).')
        result = response.json()
    usage = result.get('usage', {})
    incoming = usage.get('input_tokens')
    cached = usage.get('cache_read_input_tokens', 0)
    created = usage.get('cache_creation_input_tokens', 0)
    return {'model': result.get('model', body['model']),
            'choices': [{'message': {'content': ''.join(c.get('text', '') for c in result['content']
                                                      if c['type'] == 'text')}}],
            'usage': {'prompt_tokens': incoming + cached + created if incoming is not None else None,
                      'completion_tokens': usage.get('output_tokens'),
                      'prompt_tokens_details': {'cached_tokens': cached}}}
