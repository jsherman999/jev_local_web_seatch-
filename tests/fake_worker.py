"""Subprocess fixture: deterministic, offline, no Chrome or model calls."""

import json
import sys
import time

req = json.loads(sys.stdin.readline())
if req["goal"] in {"wait", "timeout"}:
    time.sleep(30)
if req["goal"] == "crash":
    sys.exit(1)
print(json.dumps({"event": "progress", "steps": 1}), flush=True)
print(json.dumps({"event": "trace", "seq": 1, "node": "decide", "source": "jev_choose",
                  "phase": "start", "span_id": 1, "at_ms": 1, "cycle": 1, "details": {}}), flush=True)
print(json.dumps({"event": "trace", "seq": 2, "node": "decide", "source": "jev_choose",
                  "phase": "end", "span_id": 1, "at_ms": 3, "cycle": 1,
                  "details": {"duration_ms": 2}}), flush=True)
print(
    json.dumps(
        {
            "event": "result",
            "status": "completed",
            "error": None,
            "result": {
                "agent_status": "done",
                "verification": "passed",
                "checks": [],
                "page": {
                    "url": req["url"],
                    "title": req.get("_llm", {}).get("model", "Example"),
                    "text": "Example Domain",
                    "text_truncated": False,
                    "links": [],
                    "captured_at": "2026-09-18T00:00:00+00:00",
                },
                "visited_urls": [req["url"]],
                "steps": 1,
                "elapsed_ms": 10,
                "actions": [],
            },
        }
    ),
    flush=True,
)
