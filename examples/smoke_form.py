"""Opt-in live integration check: local form + real TypeSafe/text model calls."""

import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from client import TERMINAL, call


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        body = "<html><head><title>Jev integration check</title></head><body>"
        if query:
            body += "<h1>Search results</h1><p>Search received: " + html.escape(query) + "</p>"
            body += '<a href="https://example.com">Example source</a>'
        else:
            body += (
                '<h1>Research search</h1><form method="get">'
                '<label for="q">Search query</label><input id="q" name="q" type="text">'
                '<button type="submit">Search</button></form>'
            )
        content = (body + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        job = call(
            "/v1/jobs",
            {
                "url": f"http://127.0.0.1:{server.server_port}",
                "goal": 'Search for exactly "Jev browser service" using the search form. '
                "Stop when the Search results page shows Search received: Jev browser service.",
                "max_steps": 10,
                "timeout_seconds": 120,
                "verify": {"text_contains": ["Search results", "Search received: Jev browser service"]},
            },
        )
        print("Job:", job["id"], flush=True)
        deadline = time.monotonic() + 150
        while job["status"] not in TERMINAL:
            if time.monotonic() > deadline:
                call("/v1/jobs/" + job["id"] + "/cancel", {})
                raise TimeoutError("Smoke test deadline exceeded")
            time.sleep(1)
            job = call("/v1/jobs/" + job["id"])
        print(json.dumps(job, indent=2))
        assert job["status"] == "completed", job["error"]
        assert job["result"]["verification"] == "passed"
        assert any(action["operation"] == "TYPE_TEXT" for action in job["result"]["actions"])
        assert any(action["operation"] == "CLICK" for action in job["result"]["actions"])
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
