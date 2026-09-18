"""One subprocess per job. stdout is a small NDJSON event stream for the parent."""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import JobRequest


def emit(event):
    print(json.dumps(event), flush=True)


def adapt_openai():
    """Upstream sends OpenRouter reasoning options; translate only for api.openai.com."""
    from jev_ultrafast import model

    original = model.post_json

    def post(url, key, body):
        if urlparse(url).hostname == "api.openai.com":
            body = {k: v for k, v in body.items() if k not in {"reasoning", "thinking", "max_tokens"}}
            body["max_completion_tokens"] = 2048
        return original(url, key, body)

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


def run(request):
    from jev_ultrafast import Agent

    adapt_openai()
    started = time.monotonic()
    with Agent(str(request.url), request.goal) as agent:
        state = agent.snapshot()
        status = "blocked"
        for index in range(request.max_steps):
            state = agent.command("tick")
            emit(
                {
                    "event": "progress",
                    "steps": len(state["history"]),
                    "decisions": index + 1,
                    "url": state["page"]["url"],
                    "agent_status": state["status"],
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if state["status"] in {"done", "blocked"}:
                status = "completed" if state["status"] == "done" else "blocked"
                break
        page = extract_page(agent.browser, request.max_text_chars)
        verified, checks = verify_page(page, request.verify)
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
        }
        error = None
        if status == "blocked":
            error = "Agent blocked or decision limit reached; inspect the partial result."
        elif status == "failed":
            error = "Final page did not pass the requested verification checks."
        emit({"event": "result", "status": status, "result": result, "error": error})


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        request = JobRequest.model_validate_json(sys.stdin.readline())
        run(request)
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
