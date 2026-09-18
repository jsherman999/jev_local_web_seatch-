import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from test_service import BODY
from test_service import client as service_client

from jev_service import providers
from jev_service.models import TERMINAL

client = service_client


def test_detection_never_guesses_shared_prefix():
    assert providers.detect_provider('sk-proj-example') == 'openai'
    assert providers.detect_provider('sk-ant-example') == 'anthropic'
    assert providers.detect_provider('gsk_example') == 'groq'
    with pytest.raises(HTTPException) as error:
        providers.resolve_provider('sk-shared-format')
    assert 'Choose its provider' in error.value.detail
    assert providers.resolve_provider('sk-shared-format', 'deepseek') == 'deepseek'
    with pytest.raises(HTTPException):
        providers.resolve_provider('sk-proj-example', 'deepseek')


def test_catalog_pagination_and_credential_destination(monkeypatch):
    seen = []
    def respond(request):
        seen.append(request)
        assert request.url.host == 'api.anthropic.com'
        assert request.headers['x-api-key'] == 'sk-ant-test'
        if len(seen) == 1:
            return httpx.Response(200, json={'data': [{'id': 'claude-a'}], 'has_more': True,
                                           'last_id': 'claude-a'})
        assert request.url.params['after_id'] == 'claude-a'
        return httpx.Response(200, json={'data': [{'id': 'claude-b'}], 'has_more': False})
    original = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    result = asyncio.run(providers.catalog('sk-ant-test'))
    assert [m['id'] for m in result['models']] == ['claude-a', 'claude-b']


def test_catalog_error_does_not_echo_credentials_or_follow_redirect(monkeypatch):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(302, headers={'Location': 'https://evil.test'}, text='sk-proj-secret')
    original = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(HTTPException) as error:
        asyncio.run(providers.catalog('sk-proj-secret'))
    assert 'secret' not in str(error.value.detail)
    assert len(seen) == 1


def test_selected_model_only_reaches_baseline_and_key_never_persists(client, monkeypatch):
    async def catalog(key, provider):
        assert key == 'sk-private-test'
        return {'provider': 'deepseek', 'label': 'DeepSeek',
                'models': [{'id': 'test-model', 'supported': True}]}
    monkeypatch.setattr('jev_service.demo.catalog', catalog)
    response = client.post('/v1/comparisons', headers={'X-LLM-API-Key': 'sk-private-test'},
                           json={**BODY, 'llm': {'provider': 'deepseek', 'model': 'test-model',
                                                'rates': {'input': 1, 'output': 2}}})
    assert response.status_code == 202
    comparison = response.json()
    for _ in range(150):
        comparison = client.get('/v1/comparisons/' + comparison['id']).json()
        if all(comparison[s]['status'] in TERMINAL for s in ('jev', 'llm')):
            break
        time.sleep(.02)
    assert comparison['llm']['result']['page']['title'] == 'test-model'
    assert comparison['jev']['result']['page']['title'] == 'Example'
    assert comparison['llm']['progress']['model_config']['rates']['input'] == 1
    assert 'sk-private-test' not in json.dumps(comparison)
    for manager in [client.app.state.jobs, client.app.state.baseline]:
        assert not manager.ephemeral
        rows = client.portal.call(lambda: manager.db.execute('select body from jobs').fetchall())
        assert all('sk-private-test' not in row[0] for row in rows)


def test_model_endpoint_rejects_ambiguous_key_without_network(client):
    response = client.post('/v1/demo/models', headers={'X-LLM-API-Key': 'sk-ambiguous'}, json={})
    assert response.status_code == 400
    assert 'sk-ambiguous' not in response.text
    assert client.post('/v1/demo/models', json={}).status_code == 400


def test_anthropic_translation_and_usage(monkeypatch):
    def respond(request):
        body = json.loads(request.content)
        assert request.url.path == '/v1/messages'
        assert body['system'] == 'Return JSON'
        assert body['messages'] == [{'role': 'user', 'content': 'page'}]
        return httpx.Response(200, json={'model': 'claude-test',
            'content': [{'type': 'text', 'text': '{"operation":"DONE"}'}],
            'usage': {'input_tokens': 100, 'output_tokens': 20, 'cache_read_input_tokens': 50}})
    original = httpx.Client
    monkeypatch.setattr(providers.httpx, 'Client',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    result = providers.anthropic_completion('sk-ant-test', {'model': 'claude-test',
        'messages': [{'role': 'system', 'content': 'Return JSON'}, {'role': 'user', 'content': 'page'}]})
    assert result['usage']['prompt_tokens'] == 150
    assert result['usage']['prompt_tokens_details']['cached_tokens'] == 50
    assert json.loads(result['choices'][0]['message']['content'])['operation'] == 'DONE'
