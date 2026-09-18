"""Run: .venv/bin/python examples/client.py (uses paid model APIs). No extra dependency."""

import json
import os
import time
import urllib.request

BASE = os.environ.get("JEV_URL", "http://127.0.0.1:8776")
TERMINAL = {"completed", "failed", "blocked", "timed_out", "cancelled", "interrupted"}


def call(path, body=None):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("JEV_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["JEV_API_TOKEN"]
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main():
    job = call(
        "/v1/jobs",
        {
            "url": "https://example.com",
            "goal": "Read this page and stop when Example Domain is visible.",
            "verify": {"text_contains": ["Example Domain"]},
        },
    )
    print("Job:", job["id"])
    deadline = time.monotonic() + 300
    while job["status"] not in TERMINAL:
        if time.monotonic() >= deadline:
            call("/v1/jobs/" + job["id"] + "/cancel", {})
            raise TimeoutError("Client deadline exceeded; cancellation requested")
        time.sleep(1)
        job = call("/v1/jobs/" + job["id"])
    print(json.dumps(job, indent=2))
    if job["status"] != "completed" or job["result"]["verification"] != "passed":
        raise SystemExit("Browser task did not pass verification")


if __name__ == "__main__":
    main()
