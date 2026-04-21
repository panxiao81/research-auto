# CLI Redesign Design

## Goal

Simplify the command-line interface by replacing the current flat command list with a small set of task-oriented groups.

## Current State

The CLI currently exposes many top-level commands from `src/research_auto/interfaces/cli/app.py`, including setup, pipeline, inspection, and runtime operations in one flat namespace. A separate `scripts/trigger_pipeline.py` duplicates part of that surface.

## Proposed Shape

Use four top-level groups:

- `setup`: bootstrap and seed operations
- `pipeline`: queueing and pipeline repair commands
- `inspect`: search and read-only QA commands
- `serve`: long-running processes

### Command Map

- `setup bootstrap-db`
- `setup seed icse`
- `pipeline resolve [--limit N]`
- `pipeline parse [--limit N]`
- `pipeline summarize [--limit N]`
- `pipeline resummarize-fallbacks [--limit N]`
- `pipeline repair-resolution-status`
- `pipeline drain [--queue NAME]`
- `inspect search QUERY [--limit N]`
- `inspect paper PAPER_ID`
- `inspect ask paper PAPER_ID QUESTION [--limit N]`
- `inspect ask library QUESTION [--limit N]`
- `serve worker [--once] [--queue NAME]`
- `serve api [--host HOST] [--port PORT]`

## Behavior

- Remove the old flat command names rather than keeping aliases.
- Make the new grouped CLI the only supported entry point.
- Fold `scripts/trigger_pipeline.py` into the grouped CLI and remove the duplicate flow.
- Preserve current command behavior, output formats, and defaults unless a grouped name requires a small flag rename.

## Implementation Notes

- Keep the CLI entrypoint in `src/research_auto/interfaces/cli/app.py`.
- Reuse the existing command functions; only the parser and dispatch layer should change.
- Update package entrypoints and any docs that still reference the flat commands or trigger script.

## Risks

- Any shell scripts or docs that still call the old command names will need updates.
- The CLI tests should be updated to validate the grouped command names and key behaviors.

## Success Criteria

- The CLI help reads as grouped tasks instead of a flat command list.
- The trigger script is no longer part of the user-facing workflow.
- Existing CLI behaviors still work under their new grouped names.
