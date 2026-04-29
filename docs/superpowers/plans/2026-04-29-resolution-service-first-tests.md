# Resolution Service First Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first behavior-locking unit tests for `resolution/service.py` before any refactor.

**Architecture:** Test only the pure helper `infer_arxiv_fallback_reason()` using inline `ArtifactRecord` values. This keeps the first safety net hermetic and cheap while pinning down fallback semantics that later refactoring must preserve.

**Tech Stack:** Python, pytest, dataclasses from the existing resolution service module.

---

### Task 1: Add fallback-reason characterization tests

**Files:**
- Create: `tests/test_resolution_service.py`
- Test: `tests/test_resolution_service.py`

- [ ] **Step 1: Write the failing test**

```python
from research_auto.infrastructure.resolution.service import infer_arxiv_fallback_reason


def test_infer_arxiv_fallback_reason_for_empty_artifacts() -> None:
    assert infer_arxiv_fallback_reason([]) == "no_links_on_detail_page"
```

- [ ] **Step 2: Run test to verify it fails or at least exercises the new file first**

Run: `"/home/panxiao81/.local/bin/uv" run pytest tests/test_resolution_service.py -v`
Expected: If the file is new, the first run should fail because the file does not exist yet. After creation, the tests should exercise the current implementation directly.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from research_auto.infrastructure.resolution.service import (
    ArtifactRecord,
    infer_arxiv_fallback_reason,
)


def _artifact(kind: str) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_kind=kind,
        label=kind,
        resolution_reason=None,
        source_url=f"https://example.com/{kind}",
        resolved_url=f"https://example.com/{kind}.pdf",
        downloadable=kind in {"direct_pdf", "publisher_pdf", "preprint", "attachment_pdf", "fallback_to_arxiv"},
        mime_type="application/pdf" if "pdf" in kind or kind == "fallback_to_arxiv" else None,
    )
```

Then add five tests covering the branches described in the spec.

- [ ] **Step 4: Run test to verify it passes**

Run: `"/home/panxiao81/.local/bin/uv" run pytest tests/test_resolution_service.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `"/home/panxiao81/.local/bin/uv" run pytest -q`
Expected: PASS.
