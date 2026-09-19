import asyncio
import json
import time

import pytest
from pydantic import ValidationError
from test_service import BODY, COMMAND
from test_service import client as service_client

from jev_service.batches import BatchManager, BatchRequest
from jev_service.jobs import JobManager
from jev_service.models import TERMINAL

client = service_client


def body(count=2, goal='read'):
    return {**BODY, 'goal': goal, 'models': [
        {'provider': 'deepseek', 'model': f'model-{i}', 'rates': {'input': i+1, 'output': 2}}
        for i in range(count)]}


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    async def listing(key, provider):
        assert key == 'sk-batch-test-secret'
        return {'provider': provider, 'label': 'DeepSeek',
                'models': [{'id': f'model-{i}', 'supported': True} for i in range(10)]}
    monkeypatch.setattr('jev_service.demo.catalog', listing)


HEADERS = {'X-LLM-API-Key': 'sk-batch-test-secret'}


def finish(client, batch_id):
    for _ in range(300):
        batch = client.get('/v1/batches/' + batch_id).json()
        if batch['status'] != 'running':
            return batch
        time.sleep(.02)
    pytest.fail('Batch did not finish')


def test_ten_models_run_once_sequentially_without_storing_key(client):
    response = client.post('/v1/batches', json=body(10), headers=HEADERS)
    assert response.status_code == 202
    batch = finish(client, response.json()['id'])
    assert batch['status'] == 'completed'
    assert len(batch['runs']) == 11
    assert all(r['status'] == 'completed' for r in batch['runs'])
    assert len({r['job_id'] for r in batch['runs']}) == 11
    def inspect():
        managers = client.app.state.jobs, client.app.state.baseline
        jobs = [managers[0 if r['kind'] == 'jev' else 1].get(r['job_id']) for r in batch['runs']]
        for previous, following in zip(jobs, jobs[1:]):
            assert previous['updated_at'] <= following['progress']['started_at']
        for job in jobs:
            assert job['request']['isolate_browser'] is True
            assert job['request']['capture_screenshots'] is False
        assert jobs[0]['result']['page']['title'] == 'Example'
        assert jobs[1]['result']['page']['title'] == 'model-0'
        for m in managers:
            assert not m.ephemeral
            assert 'sk-batch-test-secret' not in json.dumps(m.list())
        assert 'sk-batch-test-secret' not in client.app.state.batches.db.execute(
            'SELECT body FROM batches WHERE id=?', (batch['id'],)).fetchone()[0]
    client.portal.call(inspect)


def test_cancel_stops_active_run_skips_rest_and_releases_reservation(client):
    batch_id = client.post('/v1/batches', json=body(goal='wait'), headers=HEADERS).json()['id']
    assert client.post('/v1/jobs', json=BODY).status_code == 409
    assert client.post('/v1/comparisons', json=BODY).status_code == 409
    assert client.post('/v1/batches', json=body(), headers=HEADERS).status_code == 409
    assert client.post('/v1/batches/' + batch_id + '/cancel').status_code == 200
    batch = finish(client, batch_id)
    assert batch['status'] == 'cancelled'
    assert all(r['status'] in {'cancelled', 'not_run'} for r in batch['runs'])
    assert not client.app.state.batches.active_id
    assert client.post('/v1/jobs', json=BODY).status_code == 202


def test_failed_run_does_not_prevent_remaining_models(client):
    batch = finish(client, client.post('/v1/batches', json=body(goal='crash'), headers=HEADERS).json()['id'])
    assert batch['status'] == 'completed'
    assert all(r['status'] == 'failed' for r in batch['runs'])
    assert all(r['execution_ms'] is not None for r in batch['runs'])


def test_limits_duplicates_provider_and_auth(client, monkeypatch):
    for invalid in [body(0), body(11), {**body(), 'models': [body()['models'][0]]*2},
                    {**body(), 'models': [body()['models'][0], {'provider': 'openai', 'model': 'other'}]}]:
        assert client.post('/v1/batches', json=invalid, headers=HEADERS).status_code == 422
    with pytest.raises(ValidationError):
        BatchRequest(**{**body(), 'helper_rates': {'input': -1}})
    monkeypatch.setenv('JEV_API_TOKEN', 'auth-secret')
    assert client.post('/v1/batches', json=body(), headers=HEADERS).status_code == 401
    assert client.get('/v1/batches/unknown').status_code == 401
    assert client.post('/v1/batches/unknown/cancel').status_code == 401


def test_restart_never_replays_or_reuses_key(tmp_path):
    async def scenario():
        jobs, baseline = JobManager(tmp_path, COMMAND), JobManager(tmp_path/'baseline', COMMAND)
        jobs.start()
        baseline.start()
        batches = BatchManager(jobs, baseline)
        batch_id = batches.start(BatchRequest(**body(goal='wait')), 'sk-batch-test-secret')['id']
        await asyncio.sleep(.05)
        await batches.close()
        await baseline.close()
        await jobs.close()
        jobs, baseline = JobManager(tmp_path, COMMAND), JobManager(tmp_path/'baseline', COMMAND)
        jobs.start()
        baseline.start()
        recovered = BatchManager(jobs, baseline)
        assert recovered.get(batch_id)['status'] == 'interrupted'
        assert recovered.active_id is None
        assert recovered.task is None
        assert jobs.queue.empty() and baseline.queue.empty()
        assert all(r['status'] in TERMINAL | {'not_run'} for r in recovered.get(batch_id)['runs'])
        await recovered.close()
        await baseline.close()
        await jobs.close()
    asyncio.run(scenario())
