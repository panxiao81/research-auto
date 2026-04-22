# research-auto

Laptop-friendly prototype for a conference paper ingestion pipeline with:

- PostgreSQL as the system of record
- Playwright for conference crawling
- a PostgreSQL-backed job queue
- a small FastAPI service for inspection

## Stack

- Python managed with `uv`
- FastAPI
- Psycopg 3
- Playwright
- PostgreSQL

## Quick start

1. Create a PostgreSQL database.
2. Set `DATABASE_URL`.
3. Sync dependencies:

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv sync
```

4. Install Playwright browsers:

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv run playwright install chromium
```

5. Bootstrap the schema and seed ICSE 2026:

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto setup migrate
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto setup seed icse
```

6. Trigger the pipeline from the CLI:

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto setup seed icse
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto pipeline drain
```

7. Run the worker or API:

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto serve worker --once
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto serve api --host 127.0.0.1 --port 8000
```

## Docker

Build and run the full stack with PostgreSQL, bootstrap, API, and worker:

```bash
docker compose up --build
```

Services:

- `postgres`: PostgreSQL database
- `bootstrap`: creates schema, seeds ICSE, and drains the initial crawl job
- `api`: serves the HTTP API on `http://localhost:8000`
- `worker`: polls the PostgreSQL job queue continuously

Useful commands:

```bash
docker compose up --build api postgres
docker compose run --rm bootstrap
docker compose run --rm worker uv run research-auto pipeline drain
```

## Commands

- `setup migrate`: applies pending database migrations
- `setup bootstrap-db`: compatibility alias for `setup migrate`
- `setup seed icse`: inserts ICSE 2026 and its Research Track, then enqueues a crawl job
- `pipeline drain`: runs jobs until the queue is empty
- `pipeline drain --queue llm|parse|download|resolve|crawl|all`: drains only one worker queue
- `pipeline resolve`: enqueue detail-page artifact resolution jobs
- `pipeline parse`: enqueue PDF-to-text parse jobs for downloaded artifacts
- `pipeline summarize`: enqueue LLM summary jobs for parsed papers
- `serve worker --queue llm|parse|download|resolve|crawl|all`: claims and executes jobs from one PostgreSQL worker queue
- `serve api`: runs the FastAPI service

## Triggering the Pipeline

Use the grouped CLI when you want a direct trigger rather than calling API endpoints.

```bash
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto setup migrate
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto setup seed icse
/Users/xiao-pan/Library/Python/3.9/bin/uv run research-auto pipeline drain
```

Options:

- `setup migrate`
- `setup bootstrap-db`
- `setup seed icse`
- `pipeline resolve [--limit N]`
- `pipeline parse [--limit N]`
- `pipeline summarize [--limit N]`
- `pipeline resummarize-fallbacks [--limit N]`
- `pipeline repair-resolution-status`
- `pipeline drain [--queue NAME]`
- `serve worker [--once]`

## Current prototype scope

- conference and track registration
- PostgreSQL-backed jobs with retries and backoff
- Researchr / ICSE track crawling with Playwright
- discovered papers and authors persisted in PostgreSQL
- detail-page artifact discovery for preprints, DOI/publication links, and Researchr file attachments
- direct PDF download for resolvable paper links
- PDF-to-text parsing and chunk storage
- LLM summarization with `mock`, LiteLLM-backed providers (`openai_compatible`, `codex_oauth`, `github_copilot_oauth`, `litellm`), `codex_cli`, or `github_models_cli`
- simple API for conferences, tracks, papers, and jobs

## Environment

- `DATABASE_URL`: PostgreSQL connection string
- `PLAYWRIGHT_HEADLESS`: `true` or `false`, defaults to `true`
- `WORKER_POLL_SECONDS`: defaults to `5`
- `WORKER_QUEUE`: defaults to `all`; supported values are `all`, `crawl`, `resolve`, `download`, `parse`, and `llm`
- `ARTIFACT_ROOT`: directory for downloaded artifacts
- `LLM_PROVIDER`: `mock`, `litellm`, `openai_compatible`, `codex_oauth`, `github_copilot_oauth`, `codex_cli`, or `github_models_cli`
- `LLM_MODEL`: model identifier, such as `gpt-5-mini` or `openai/gpt-4o-mini`
- `LLM_API_KEY`: used by `openai_compatible`
- `LLM_BASE_URL`: used by `openai_compatible`
- `LITELLM_BACKEND`: optional explicit backend prefix like `chatgpt`, `github_copilot`, or `openai`
- `CODEX_ACCESS_TOKEN`: bearer token for Codex OAuth access
- `CODEX_ACCOUNT_ID`: optional ChatGPT account/org header for Codex
- `GITHUB_COPILOT_TOKEN`: bearer token from GitHub Copilot OAuth/device flow
- `GITHUB_COPILOT_BASE_URL`: defaults to `https://api.githubcopilot.com`
- `ARXIV_CONTACT`: contact identifier for arXiv usage (default: `mailto:pan-xiao@live.cn`)
- `ARXIV_CACHE_TTL_HOURS`: arXiv query cache TTL in hours (default: `168`)
- `ARXIV_DELAY_SECONDS`: delay between arXiv API requests (default: `3.0`)
- `ARXIV_PAGE_SIZE`: page size for arXiv API requests (default: `100`)
- `ARXIV_NUM_RETRIES`: retry count for arXiv API requests (default: `5`)

## Notes

- The worker currently implements `crawl_track` end to end.
- The worker also resolves detail-page artifacts, downloads PDFs, parses them to text, and can summarize them.
- `codex_oauth` now uses LiteLLM's `chatgpt/...` backend and still auto-loads Codex CLI auth from `~/.codex/auth.json`.
- `codex_cli` uses the installed `codex exec` command directly and is currently the most reliable way to reuse an existing Codex CLI login in this environment.
- `github_copilot_oauth` now uses LiteLLM's `github_copilot/...` backend.
- `github_models_cli` uses `gh models run ...` and therefore depends on a working `gh auth` session plus the `gh-models` extension.
