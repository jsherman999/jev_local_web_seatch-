"""One subprocess per job. stdout is a small NDJSON event stream for the parent."""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import JobRequest
from .telemetry import UsageMeter
from .trace import Trace


def emit(event):
    print(json.dumps(event), flush=True)


def adapt_openai(meter=None, trace=None, baseline=False):
    """Upstream sends OpenRouter reasoning options; translate only for api.openai.com."""
    from jev_ultrafast import model

    original = model.post_json

    def dispatch(url, key, body):
        if baseline and urlparse(url).hostname == 'api.anthropic.com':
            from .providers import anthropic_completion
            return anthropic_completion(key, body)
        return original(url, key, body)

    def post(url, key, body):
        if urlparse(url).hostname == "api.openai.com":
            body = {k: v for k, v in body.items() if k not in {"reasoning", "thinking", "max_tokens"}}
            body["max_completion_tokens"] = 2048
        call = None
        if meter is not None:
            call = meter.begin("typesafe" if urlparse(url).hostname == "api.typesafe.ai" else "text",
                               body.get("model", "unknown"))
            emit({"event": "progress", "usage": meter.summary()})
        if trace is None:
            result = dispatch(url, key, body)
        else:
            typesafe = urlparse(url).hostname == "api.typesafe.ai"
            node = "decide" if typesafe or baseline else "helper"
            source = "jev_choose" if typesafe else "baseline_choose" if baseline else "field_text"
            with trace.span(node, source, model=body.get("model"),
                            api="POST /v1/systemone" if typesafe else
                            "POST /messages" if urlparse(url).hostname == 'api.anthropic.com' else
                            "POST /chat/completions",
                            questions=list(body.get("questions", {}))) as outcome:
                result = dispatch(url, key, body)
                outcome["model"] = result.get("model", body.get("model"))
        if call is not None:
            meter.finish(call, result)
            emit({"event": "progress", "usage": meter.summary()})
        return result

    model.post_json = post


def verify_page(page, spec):
    checks = []
    if spec.url_contains:
        checks.append(
            {
                "kind": "url_contains",
                "expected": spec.url_contains,
                "passed": spec.url_contains in page["url"],
            }
        )
    for text in spec.text_contains:
        checks.append(
            {"kind": "text_contains", "expected": text, "passed": text.casefold() in page["text"].casefold()}
        )
    status = "not_requested" if not checks else ("passed" if all(x["passed"] for x in checks) else "failed")
    return status, checks


def extract_page(browser, limit):
    # Fixed application code, never model-supplied JavaScript. Read rendered DOM text, not HTML.
    page = browser.evaluate("""(() => {
      const text = document.body?.innerText || '';
      const links = [...document.querySelectorAll('a[href]')]
        .filter(a => /^https?:$/.test(new URL(a.href).protocol) && a.href.length <= 2048)
        .map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0,500), url:a.href}));
      return {url:location.href,title:document.title,text:text.slice(0,100000),
        text_truncated:text.length>100000,links:links.slice(0,500),links_truncated:links.length>500};
    })()""")
    if not page:
        raise RuntimeError("Could not extract final page")
    page["text_truncated"] = page["text_truncated"] or len(page["text"]) > limit
    page["text"] = page["text"][:limit]
    page["captured_at"] = datetime.now(timezone.utc).isoformat()
    return page


def run(request, baseline=False):
    from jev_ultrafast import Agent

    meter = UsageMeter()
    trace = Trace(emit, enabled=request.capture_screenshots)
    adapt_openai(meter, trace, baseline)
    if baseline:
        from .baseline import install
        install()
    if request.isolate_browser:
        from .browser_context import install
        install()
    from .browser_adapter import install as install_browser_adapter
    install_browser_adapter(emit)
    trace.install_browser_hooks(baseline, request.isolate_browser)
    started = time.monotonic()
    with Agent(str(request.url), request.goal, screenshots=request.capture_screenshots) as agent:
        state = agent.snapshot()
        def preview():
            return {"screenshot": state["page"].get("screenshot"),
                    "title": state["page"]["title"], "url": state["page"]["url"],
                    "actions": [{k: h.get(k) for k in ("step", "operation", "action", "url")}
                                for h in state["history"]],
                    "usage": meter.summary()}

        emit({"event": "progress", **preview()})
        status = "blocked"
        for index in range(request.max_steps):
            trace.cycle = index + 1
            state = agent.command("tick")
            emit(
                {
                    "event": "progress",
                    "steps": len(state["history"]),
                    "decisions": index + 1,
                    "url": state["page"]["url"],
                    "agent_status": state["status"],
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    **preview(),
                }
            )
            if state["status"] in {"done", "blocked"}:
                status = "completed" if state["status"] == "done" else "blocked"
                break
        with trace.span("verify", "verify_page") as outcome:
            page = extract_page(agent.browser, request.max_text_chars)
            verified, checks = verify_page(page, request.verify)
            outcome["verification"] = verified
        if status == "completed" and verified == "failed":
            status = "failed"
        actions = [
            {k: h.get(k) for k in ("step", "operation", "action", "kind", "url", "latency_ms")}
            for h in state["history"]
        ]
        result = {
            "agent_status": state["status"],
            "verification": verified,
            "checks": checks,
            "page": page,
            "visited_urls": list(
                dict.fromkeys([str(request.url)] + [h["url"] for h in state["history"]] + [page["url"]])
            ),
            "steps": len(state["history"]),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "actions": actions,
            "usage": meter.summary(),
        }
        error = None
        if status == "blocked":
            error = "Agent blocked or decision limit reached; inspect the partial result."
        elif status == "failed":
            error = "Final page did not pass the requested verification checks."
        trace.record("return", "worker_run", "instant", status=status,
                     text_chars=len(page["text"]), source_links=len(page["links"]))
        emit({"event": "result", "status": status, "result": result, "error": error})


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        payload = json.loads(sys.stdin.readline())
        runtime = payload.pop('_llm', None)
        if runtime:
            if '--baseline' not in sys.argv:
                raise ValueError('LLM override is only supported for the baseline')
            from .providers import PROVIDERS
            os.environ['TEXT_MODEL_API_KEY'] = runtime['key']
            os.environ['TEXT_MODEL'] = runtime['model']
            os.environ['TEXT_MODEL_BASE_URL'] = PROVIDERS[runtime['provider']][1]
        request = JobRequest.model_validate(payload)
        run(request, baseline="--baseline" in sys.argv)
    except Exception as exc:
        # Redact configured secrets before returning any provider/browser error.
        message = str(exc)[:1000]
        for name, value in os.environ.items():
            if any(x in name for x in ("KEY", "TOKEN", "SECRET")) and value:
                message = message.replace(value, "[redacted]")
        emit(
            {
                "event": "result",
                "status": "failed",
                "result": None,
                "error": f"{type(exc).__name__}: {message}",
            }
        )


if __name__ == "__main__":
    main()
