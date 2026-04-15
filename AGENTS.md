# research-auto Agent Guide

## Purpose

This repository builds a conference-paper ingestion and reading pipeline:

- crawl accepted papers from conference sites
- resolve paper artifacts and PDFs
- parse PDFs into text and chunks
- generate structured bilingual summaries
- support search and QA over the paper library
- expose API, CLI, and SSR UI

## Stack

- Python managed with `uv`
- PostgreSQL as the system of record
- FastAPI for API + SSR routes
- Playwright for crawling conference pages
- LiteLLM for model access

## Current Defaults

- Preferred summarization path: `github_copilot_oauth`
- Preferred model: `gpt-5.4-mini`
- GitHub Copilot must use LiteLLM Responses API, not Chat Completions, for this model
- arXiv access uses the `arxiv` Python library with a singleton `arxiv.Client`
- Worker queues are split by job class; prefer dedicated `llm`, `parse`, `download`, `resolve`, or `crawl` workers over one mixed worker

## Important Behaviors

### Resolution status

- `resolved` means the paper has a usable `best_pdf_url`
- `unresolved` means artifact resolution completed but no usable PDF was found
- Do not mark a paper `resolved` if `best_pdf_url` is null

### arXiv fallback

- arXiv fallback is allowed when conference or publisher links do not yield a usable PDF
- Keep fallback reasons explicit in `artifacts.resolution_reason`
- Prefer cached arXiv query results when available
- Reuse the shared arXiv client; do not create a new client per query

### Summaries

- Avoid `mock` summaries for real runs
- `litellm_fallback` rows indicate model failure fallback, not a successful model summary
- Re-summarize fallback or mock rows when model access is working

### UI

- Preserve the current Bootstrap-based SSR UI
- Paper detail page includes a BibTeX citation block
- Citation format is currently BibTeX only, using `url` when DOI is absent

## Common Commands

```bash
uv run research-auto bootstrap-db
uv run research-auto seed-icse
uv run research-auto enqueue-resolve --limit 50
uv run research-auto enqueue-parse --limit 50
uv run research-auto enqueue-summarize --limit 50
uv run research-auto enqueue-resummarize-fallbacks --limit 20
uv run research-auto repair-resolution-status
uv run research-auto drain --queue llm
uv run research-auto worker --queue resolve
uv run research-auto api --host 127.0.0.1 --port 8000
uv run pytest -q
```

## Environment

Typical local database:

```bash
export DATABASE_URL="postgresql://research_auto:research_auto@127.0.0.1:5432/research_auto"
```

Typical model settings:

```bash
export LLM_PROVIDER="github_copilot_oauth"
export LLM_MODEL="gpt-5.4-mini"
```

Useful arXiv tuning:

```bash
export ARXIV_DELAY_SECONDS="3.0"
export ARXIV_PAGE_SIZE="100"
export ARXIV_NUM_RETRIES="5"
```

## Working Notes

- This repo may have a dirty worktree; do not revert unrelated changes
- Prefer minimal changes over large refactors
- Run tests after meaningful code changes
- If GitHub Copilot OAuth behaves unexpectedly, verify LiteLLM token files under `~/.config/litellm/github_copilot/`
