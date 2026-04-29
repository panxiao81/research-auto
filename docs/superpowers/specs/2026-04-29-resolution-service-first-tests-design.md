# Resolution Service First Tests Design

## Goal
Add the first safe unit-test slice for `src/research_auto/infrastructure/resolution/service.py` so later refactoring can proceed with a behavior safety net.

## Scope
- Add characterization tests for the pure function `infer_arxiv_fallback_reason()`.
- Cover the main fallback-reason branches without touching network, database, or HTML parsing flows.
- Keep production code unchanged unless the test reveals a real bug.

Out of scope:
- Refactoring `resolution/service.py` in this step.
- Testing `resolve_detail_page()`, `query_arxiv()`, or other I/O-heavy flows.
- Changing fallback reason semantics.

## Why This Slice First
`resolution/service.py` is still the lowest-coverage high-risk module. Starting with the pure fallback-reason helper gives a cheap, hermetic safety net around behavior that matters to the repo: explicit arXiv fallback reasons must remain stable and understandable.

This is the best first slice because it:
- avoids mocks and external dependencies,
- locks in current behavior before refactoring,
- creates a small tested seam near the center of the arXiv fallback path.

## Behaviors To Lock Down
Add tests for these branches in `infer_arxiv_fallback_reason()`:
- `detail_access_failed=True` returns `detail_page_access_failed`
- empty artifact list returns `no_links_on_detail_page`
- DOI or publication links without a downloadable PDF return `landing_page_without_accessible_pdf`
- only non-paper links return `non_paper_links_only`
- mixed links without a usable paper PDF return `no_accessible_paper_pdf`

## Files
- Create: `tests/test_resolution_service.py`
- Read only: `src/research_auto/infrastructure/resolution/service.py`

## Testing Strategy
- Build tiny `ArtifactRecord` fixtures inline in the test file.
- Call the real `infer_arxiv_fallback_reason()` function directly.
- Assert exact reason strings so future refactors preserve semantics.
- Keep each test focused on one branch.

## Acceptance Criteria
- The repo has a new unit test file for `resolution/service.py`.
- The fallback-reason helper is covered by direct pure-function tests.
- The new tests pass without production code changes unless a real bug is exposed.
