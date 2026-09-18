import asyncio
import fcntl
import json
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

from .config import ROOT
from .models import TERMINAL, Job


def now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """All mutations run on the single API event loop; SQLite persists every transition."""

    def __init__(self, data, command=None):
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (data / "service.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Another Jev API process is using this data directory") from None
        self.db = sqlite3.connect(data / "jobs.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.commit()
        self.command = command or [sys.executable, "-m", "jev_service.worker"]
        self.queue = asyncio.Queue()
        self.runner = None
        self.process = None
        self.active_id = None
        self.cancel_event = asyncio.Event()
        self.ephemeral = {}

    def save(self, job):
        job["updated_at"] = now()
        self.db.execute(
            "INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (job["id"], json.dumps(job)),
        )
        self.db.commit()
        return job

    def get(self, job_id):
        row = self.db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit=50, offset=0):
        rows = self.db.execute("SELECT body FROM jobs ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset))
        return [json.loads(r[0]) for r in rows]

    def start(self):
        for (body,) in self.db.execute("SELECT body FROM jobs").fetchall():
            job = json.loads(body)
            if job["status"] not in TERMINAL:
                job.update(
                    status="interrupted", error="Service restarted. Job was not replayed automatically."
                )
                self.save(job)
        self.runner = asyncio.create_task(self.loop())

    def submit(self, request, *, runtime=None, metadata=None):
        active = self.db.execute(
            "SELECT count(*) FROM jobs WHERE json_extract(body,'$.status') IN ('queued','running')"
        ).fetchone()[0]
        if active >= 100:
            raise OverflowError("Queue capacity of 100 jobs reached")
        job = Job(id=str(uuid.uuid4()), status="queued", created_at=now(), updated_at=now(), request=request)
        job.progress.update(metadata or {})
        saved = self.save(job.model_dump(mode="json"))
        if runtime:
            self.ephemeral[job.id] = runtime
        self.queue.put_nowait(saved["id"])
        return saved

    def cancel(self, job_id):
        job = self.get(job_id)
        if not job or job["status"] in TERMINAL:
            return job
        if job_id == self.active_id:
            # Do not claim cancellation finished until the subprocess has stopped.
            self.cancel_event.set()
            job["progress"]["cancellation_requested"] = True
        else:
            self.ephemeral.pop(job_id, None)
            job.update(status="cancelled", error="Cancelled before execution")
        return self.save(job)

    async def stop_process(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def execute(self, job):
        runtime = self.ephemeral.pop(job['id'], None)
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2_000_000,
        )
        payload = {**job['request']}
        if runtime:
            payload['_llm'] = runtime
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()
        self.process.stdin.close()
        result = None
        while line := await self.process.stdout.readline():
            if runtime:
                line = line.replace(runtime['key'].encode(), b'[redacted]')
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if event.get("event") == "progress":
                job["progress"].update({k: v for k, v in event.items() if k != "event"})
                self.save(job)
            elif event.get("event") == "trace":
                trace = job["progress"].setdefault("trace", [])
                trace.append({k: v for k, v in event.items() if k != "event"})
                # Public decision budget bounds normal runs well below this cap.
                if len(trace) > 1200:
                    del trace[:-1200]
                    job["progress"]["trace_truncated"] = True
                self.save(job)
            elif event.get("event") == "result":
                result = event
        await self.process.wait()
        if result is None:
            raise RuntimeError("Browser worker exited without a result")
        return result

    async def loop(self):
        while True:
            job_id = await self.queue.get()
            job = self.get(job_id)
            if not job or job["status"] != "queued":
                self.queue.task_done()
                continue
            self.active_id = job_id
            self.cancel_event.clear()
            job["status"] = "running"
            started = time.monotonic()
            job["progress"]["started_at"] = now()
            self.save(job)
            execution = asyncio.create_task(self.execute(job))
            cancellation = asyncio.create_task(self.cancel_event.wait())
            try:
                done, _ = await asyncio.wait(
                    [execution, cancellation],
                    timeout=job["request"]["timeout_seconds"],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation in done:
                    await self.stop_process()
                    job.update(
                        status="cancelled", error="Cancelled by caller; previous actions are not undone."
                    )
                elif execution in done:
                    result = execution.result()
                    job.update(
                        status=result["status"], result=result.get("result"), error=result.get("error")
                    )
                    Job.model_validate(job)
                else:
                    await self.stop_process()
                    job.update(
                        status="timed_out",
                        error="Execution deadline reached; previous actions are not undone.",
                    )
            except asyncio.CancelledError:
                await self.stop_process()
                job.update(
                    status="interrupted", error="Service stopped during execution; job was not replayed."
                )
                raise
            except Exception:
                await self.stop_process()
                job.update(
                    status="failed", result=None, error="Browser worker failed; check service configuration."
                )
            finally:
                execution.cancel()
                cancellation.cancel()
                await asyncio.gather(execution, cancellation, return_exceptions=True)
                job["progress"]["execution_ms"] = round((time.monotonic() - started) * 1000)
                self.save(job)
                self.active_id = None
                self.process = None
                self.ephemeral.pop(job_id, None)
                self.queue.task_done()

    async def close(self):
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        self.ephemeral.clear()
        self.db.close()
        self.lock.close()
