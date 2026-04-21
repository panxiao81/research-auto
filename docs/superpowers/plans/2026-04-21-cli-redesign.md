# CLI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat CLI with grouped task-oriented commands and remove the duplicate trigger script path.

**Architecture:** Keep the existing command functions in `src/research_auto/interfaces/cli/app.py`, but replace the parser with nested groups for setup, pipeline, inspect, and serve. Update docs and the trigger script so the grouped CLI is the only user-facing entry point.

**Tech Stack:** Python 3.11, `argparse`, FastAPI, PostgreSQL, `pytest`

---

### Task 1: Rework the CLI parser into grouped commands

**Files:**
- Modify: `src/research_auto/interfaces/cli/app.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
from research_auto.interfaces.cli.app import build_parser


def test_grouped_cli_parser_exposes_new_commands():
    parser = build_parser()
    help_text = parser.format_help()

    assert "setup" in help_text
    assert "pipeline" in help_text
    assert "inspect" in help_text
    assert "serve" in help_text
    assert "bootstrap-db" not in help_text
    assert "seed-icse" not in help_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_grouped_cli_parser_exposes_new_commands -v`
Expected: FAIL because the current parser still exposes flat commands.

- [ ] **Step 3: Write minimal implementation**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-auto")
    subparsers = parser.add_subparsers(dest="group", required=True)

    setup_parser = subparsers.add_parser("setup")
    setup_subparsers = setup_parser.add_subparsers(dest="command", required=True)
    setup_subparsers.add_parser("bootstrap-db")
    setup_subparsers.add_parser("seed-icse")

    pipeline_parser = subparsers.add_parser("pipeline")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="command", required=True)
    resolve_parser = pipeline_subparsers.add_parser("resolve")
    resolve_parser.add_argument("--limit", type=int)
    parse_parser = pipeline_subparsers.add_parser("parse")
    parse_parser.add_argument("--limit", type=int)
    summarize_parser = pipeline_subparsers.add_parser("summarize")
    summarize_parser.add_argument("--limit", type=int)
    resummarize_parser = pipeline_subparsers.add_parser("resummarize-fallbacks")
    resummarize_parser.add_argument("--limit", type=int)
    repair_parser = pipeline_subparsers.add_parser("repair-resolution-status")
    drain_parser = pipeline_subparsers.add_parser("drain")
    drain_parser.add_argument("--queue")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_subparsers = inspect_parser.add_subparsers(dest="command", required=True)
    search_parser = inspect_subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    paper_parser = inspect_subparsers.add_parser("paper")
    paper_parser.add_argument("paper_id")
    ask_parser = inspect_subparsers.add_parser("ask")
    ask_subparsers = ask_parser.add_subparsers(dest="target", required=True)
    ask_paper_parser = ask_subparsers.add_parser("paper")
    ask_paper_parser.add_argument("paper_id")
    ask_paper_parser.add_argument("question")
    ask_paper_parser.add_argument("--limit", type=int, default=8)
    ask_library_parser = ask_subparsers.add_parser("library")
    ask_library_parser.add_argument("question")
    ask_library_parser.add_argument("--limit", type=int, default=8)

    serve_parser = subparsers.add_parser("serve")
    serve_subparsers = serve_parser.add_subparsers(dest="command", required=True)
    worker_parser = serve_subparsers.add_parser("worker")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--queue")
    api_parser = serve_subparsers.add_parser("api")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8000)
    return parser
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_grouped_cli_parser_exposes_new_commands -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/research_auto/interfaces/cli/app.py tests/test_cli.py
git commit -m "refactor: group CLI commands by task"
```

### Task 2: Remove the trigger script dependency

**Files:**
- Delete: `scripts/trigger_pipeline.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path


def test_trigger_script_is_removed():
    assert not Path("scripts/trigger_pipeline.py").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trigger_script.py::test_trigger_script_is_removed -v`
Expected: FAIL until the script is removed.

- [ ] **Step 3: Write minimal implementation**

```python
"""Removed in favor of the grouped research-auto CLI entrypoint."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trigger_script.py::test_trigger_script_is_removed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: update CLI usage for grouped commands"
```

### Task 3: Verify end-to-end CLI behavior

**Files:**
- Modify: none
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
from research_auto.interfaces.cli.app import build_parser


def test_grouped_cli_dispatch_names():
    parser = build_parser()
    assert parser.parse_args(["setup", "bootstrap-db"]).group == "setup"
    assert parser.parse_args(["pipeline", "resolve", "--limit", "2"]).command == "resolve"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_grouped_cli_dispatch_names -v`
Expected: FAIL until the parser is updated.

- [ ] **Step 3: Write minimal implementation**

```python
def main() -> None:
    args = build_parser().parse_args()
    if args.group == "setup" and args.command == "bootstrap-db":
        bootstrap_db()
    elif args.group == "setup" and args.command == "seed-icse":
        seed_icse()
    elif args.group == "pipeline" and args.command == "resolve":
        enqueue_resolve(args.limit)
    # ...remaining grouped dispatch cases...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/research_auto/interfaces/cli/app.py tests/test_cli.py
git commit -m "test: cover grouped cli dispatch"
```
