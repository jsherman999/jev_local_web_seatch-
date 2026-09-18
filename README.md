# Jev Browser Service

Browser automation shared by apps on this Mac and its local network.

**LAN Swagger:** http://192.168.4.27:8776/docs (also http://jmini.local:8776/docs).

- **Swagger:** http://127.0.0.1:8776/docs
- **OpenAPI:** http://127.0.0.1:8776/openapi.json
- **Agent integration guide:** [docs/INTEGRATION.md](docs/INTEGRATION.md), also served at `/integration.md`
- **Readiness:** http://127.0.0.1:8776/ready

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
