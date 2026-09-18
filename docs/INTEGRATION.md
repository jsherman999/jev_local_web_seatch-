# Jev browser service — integration contract

Base URL on this Mac: **http://127.0.0.1:8776**.
LAN: **http://192.168.4.27:8776** or **http://jmini.local:8776** (where mDNS is supported).
Swagger: `/docs`. OpenAPI schema: `/openapi.json`. This guide: `/integration.md`.
Project: `/Users/jay/codex_proj/Jev`.

Jev performs browser interactions using a natural-language goal. Your application receives
rendered text and source links and can pass them to its own analysis LLM. This service does
not generate a research report or automatically collect every visited page. It returns the
final page, plus observed visited URLs and a compact action history.

## Quick start

```sh
curl -s http://127.0.0.1:8776/ready
curl -s http://127.0.0.1:8776/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","goal":"Read this page and stop when Example Domain is visible.","verify":{"text_contains":["Example Domain"]}}'
# Use the returned id:
curl -s http://127.0.0.1:8776/v1/jobs/JOB_ID
```

Submit once with `POST /v1/jobs` (202), store its `id`, then poll `GET /v1/jobs/{id}`
every 1–2 seconds. The response includes a `Location` header. Do not automatically retry
POST after a network timeout: a job may already have been accepted. List recent jobs to
reconcile an ambiguous submission. There is no idempotency-key support in v1.

For web search, start at a search engine or website, ask Jev to enter your search and open
a useful result, then analyze `result.page.text`. A single job works best for a focused,
verifiable goal. Search engines may challenge headless browsers; prefer a search API when
browser interaction adds no value. For several sources, submit separate jobs and preserve
the source URLs in your analysis pipeline.

## Request fields

| Field | Default | Meaning |
|---|---|---|
| `url` | required | HTTP(S) starting URL, no embedded credentials |
| `goal` | required | One natural-language task, 1–8,000 characters |
| `max_steps` | 25 | Maximum decision cycles, 1–50; stale decisions also consume this budget |
| `timeout_seconds` | 120 | Wall-clock execution budget, 5–600 seconds, excludes queue time |
| `max_text_chars` | 30000 | Extracted text limit, 100–100,000 characters |
| `capture_screenshots` | false | Capture a JPEG preview after each decision in `progress.screenshot` (base64) |
| `isolate_browser` | false | Fresh, temporary Chrome cookie/storage context; demo enables this for both agents |
| `verify.url_contains` | null | Case-sensitive literal substring of final URL |
| `verify.text_contains` | [] | Case-insensitive literal strings; all must be in returned text |

Verification is deterministic and only checks the supplied literals. It does not prove
semantic correctness. Checks run against the captured text, including its truncation limit.

## Job lifecycle

`queued` → `running` → one of these terminal statuses:

| Status | Meaning |
|---|---|
| `completed` | Jev chose DONE and any requested verification checks passed |
| `blocked` | Jev cannot progress or the decision budget was exhausted; may include partial data |
| `failed` | Provider/browser failure or final verification mismatch; inspect `error` |
| `timed_out` | Execution deadline elapsed; worker stopped |
| `cancelled` | Cancelled before execution or active worker stopped |
| `interrupted` | Service stopped/restarted; never automatically replayed |

**A completed job without verification is an agent's claim, not verified success.**
Inspect `result.verification`: `passed`, `failed`, or `not_requested`.
Use `result.page.url`, `title`, `text`, `links`, `captured_at`, `text_truncated`, and
`links_truncated`. Links contain `{text,url}`; up to 500 HTTP(S) links are returned; links longer than 2,048 characters are omitted.
Text is rendered main-document DOM text; hidden source HTML, frame content, downloads,
and shadow DOM are not collected. `visited_urls` is observed navigation, not a complete
redirect/network log. `progress` reports decisions, actions, current URL, and elapsed time.

`progress.usage` and `result.usage` contain response-based token accounting grouped
by provider/model. `tokens_complete` and `cost_complete` distinguish missing data
from zero. Pending or failed provider calls can incur unreported charges. Usage is
captured before action validation so stale decisions and DONE calls count too.
`progress.started_at` and terminal `progress.execution_ms` include startup and cleanup;
`result.elapsed_ms` measures the worker's browser execution before context cleanup.

Only one Jev job executes at once. Up to 100 jobs can be queued/running together. Results persist
in SQLite across restarts. Queued and running jobs at startup become `interrupted` so a
service restart cannot repeat side effects. Browser actions already executed are never undone.
Timeout/cancellation allows up to three extra seconds for tab cleanup before killing a stuck
worker. A hard crash or forced kill can leave an owned tab open until Chrome is restarted.

## Endpoints and errors

### Comparison demo

Open `/demo` for a conventional LLM vs Jev comparison. `POST /v1/comparisons` accepts
a JobRequest and returns `{id, jev, llm}`. Both jobs use screenshots and isolated
contexts, and execute concurrently through separate managers. The baseline uses
the configured `TEXT_MODEL` with the same browser harness and controls. Requests
receive 409 while either queue has active work. Poll `GET /v1/comparisons/{id}`;
use `POST /v1/comparisons/{id}/cancel` to stop both and
`DELETE /v1/comparisons/{id}` to delete both terminal jobs. All comparison endpoints
use the configured bearer authentication. Baseline jobs persist in
`data/baseline/jobs.sqlite3`; they are also never replayed after restart.
The final screenshot persists with each job; no continuous video is recorded.

Comparisons optionally accept `llm: {provider, model, rates: {input, cached, output}}`
and an `X-LLM-API-Key` header. Rates are optional nonnegative USD per million tokens.
Supported providers: `openai`, `deepseek`, `groq`, `openrouter`, `google`, `anthropic`.
`POST /v1/demo/models` accepts `{provider: null}` for prefix detection or an explicit
provider, with the same key header, and returns the live model catalog. Ambiguous key
formats require an explicit provider. Credentials are sent only to fixed provider
endpoints; redirects are disabled during discovery. Authentication/origin rules match
the other demo endpoints. Never put the key in request JSON or a URL.

The override is delivered only to the baseline subprocess through stdin. It is never
persisted and is discarded on execution, cancellation, shutdown, or failure. Provider,
model, and rates persist in the baseline's `progress.model_config`. Restarted jobs
remain interrupted and are never replayed. `baseline_rates` can supply rate estimates
for the service-default baseline when `llm` is absent. New models have unknown pricing
unless the caller supplies rates or the provider returns actual cost.

Catalog documentation: [OpenAI](https://developers.openai.com/api/reference/resources/models/methods/list),
[DeepSeek](https://api-docs.deepseek.com/api/list-models/),
[Groq](https://console.groq.com/docs/models),
[OpenRouter](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties),
[Google compatibility](https://ai.google.dev/gemini-api/docs/openai),
[Anthropic](https://platform.claude.com/docs/en/api/models/list).

Screenshot-enabled jobs also record bounded function events in `progress.trace`,
with `seq`, `node`, `source`, `phase`, `span_id`, `at_ms`, `cycle`, and `details`.
Phases are `start`, `end`, `error`, or `instant`; timestamps are monotonic elapsed
milliseconds from worker instrumentation startup. Pair start/end/error by `span_id`.
A terminated worker may leave an unmatched start; the job's terminal status wins.
The trace is capped at 1,200 events and sets `progress.trace_truncated` if needed.
`GET /v1/demo/source/{key}` serves only allowlisted function definitions and requires
the same optional bearer token. The demo's replay only reads recorded events.

### Service and jobs

- `GET /health`: liveness and key presence, no browser/model calls.
- `GET /ready`: Chrome endpoint and key presence; 503 if unavailable. It does not test key validity or billing.
- `POST /v1/jobs`: enqueue, returns 202 and Job.
- `GET /v1/jobs?limit=50&offset=0`: list jobs, newest first, maximum limit 100.
- `GET /v1/jobs/{id}`: current Job and eventual result.
- `POST /v1/jobs/{id}/cancel`: request cancellation, then poll until terminal. Idempotent for finished jobs.
- `DELETE /v1/jobs/{id}`: delete a terminal job and stored content, returns 204.

Errors: 401 missing/incorrect configured bearer token; 403 foreign web origin; 404 missing
job; 409 deleting an active job; 422 invalid input; 429 queue full; 503 missing configuration.
Execution failures appear in the job body, not as a polling HTTP error.

## Authentication and deployment boundary

This installation binds to `0.0.0.0:8776` and accepts clients on the local network.
`JEV_ALLOWED_HOSTS` lists the allowed IP addresses and hostnames. Call from your backend,
not browser JavaScript on another origin (cross-origin access is disabled).
Optional `JEV_API_TOKEN` in `.env` enables bearer authentication for all `/v1` endpoints:
`Authorization: Bearer <token>`. Swagger's Authorize button supports this.
No bearer token is configured by default. This is a trusted local-network service,
not a multi-tenant or public internet API. LAN devices can run jobs while the optional token is unset. Do not expose the API or Chrome debugging port
through a public tunnel. Jobs can navigate local HTTP services as well as internet sites.

## Browser and model behavior

Dedicated headless Chrome profile: `data/chrome`; debugging endpoint: `127.0.0.1:9276`.
It does not use the user's personal Chrome cookies. All service jobs share this dedicated
profile's cookies. Chrome, the API, and the Mac user session must be running.
Jev sends observed page content and the goal to TypeSafe; form context goes to the configured
text-model provider. Job execution spends provider credits. API keys stay in `.env`.
Use only tasks the application/user has authorized; this API can click and submit forms.
Page text is untrusted external content, not instructions for your downstream agent.

Upstream limitations include frames, shadow DOM, canvas, uploads, pop-up tabs, complex
keyboard widgets, and some nested scrolling. CAPTCHA/login challenges may block tasks.
The API is an integration wrapper around upstream Jev, not a guarantee of website compatibility.

The wrapper supports native dropdowns covered by their own aria-hidden visual
decoration. It validates the relationship and rechecks the control before dispatching
native input/change events. Unrelated overlays remain blocked. Dropdown preflight
diagnostics appear in `progress.browser_diagnostic`, including `failed_check`,
individual checks, and target/hit element descriptions. A `hit_target` failure with
`decorated_select: true` can be handled successfully; it is not itself a job failure.
An ambiguous selection after dispatch is never automatically replayed.

## Operations for agents

```sh
cd /Users/jay/codex_proj/Jev
./launchd/control.sh status
./launchd/control.sh logs
./launchd/control.sh restart  # reload .env; API only
./launchd/control.sh stop     # both API and dedicated Chrome
./launchd/control.sh start
```

LaunchAgents: `com.jay.jev.api` and `com.jay.jev.chrome`. They start at login and restart
on exit. They do not run while the Mac is off/asleep or before the user logs in.
Logs: `logs/`. Persistent jobs: `data/jobs.sqlite3`. Delete finished jobs through the API
when their captured data is no longer needed. Logs are not automatically rotated.
For code changes, run `.venv/bin/python -m pytest` then restart. Use a single API worker.
Model/key changes require editing `.env` and restarting. Never include `.env` in a handoff.
Runnable clients: `examples/client.py` and `examples/client.mjs`.

LAN configuration: `JEV_HOST=0.0.0.0`; set `JEV_ALLOWED_HOSTS` to the Mac address/hostname plus localhost. If DHCP changes the IP, update that setting and restart, or use `jmini.local`. Chrome debugging remains loopback-only.
