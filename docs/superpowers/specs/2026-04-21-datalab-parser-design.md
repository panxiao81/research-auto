# Datalab Parser Backend Design

## Goal
Make Datalab the primary PDF parsing backend while preserving the existing pipeline shape. Backend swaps should stay inside the parsing adapter boundary, with `pypdf` retained as a fallback and backup backend.

## Scope
- Add a Datalab-backed parser implementation for stored PDFs.
- Keep `ParseGateway.parse(storage_uri=...) -> ParsedPaper` as the stable parsing seam.
- Preserve the existing worker-driven `resolve -> download -> parse -> summarize/search` job flow.
- Persist the parser's source text in a backend-agnostic way so it can be either Markdown or plain text.
- Keep parser identity in the existing version-style naming convention.
- Keep `pypdf` available as a fallback when Datalab fails or is disabled.

Out of scope:
- Changing the resolution job.
- Reworking the summarize or RAG layers to become Markdown-aware in this change.
- Refactoring the worker composition root beyond what is needed to wire the new parser backend.

## Current State
The current parser path lives in `src/research_auto/infrastructure/parsing/` and uses `pypdf` directly:

- Parsing already runs as a worker job via `parse_artifact`.

- `PdfParserAdapter` reads the stored artifact from the storage adapter.
- `parse_pdf_file()` extracts plain text with `PdfReader`.
- The parser returns `ParsedPaper(full_text, abstract_text, page_count, content_hash, chunks)`.
- `PostgresPipelineRepository.replace_parse()` stores the parse row in `paper_parses` and the derived chunks in `paper_chunks`.
- Summarization, search, and QA read from `paper_parses` and `paper_chunks` without caring how the parse was produced.

This is the right boundary. The backend swap should stay here.

## Design Principles
- The parsing contract stays backend-agnostic.
- Parsing remains a worker-executed job, and `JobExecutor` should not branch on parser backend.
- Parsed source content may be Markdown or plain text.
- Parser identity should stay in the existing version-style naming convention.
- Any temporary worker wiring change is acceptable for this task, but the backend-specific logic must stay in parsing infrastructure.

## Proposed Shape

### Parse Model
Extend `ParsedPaper` so it can carry both the parser's original source text and normalized downstream text:

- `source_text`: the parser output to preserve verbatim
  - Datalab: Markdown
  - `pypdf`: extracted plain text
- `full_text`: normalized text used by the rest of the current pipeline
- `abstract_text`, `page_count`, `content_hash`, `chunks`: unchanged responsibilities

`source_text` is intentionally backend-agnostic. The system stores what the parser produced without naming the field after one provider.

### Parser Adapter
Replace the current single-backend adapter with a small backend-selecting adapter in `src/research_auto/infrastructure/parsing/adapters.py`:

- Read the stored PDF bytes once from the storage gateway.
- Try the configured primary backend.
- If the primary backend is `datalab`, call Datalab first and fall back to `pypdf` on failure or empty output.
- If the primary backend is `pypdf`, keep the current local behavior.

The adapter remains responsible for returning a complete `ParsedPaper` object. No backend-specific branching should leak into `JobExecutor`.

### Worker Job Boundary
Parsing should remain a dedicated worker job.

- `download_artifact` should continue enqueueing `parse_artifact` for PDF artifacts.
- `parse_artifact` should continue reading the stored PDF and producing a parse row plus chunks.
- Changing the parser backend must not collapse parsing into the download step or move it out of worker execution.

This keeps parsing isolated, retryable, and independently scalable.

### Datalab Integration
Use the Datalab Python SDK as the client boundary. Based on current SDK docs:

- initialize `DatalabClient()` using `DATALAB_API_KEY`
- call `client.convert(...)` on the PDF
- read `result.success`, `result.markdown`, and `result.page_count`
- treat empty or unsuccessful conversion as a backend failure

The Datalab integration should live in a dedicated parsing module, for example `src/research_auto/infrastructure/parsing/datalab_parser.py`, rather than being embedded into the adapter class.

### Text Normalization
Downstream code still expects `full_text` and `chunks` to behave like plain text. For that reason:

- `source_text` stores the verbatim parser output.
- `full_text` stores normalized plain text derived from `source_text`.
- For Datalab, normalization should strip Markdown syntax conservatively enough to preserve section text, lists, and table cell text as readable plain text.
- For `pypdf`, normalization can reuse the current text cleanup path.

This preserves high-fidelity source content while avoiding a broad downstream rewrite in the same task.

### Abstract Extraction and Chunking
Keep abstract extraction and chunking inside parsing infrastructure.

- Abstract extraction should run on normalized `full_text`.
- Chunking should continue producing plain-text chunks for `paper_chunks` so current summary and search code keeps working.
- The chunking algorithm can stay mostly unchanged, with only small adjustments if Markdown-derived text needs cleaner paragraph boundaries.

## Data Model
Persist backend-agnostic source content in `paper_parses`.

Recommended schema changes:
- add `source_text text not null`

Field meanings:
- `source_text`: the original parser output, which may be Markdown or plain text
- `full_text`: normalized text used by the rest of the pipeline

Do not add a Datalab-specific `markdown_text` column. That would make the persistence model care about one backend by name.

## Parser Versioning
Keep parser identity in `parser_version` using the current naming style.

Recommended shape:
- `parser_version`: values such as `pypdf-v1` and `datalab-v1`

This matches the existing style, keeps reporting simple, and avoids introducing an extra backend field unless there is a later need.

`content_hash` should be computed from `source_text`, not normalized `full_text`, so deduplication reflects the real preserved parse payload.

## Configuration
Add parser configuration to `src/research_auto/config.py`:

- `PARSER_BACKEND`, default `datalab`
- `DATALAB_API_KEY`, optional when `PARSER_BACKEND=pypdf`
- optional `DATALAB_BASE_URL` and timeout settings only if the SDK wiring needs them

The worker can temporarily branch on `settings.parser_backend` when constructing the parser adapter in `src/research_auto/interfaces/worker/runner.py`. That is acceptable for now because the branching stays in the composition root, not in the job logic.

## Error Handling
- If Datalab succeeds and returns non-empty Markdown, the parse succeeds with a parser version such as `datalab-v1`.
- If Datalab raises an SDK/API/timeout error, fall back to `pypdf`.
- If Datalab returns an unsuccessful or empty result, fall back to `pypdf`.
- If both backends fail, the parse job fails.
- Missing `DATALAB_API_KEY` should be treated as a configuration error when Datalab is selected as the primary backend.

Fallback behavior must remain inside parsing infrastructure so the job layer does not know or care which backend produced the final parse.

## File-Level Changes
- Modify `src/research_auto/domain/records.py`
  - extend `ParsedPaper` with `source_text`
- Modify `src/research_auto/infrastructure/parsing/adapters.py`
  - turn the current adapter into a backend-selecting adapter
- Add `src/research_auto/infrastructure/parsing/datalab_parser.py`
  - Datalab client wrapper and conversion logic
- Modify `src/research_auto/infrastructure/parsing/pdf_parser.py`
  - keep `pypdf` parsing available as the local backend implementation
- Modify `src/research_auto/infrastructure/postgres/schema.py`
  - add `source_text` to `paper_parses`
- Modify `src/research_auto/infrastructure/postgres/repositories.py`
  - persist and read the new parse fields
- Modify `src/research_auto/config.py`
  - add parser backend settings
- Modify `src/research_auto/interfaces/worker/runner.py`
  - wire parser backend selection if needed for this phase
- Modify `tests/test_pdf_parser.py`
  - cover Datalab success and fallback behavior
- Add or update repository tests for parse persistence

## Testing
- Parser test: Datalab success preserves Markdown in `source_text`, derives normalized `full_text` and chunks, and stores a parser version such as `datalab-v1`.
- Parser test: Datalab exception falls back to `pypdf` and stores a parser version such as `pypdf-v1`.
- Parser test: empty Datalab Markdown falls back to `pypdf`.
- Repository test: `replace_parse()` stores `source_text`, `parser_version`, `full_text`, and chunks correctly.
- Worker/config test: selecting `PARSER_BACKEND=datalab` wires the Datalab-capable parser.
- Regression test: existing summarize inputs still come from `abstract_text` and `paper_chunks` without backend-specific branching.

## Acceptance Criteria
- Setting `PARSER_BACKEND=datalab` makes Datalab the primary parser.
- The parse adapter falls back to `pypdf` automatically when Datalab fails.
- Parsing remains a dedicated worker job executed through `parse_artifact`.
- `paper_parses` stores the original parser output in a backend-agnostic field, and parser identity stays encoded in `parser_version`.
- Existing summarize, search, and QA flows continue to work without backend-specific changes.
- Switching parser backends does not require changing `JobExecutor` logic.
