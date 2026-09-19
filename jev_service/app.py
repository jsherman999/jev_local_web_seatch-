import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .batches import BatchManager
from .config import DATA, ROOT, missing_keys
from .demo import routes
from .jobs import JobManager
from .models import TERMINAL, Job, JobRequest

security = HTTPBearer(auto_error=False)


def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    token = os.environ.get("JEV_API_TOKEN", "")
    if token and (not credentials or not secrets.compare_digest(credentials.credentials, token)):
        raise HTTPException(
            401, "Supply Authorization: Bearer <JEV_API_TOKEN>", headers={"WWW-Authenticate": "Bearer"}
        )


def create_app(data: Path = DATA, command=None):
    @asynccontextmanager
    async def lifespan(app):
        manager = JobManager(data, command)
        manager.start()
        app.state.jobs = manager
        try:
            baseline = JobManager(data / "baseline", command or
                                  [sys.executable, "-m", "jev_service.worker", "--baseline"])
            app.state.baseline = baseline
            baseline.start()
            app.state.batches = BatchManager(manager, baseline)
            try:
                yield
            finally:
                await app.state.batches.close()
                await baseline.close()
        finally:
            await manager.close()

    app = FastAPI(
        title="Jev Browser Service",
        version="0.1.0",
        lifespan=lifespan,
        description="Local browser automation for other apps. Submit a job, poll its status, "
        "then consume rendered page text and source links. One job executes at a time. "
        "**completed** means Jev chose DONE; check result.verification for independent checks. "
        "Read [/integration.md](/integration.md) for a complete agent integration guide.",
    )
    allowed_hosts = [
        host.strip()
        for host in os.environ.get("JEV_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def reject_foreign_origins(request: Request, call_next):
        # Backend clients omit Origin; allow Swagger on any explicitly configured service host.
        origin = request.headers.get("origin")
        allowed_origins = {f"http://{host}:{os.environ.get('JEV_PORT', '8776')}" for host in allowed_hosts}
        if origin and origin not in allowed_origins:
            return PlainTextResponse("Cross-origin browser access is disabled", status_code=403)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/docs")

    @app.get("/health", tags=["Service"])
    async def health():
        """Liveness and configuration only. Does not spend model credits or start a browser."""
        return {
            "status": "ok",
            "service": "jev-browser-service",
            "version": "0.1.0",
            "models_configured": not missing_keys(),
            "missing_keys": missing_keys(),
            "active_job_id": app.state.jobs.active_id,
        }

    @app.get("/ready", tags=["Service"])
    async def ready(response: Response):
        """Checks the dedicated Chrome endpoint and key presence; does not validate provider billing."""
        browser = False
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                r = await client.get(os.environ["BU_CDP_URL"].rstrip("/") + "/json/version")
                browser = r.is_success and bool(r.json().get("webSocketDebuggerUrl"))
        except (httpx.HTTPError, ValueError):
            pass
        ok = browser and not missing_keys()
        response.status_code = 200 if ok else 503
        return {
            "ready": ok,
            "browser_connected": browser,
            "missing_keys": missing_keys(),
            "provider_credentials_validated": False,
        }

    @app.get("/integration.md", response_class=PlainTextResponse, tags=["Service"])
    async def integration():
        return (ROOT / "docs" / "INTEGRATION.md").read_text()

    @app.post(
        "/v1/jobs", response_model=Job, status_code=202, dependencies=[Depends(authenticate)], tags=["Jobs"]
    )
    async def submit(body: JobRequest, response: Response):
        """Queue one goal. Model calls are billable. Do not retry ambiguous submissions blindly."""
        if app.state.batches.active_id:
            raise HTTPException(409, 'A sequential model comparison is running')
        if missing_keys():
            raise HTTPException(
                503,
                {
                    "message": "Configure model credentials in .env and restart",
                    "missing_keys": missing_keys(),
                },
            )
        try:
            job = app.state.jobs.submit(body)
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from None
        response.headers["Location"] = "/v1/jobs/" + job["id"]
        return job

    @app.get("/v1/jobs", response_model=list[Job], dependencies=[Depends(authenticate)], tags=["Jobs"])
    async def jobs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        """Most recently submitted first. Results remain on disk until explicitly deleted."""
        return app.state.jobs.list(limit, offset)

    @app.get("/v1/jobs/{job_id}", response_model=Job, dependencies=[Depends(authenticate)], tags=["Jobs"])
    async def get_job(job_id: str):
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.post(
        "/v1/jobs/{job_id}/cancel", response_model=Job, dependencies=[Depends(authenticate)], tags=["Jobs"]
    )
    async def cancel(job_id: str):
        """Request cancellation. Poll until cancelled; already-executed browser actions are not undone."""
        job = app.state.jobs.cancel(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.delete("/v1/jobs/{job_id}", status_code=204, dependencies=[Depends(authenticate)], tags=["Jobs"])
    async def delete(job_id: str):
        """Delete a finished job and its captured content. Cancel active jobs first."""
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job["status"] not in TERMINAL:
            raise HTTPException(409, "Cancel the job and wait for a terminal status first")
        app.state.jobs.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        app.state.jobs.db.commit()
        return Response(status_code=204)

    app.include_router(routes(authenticate))
    return app


app = create_app()
