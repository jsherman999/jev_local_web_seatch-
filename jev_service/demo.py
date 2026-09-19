"""Same-origin comparison UI, backed by Jev jobs and an independent LLM worker."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .batches import BatchRequest
from .config import ROOT, missing_keys
from .models import TERMINAL, JobRequest
from .providers import PROVIDERS, CatalogRequest, ComparisonRequest, catalog, default_llm
from .source_view import SOURCES, source_excerpt

STATIC = ROOT / "jev_service" / "static"


def routes(authenticate):
    router = APIRouter()

    @router.get("/demo", include_in_schema=False)
    async def page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @router.get("/demo/app.js", include_in_schema=False)
    async def script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @router.get("/demo/style.css", include_in_schema=False)
    async def style():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @router.get("/demo/flow.js", include_in_schema=False)
    async def flow_script():
        return FileResponse(STATIC / "flow.js", media_type="text/javascript")

    @router.get("/demo/flow.css", include_in_schema=False)
    async def flow_style():
        return FileResponse(STATIC / "flow.css", media_type="text/css")

    @router.get('/demo/batch.js', include_in_schema=False)
    async def batch_script():
        return FileResponse(STATIC / 'batch.js', media_type='text/javascript')

    @router.post('/v1/batches', status_code=202, dependencies=[Depends(authenticate)], tags=['Demo'])
    async def start_batch(body: BatchRequest, request: Request):
        if missing_keys():
            raise HTTPException(503, 'Configure the Jev and text-helper credentials first')
        request.app.state.batches.require_idle()
        key = request.headers.get('x-llm-api-key', '')
        listing = await catalog(key, body.models[0].provider)
        available = {m['id'] for m in listing['models'] if m['supported']}
        if any(m.model not in available for m in body.models):
            raise HTTPException(400, 'Choose available text chat models from the provider catalog')
        return request.app.state.batches.start(body, key)

    @router.get('/v1/batches/{batch_id}', dependencies=[Depends(authenticate)], tags=['Demo'])
    async def get_batch(batch_id: str, request: Request):
        return request.app.state.batches.get(batch_id)

    @router.post('/v1/batches/{batch_id}/cancel', dependencies=[Depends(authenticate)], tags=['Demo'])
    async def cancel_batch(batch_id: str, request: Request):
        return request.app.state.batches.cancel(batch_id)

    @router.get("/v1/demo/source/{key}", dependencies=[Depends(authenticate)], tags=["Demo"])
    async def source(key: str):
        if key not in SOURCES:
            raise HTTPException(404, "Unknown function")
        return source_excerpt(key)

    @router.get("/demo/playground", include_in_schema=False)
    async def playground():
        return FileResponse(STATIC / "playground.html")

    @router.get("/demo/config", include_in_schema=False)
    async def config():
        text_model = os.environ.get("TEXT_MODEL", "deepseek-chat")
        direct_openai = os.environ.get("TEXT_MODEL_BASE_URL", "").rstrip("/") == "https://api.openai.com/v1"
        return {"text_model": text_model, "jev_model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
                "default_llm": default_llm(),
                "providers": [{"id": p, "label": v[0]} for p, v in PROVIDERS.items()],
                "auth_required": bool(os.environ.get("JEV_API_TOKEN")),
                "rates": {"text_input": 2.5, "text_cached": .25, "text_output": 15}
                if direct_openai and text_model in {"gpt-5.4", "gpt-5.4-2026-03-05"} else {}}

    @router.post('/v1/demo/models', dependencies=[Depends(authenticate)], tags=['Demo'])
    async def models(body: CatalogRequest, request: Request):
        # Read manually: validation errors must never echo a credential header.
        return await catalog(request.headers.get('x-llm-api-key', ''), body.provider)

    def pair(request, job_id):
        jev = request.app.state.jobs.get(job_id)
        baseline_id = jev and jev["progress"].get("comparison_baseline_id")
        baseline = request.app.state.baseline.get(baseline_id) if baseline_id else None
        if not jev or not baseline:
            raise HTTPException(404, "Comparison not found")
        return {"id": job_id, "jev": jev, "llm": baseline}

    @router.post("/v1/comparisons", status_code=202, dependencies=[Depends(authenticate)], tags=["Demo"])
    async def start(body: ComparisonRequest, request: Request):
        request.app.state.batches.require_idle()
        if missing_keys():
            raise HTTPException(503, "Configure the model credentials before running a comparison")
        managers = (request.app.state.jobs, request.app.state.baseline)
        for manager in managers:
            active = manager.db.execute(
                "SELECT count(*) FROM jobs WHERE json_extract(body,'$.status') IN ('queued','running')"
            ).fetchone()[0]
            if active:
                raise HTTPException(409, "Wait for the current jobs to finish before comparing")
        runtime, metadata = None, default_llm()
        if body.baseline_rates:
            metadata['rates'] = body.baseline_rates.model_dump()
        if body.llm:
            key = request.headers.get('x-llm-api-key', '')
            listing = await catalog(key, body.llm.provider)
            if not any(m['id'] == body.llm.model and m['supported'] for m in listing['models']):
                raise HTTPException(400, 'Choose an available text chat model from the provider catalog.')
            runtime = {'key': key, 'provider': body.llm.provider, 'model': body.llm.model}
            metadata = {**body.llm.model_dump(), 'label': listing['label']}
        elif request.headers.get('x-llm-api-key'):
            raise HTTPException(400, 'Choose a provider and model for this API key.')
        # Discovery awaits the network; recheck queues before the atomic submission.
        request.app.state.batches.require_idle()
        if any(m.db.execute("SELECT count(*) FROM jobs WHERE json_extract(body,'$.status') "
                            "IN ('queued','running')").fetchone()[0] for m in managers):
            raise HTTPException(409, 'Wait for the current jobs to finish before comparing')
        body = JobRequest.model_validate(body.model_dump(exclude={'llm', 'baseline_rates'})).model_copy(
            update={"capture_screenshots": True, "isolate_browser": True})
        jev = managers[0].submit(body)
        try:
            baseline = managers[1].submit(body, runtime=runtime, metadata={'model_config': metadata})
        except Exception:
            managers[0].cancel(jev["id"])
            raise
        jev["progress"]["comparison_baseline_id"] = baseline["id"]
        managers[0].save(jev)
        return pair(request, jev["id"])

    @router.get("/v1/comparisons/{job_id}", dependencies=[Depends(authenticate)], tags=["Demo"])
    async def get(job_id: str, request: Request):
        return pair(request, job_id)

    @router.post("/v1/comparisons/{job_id}/cancel", dependencies=[Depends(authenticate)], tags=["Demo"])
    async def cancel(job_id: str, request: Request):
        comparison = pair(request, job_id)
        request.app.state.jobs.cancel(comparison["jev"]["id"])
        request.app.state.baseline.cancel(comparison["llm"]["id"])
        return pair(request, job_id)

    @router.delete("/v1/comparisons/{job_id}", status_code=204,
                   dependencies=[Depends(authenticate)], tags=["Demo"])
    async def delete(job_id: str, request: Request):
        comparison = pair(request, job_id)
        if any(comparison[side]["status"] not in TERMINAL for side in ("jev", "llm")):
            raise HTTPException(409, "Stop both jobs before deleting the comparison")
        for side, manager in (("jev", request.app.state.jobs), ("llm", request.app.state.baseline)):
            manager.db.execute("DELETE FROM jobs WHERE id=?", (comparison[side]["id"],))
            manager.db.commit()

    return router
