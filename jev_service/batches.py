"""Persistent, sequential comparisons. Credentials exist only in the runner's memory."""
import asyncio
import json
import uuid

from fastapi import HTTPException
from pydantic import Field, field_validator

from .jobs import now
from .models import TERMINAL, JobRequest
from .providers import LLMChoice, Rates, default_llm


class BatchRequest(JobRequest):
    models: list[LLMChoice] = Field(min_length=1, max_length=10)
    helper_rates: Rates = Field(default_factory=Rates)
    jev_rates: Rates = Field(default_factory=Rates)

    @field_validator('models')
    @classmethod
    def distinct_models(cls, models):
        if len({m.provider for m in models}) != 1:
            raise ValueError('Choose models from one provider')
        if len({m.model for m in models}) != len(models):
            raise ValueError('Choose each model only once')
        return models


class BatchManager:
    def __init__(self, jobs, baseline):
        self.jobs, self.baseline = jobs, baseline
        self.db = jobs.db
        self.db.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()
        self.active_id = None
        self.task = None
        for (body,) in self.db.execute('SELECT body FROM batches').fetchall():
            batch = json.loads(body)
            if batch['status'] == 'running':
                batch['status'] = 'interrupted'
                batch['error'] = 'Service restarted. Unfinished runs were not replayed.'
                for row in batch['runs']:
                    if row['status'] not in TERMINAL:
                        row['status'] = 'interrupted' if row.get('job_id') else 'not_run'
                self.save(batch)

    def save(self, batch):
        batch['updated_at'] = now()
        self.db.execute('INSERT INTO batches VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                        (batch['id'], json.dumps(batch)))
        self.db.commit()

    def require_idle(self):
        if self.active_id or any(m.db.execute(
                "SELECT count(*) FROM jobs WHERE json_extract(body,'$.status') IN ('queued','running')"
                ).fetchone()[0] for m in (self.jobs, self.baseline)):
            raise HTTPException(409, 'Wait for the current jobs or model comparison to finish')

    def manager(self, row):
        return self.jobs if row['kind'] == 'jev' else self.baseline

    def refresh_row(self, row):
        if row.get('job_id'):
            job = self.manager(row).get(row['job_id'])
            if job:
                result, progress = job.get('result') or {}, job['progress']
                row.update(status=job['status'], error=job.get('error'),
                           verification=result.get('verification', 'not_requested'),
                           usage=result.get('usage') or progress.get('usage', {}),
                           execution_ms=progress.get('execution_ms'),
                           started_at=progress.get('started_at'),
                           steps=result.get('steps', progress.get('steps', 0)),
                           url=result.get('page', {}).get('url', progress.get('url')))
        return row

    def get(self, batch_id):
        row = self.db.execute('SELECT body FROM batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Comparison batch not found')
        batch = json.loads(row[0])
        for run in batch['runs']:
            self.refresh_row(run)
        return batch

    def start(self, body, key):
        self.require_idle()
        batch_id = str(uuid.uuid4())
        request = JobRequest.model_validate(body.model_dump(exclude={'models', 'helper_rates', 'jev_rates'}))
        request = request.model_copy(update={'capture_screenshots': False, 'isolate_browser': True})
        batch = {'id': batch_id, 'status': 'running', 'created_at': now(), 'updated_at': now(),
                 'request': request.model_dump(mode='json'), 'cancel_requested': False,
                 'helper_rates': body.helper_rates.model_dump(), 'jev_rates': body.jev_rates.model_dump(),
                 'runs': [{'kind': 'jev', 'model': 'Jev', 'status': 'pending'},
                          *[{'kind': 'llm', **m.model_dump(), 'status': 'pending'} for m in body.models]]}
        self.save(batch)
        self.active_id = batch_id
        self.task = asyncio.create_task(self.run(batch, request, key))
        return batch

    def cancel(self, batch_id):
        batch = self.get(batch_id)
        if batch['status'] == 'running':
            batch['cancel_requested'] = True
            self.save(batch)
            for row in batch['runs']:
                if row.get('job_id') and row['status'] not in TERMINAL:
                    self.manager(row).cancel(row['job_id'])
        return self.get(batch_id)

    async def run(self, batch, request, key):
        try:
            for index in range(len(batch['runs'])):
                batch = self.get(batch['id'])
                if batch['cancel_requested']:
                    break
                row = batch['runs'][index]
                runtime = None if row['kind'] == 'jev' else {
                    'key': key, 'provider': row['provider'], 'model': row['model']}
                metadata = {'batch_id': batch['id']}
                if runtime:
                    metadata['model_config'] = {k: row[k] for k in ('provider', 'model', 'rates')}
                else:
                    row['helper_model'] = default_llm()['model']
                job = self.manager(row).submit(request, runtime=runtime, metadata=metadata)
                row.update(job_id=job['id'], status=job['status'])
                self.save(batch)
                while self.manager(row).get(job['id'])['status'] not in TERMINAL:
                    await asyncio.sleep(.1)
                # Fetch again so cancellation cannot be overwritten by a stale record.
                batch = self.get(batch['id'])
                self.save(batch)
            batch = self.get(batch['id'])
            batch['status'] = 'cancelled' if batch['cancel_requested'] else 'completed'
        except asyncio.CancelledError:
            batch = self.get(batch['id'])
            for row in batch['runs']:
                if row.get('job_id') and row['status'] not in TERMINAL:
                    self.manager(row).cancel(row['job_id'])
            batch['status'] = 'interrupted'
            batch['error'] = 'Service stopped. Unfinished runs were not replayed.'
        except Exception:
            batch = self.get(batch['id'])
            for row in batch['runs']:
                if row.get('job_id') and row['status'] not in TERMINAL:
                    self.manager(row).cancel(row['job_id'])
            batch['status'] = 'failed'
            batch['error'] = 'Batch coordination failed. Unfinished runs were not replayed.'
        finally:
            for row in batch['runs']:
                if not row.get('job_id'):
                    row['status'] = 'not_run'
            self.save(batch)
            self.active_id = None

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task
