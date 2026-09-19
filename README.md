# Jev Browser Service

Browser automation shared by apps on this Mac and its local network.

**Local Swagger:** http://localhost:8776/docs.

- **Swagger:** http://127.0.0.1:8776/docs
- **OpenAPI:** http://127.0.0.1:8776/openapi.json
- **Agent integration guide:** [docs/INTEGRATION.md](docs/INTEGRATION.md), also served at `/integration.md`
- **Readiness:** http://127.0.0.1:8776/ready
- **Side-by-side demo:** http://127.0.0.1:8776/demo

## Browser comparison demo

Enter an **LLM API key** to choose the regular browser's provider and model. Recognizable
prefixes select OpenAI, Anthropic, Google, Groq, or OpenRouter automatically. Shared
formats (including generic `sk-` keys used by DeepSeek and older OpenAI keys) require a
provider choice; keys are never tried against multiple providers. The provider's live
catalog populates the dropdown, with known non-chat models visible but disabled.
Model listings do not guarantee account access or compatibility with JSON browser actions.
The baseline accepts a single plain or Markdown-fenced JSON action, with the same
observed-target validation. Empty responses, output-limit endings, and refusals fail
with specific messages. Positive integer element IDs are normalized to strings;
extra metadata is ignored and never executed. Invalid field types are reported in
`progress.action_validation`, without recording response values. On failure, no action
is executed or automatically retried. Safe response
diagnostics retain the finish reason and presence of content, not model text or reasoning.
OpenRouter errors include a bounded, credential-redacted provider explanation when
available. Token budgets and account data-policy settings are not changed automatically.
OpenRouter Haiku 4.5 and `~anthropic/claude-haiku-latest` use a strict action JSON
schema and require a supporting provider endpoint. Observed-target and browser
freshness checks still run locally; the alias is passed through unchanged.
Leave the key blank to use the configured service default.

Entered keys are held only in tab/service/worker memory and passed through a private
worker pipe. They are not stored in browser storage, SQLite, traces, or configuration.
The backend is required: calls do not go directly from a GitHub Pages frontend.
The selected model affects only the regular LLM lane; Jev's text helper stays configured
separately. Model ID, provider, and rate estimates are saved with the comparison.

After both runs end, the cost card shows the more expensive lane, a ratio such as
**DeepSeek 15× Jev**, and the dollar difference. Jev's total includes its helper.
Unknown model rates must be entered under **Run settings & pricing** before running;
provider-reported cost takes precedence. Missing usage disables the ratio, and zero-cost
runs show a dollar comparison instead of dividing by zero. Spend is not an outcome ranking.

Open `/demo`, enter a URL and task, then select **Start comparison**. The left pane
uses the configured text LLM as a conventional JSON action agent; the right uses
the Jev service. **Try the practice task** loads a local product-search exercise.
Both run concurrently in fresh Chrome contexts with the same DOM observations,
supported controls, viewport, decision budget, and timeout. The baseline generates
its action and any field text together; Jev uses TypeSafe decisions plus its text helper.
The panes are real, read-only browser screenshots updated after each decision,
not interactive embedded websites or continuous video.

Each pane shows execution time, provider-reported input/output tokens, estimated
total API cost, an action log, and final page text. Timing includes process startup,
browser work, screenshot capture, model calls, and cleanup, but excludes queue time.
The agents run concurrently on shared hardware; this is a demo, not a controlled benchmark.
An optional final-text check helps compare outcomes; it does not verify every semantic
requirement. No overall winner is inferred from tokens alone.
Editing the practice task's URL or goal clears its preset success phrase; manually
entered verification text remains under your control.

Both agents share a dropdown compatibility adapter. It supports native selects
covered by their own visible, aria-hidden decoration (such as Amazon's sort control),
while retaining freshness, visibility, enabled-option, and unrelated-overlay checks.
Dropdown diagnostics record the precise failed check in `progress.browser_diagnostic`.

Expand **Run settings & pricing** to adjust limits and USD-per-million token rates.
Default estimates use [GPT-5.4 pricing](https://developers.openai.com/api/docs/models/gpt-5.4)
($2.50 input, $0.25 cached input, $15 output) only for the matching direct OpenAI
configuration, and [Jev 1.13 pricing](https://docs.typesafe.ai/models)
($0.042 input, free output), checked September 18, 2026. Jev's alias can change;
confirm your account/model rates. Provider-reported cost takes precedence. Jev's
total includes text-helper calls. Missing usage or prices are shown as unavailable,
and in-flight/failed requests can incur charges not yet reported. Rates cover standard
short-context API calls; taxes, custom discounts, and local compute are excluded.

**Stop both** cancels the workers; refreshing restores the last comparison in that
tab. The optional service token is entered in settings and is never saved. Job
data and final screenshots persist locally; `DELETE /v1/comparisons/{id}` deletes
both finished jobs. The demo refuses to start while either queue is busy. The normal
Jev queue still runs one job at a time; a separate baseline worker runs alongside it.
The demo shares the existing service's host/origin/authentication protections.

### Live execution diagrams

The **Under the hood** panels highlight actual worker calls: browser setup, DOM
observation, Jev/LLM API requests, output routing, the optional Jev text helper,
browser execution, final checks, and result delivery. Select a box for a plain-language
explanation and **View actual function** for its source. Only an explicit allowlist of
function definitions is served, with the same optional bearer authentication as jobs;
the source endpoint cannot read arbitrary files or runtime credentials.

After a run, **Replay**, the event buttons, and the slider inspect the recorded trace.
Replay deliberately displays one event every 0.5 seconds and keeps original timestamps;
it does not rerun jobs, spend model credits, or replay historical browser screenshots.
Live polling may miss a brief highlight, but the trace retains the event for inspection.
Old jobs created before tracing show an explicit unavailable message. Stopped jobs do
not leave a model falsely marked as still running. Trace events record function identifiers,
timestamps, operation/target IDs, model IDs, and small counts—not request bodies,
typed field values, credentials, or hidden reasoning. Normal jobs without screenshots
do not emit traces. At most 1,200 events are retained per job, with truncation flagged.

The flow explains this application's implementation and observable API calls. It does
not claim to expose hosted model internals. Jev evaluates typed questions in one request;
the baseline generates action JSON and field text. Browser timings include the additional
instrumentation and should not be treated as a general provider benchmark.

Submit a URL and goal to `POST /v1/jobs`, poll by ID, then pass the returned page text and
source links to your application's analysis LLM. Supports persistent jobs, cancellation,
timeouts, and optional deterministic final-page checks.

## Example: a local property-comparison app

A user asks: **“Compare these five houses, focusing on price, property taxes, and
recent price reductions.”** Your app can use Jev to gather information that requires
interacting with a listing website, then use its own analysis LLM to compare the findings.

1. **Send a browser job for each listing.** For example, submit this body to `POST /v1/jobs`:

   ```json
   {
     "url": "https://listing-site.example/property/123",
     "goal": "Expand the price history and property tax sections. Stop when both sections are visible."
   }
   ```

   This URL is illustrative; replace it with a real listing. Jev queues the jobs and
   executes one at a time.
2. **Poll for completion.** Use the returned job ID with `GET /v1/jobs/{id}`. Collect
   `result.page.text`, `result.page.url`, and `result.page.links`. Check the terminal
   status, any errors, and whether the information you need was actually captured.
3. **Analyze the findings in your app.** Pass the captured text to your app's analysis
   LLM to extract prices, taxes, and price-history entries into a comparison table.
4. **Show the comparison with sources.** Link findings to their listing pages, include
   the capture time, and flag missing information rather than inventing values.

**Jev's value is interacting with the website:** clicking expandable sections or
navigating filters to reveal the information your app needs. Your app's LLM interprets
that information. For sites with a suitable API, use it directly; Jev is useful when
getting the data requires supported browser interactions and the site permits automation.

TypeSafe's Jev model chooses browser actions. The configured text model—OpenAI in this
installation—only generates text to enter in fields, such as search queries. It does
not analyze the listings or write the comparison report. That remains your app's job.

This example does not supply verification checks, so `completed` means Jev chose DONE,
not that every requested fact was independently verified. Add `verify.text_contains`
or `verify.url_contains` when you know suitable final-page checks; see the
[integration guide](docs/INTEGRATION.md) for their exact semantics and browser limitations.

## Install

Requires macOS, Google Chrome, uv, Python 3.12+, TypeSafe and text-model credentials.

```sh
git clone https://github.com/jsherman999/jev_local_web_seatch-.git
cd jev_local_web_seatch-
uv sync --locked
cp .env.example .env  # only on a new install; preserve existing credentials
chmod 600 .env
# Fill in model credentials in .env.
./launchd/control.sh install
```

The installer writes two plists to `~/Library/LaunchAgents`, then starts a dedicated
headless Chrome instance and the API. This installation listens on `0.0.0.0:8776` for LAN access, controlled by `JEV_HOST` and `JEV_ALLOWED_HOSTS` in `.env`. New installs default to loopback.
Chrome uses `127.0.0.1:9276` and `data/chrome`, independent of personal browser sessions.

```sh
./launchd/control.sh status
./launchd/control.sh restart
./launchd/control.sh logs
./launchd/control.sh stop
./launchd/control.sh start
./launchd/control.sh uninstall  # retains data and source
```

Development: `.venv/bin/python -m jev_service` (stop the installed API first).
Tests: `.venv/bin/python -m pytest`; lint: `.venv/bin/ruff check jev_service tests launchd examples`.
Tests use fake workers and never call paid APIs. Live example clients do call paid APIs.

## Dependencies and credentials

Upstream source is preserved at `vendor/jev-ultrafast`, commit
`1231850a0bf1a0c0341fe408ef1668dbbfdfac46`, under its MIT license. `uv.lock` pins dependencies.
The wrapper leaves upstream code unchanged. A small transport adapter translates the text
helper's request options for OpenAI's Chat Completions endpoint when using `api.openai.com`.

Credentials live in `.env` (gitignored, mode 600), never in launchd plists or docs.
`TEXT_MODEL_BASE_URL`, `TEXT_MODEL`, and `TEXT_MODEL_API_KEY` choose the text helper.
`JEV_API_TOKEN` optionally requires a bearer token for job endpoints. Read the integration
guide for lifecycle semantics, limits, and deployment assumptions.

Sources: [Jev upstream](https://github.com/browser-use/jev-ultrafast),
[OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

### Compare up to ten models

Load a provider's models using your API key, open **Compare models**, and select
1–10 distinct models from that provider. The searchable picker includes optional
input, cached-input, and output rates for each selected model. Enter the URL, goal,
and optional final-text check once, then choose **Run selected models + Jev**.

Jev runs once, followed by each selected model once in selection order. All runs
use fresh headless browser contexts and identical limits. Other service jobs are
rejected while a batch runs to avoid overlapping browser work. Batch mode omits
screenshots and live function traces for every participant; the original two-pane
comparison remains available separately.

Progress, cancellation, cost/time bar charts, and a results table appear below the
form. Jev is highlighted; failed attempts are striped and never presented as successful
wins. Unknown costs remain unavailable. Jev cost includes its text helper, and rate
estimates are captured at submission. A single run is illustrative, not a statistically
reliable benchmark. Site changes, provider load, and cache effects can affect results.

Reloading the tab restores the last batch. Completed results persist locally. A
cancel stops the active run and skips remaining models. A service restart interrupts
unfinished work without replaying it; entered keys are never persisted.
