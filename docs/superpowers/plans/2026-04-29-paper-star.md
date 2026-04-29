# Paper Star Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the single current user star and unstar papers, then filter/search the library by starred papers.

**Architecture:** Store the star state directly on `papers` as a boolean column so the feature stays simple and durable. Expose the flag through the existing read repository, add a toggle endpoint on the paper detail page, and thread a `starred` filter into the papers list and search views.

**Tech Stack:** Python, FastAPI, PostgreSQL, Jinja2 templates, pytest.

---

### Task 1: Add paper star persistence and query support

**Files:**
- Modify: `src/research_auto/infrastructure/postgres/schema.py:41-76`
- Modify: `src/research_auto/infrastructure/postgres/repositories.py:542-737`
- Modify: `src/research_auto/infrastructure/testing/fake_database.py:22-340`
- Test: `tests/test_frontend.py`

- [ ] **Step 1: Write the failing test**

```python
def test_papers_page_can_filter_starred_papers(client):
    response = client.get("/ui/papers?starred=true")
    assert response.status_code == 200
    assert "Breaking Single-Tester Limits" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_frontend.py -k starred -v`
Expected: FAIL because `starred` is not yet supported.

- [ ] **Step 3: Write minimal implementation**

```python
-- in schema.py
alter table papers add column if not exists starred boolean not null default false;

# in list_papers
if starred is not None:
    filters.append("p.starred = %s")
    params.append(starred)

# in get_paper_detail/search/list queries include p.starred in selected fields
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_frontend.py -k starred -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/research_auto/infrastructure/postgres/schema.py src/research_auto/infrastructure/postgres/repositories.py src/research_auto/infrastructure/testing/fake_database.py tests/test_frontend.py
git commit -m "feat: support starring papers"
```

### Task 2: Add paper star toggle UI and route

**Files:**
- Modify: `src/research_auto/interfaces/web/routes.py:270-337`
- Modify: `templates/pages/paper_detail.html:1-106`
- Modify: `templates/partials/paper_row.html:1-19`
- Modify: `templates/pages/papers.html:1-59`
- Test: `tests/test_frontend.py`

- [ ] **Step 1: Write the failing test**

```python
def test_paper_detail_shows_star_toggle(client):
    response = client.get("/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49")
    assert 'action="/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49/star"' in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_frontend.py -k star_toggle -v`
Expected: FAIL because the route and form do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# in routes.py
@router.post("/ui/papers/{paper_id}/star")
async def ui_toggle_paper_star(request: Request, paper_id: str):
    detail = _paper_detail_or_404(request, paper_id)
    starred = not bool(detail["paper"].get("starred"))
    request.app.state.job_repository.set_paper_starred(paper_id=paper_id, starred=starred)
    return RedirectResponse(url=f"/ui/papers/{paper_id}", status_code=303)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_frontend.py -k star_toggle -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/research_auto/interfaces/web/routes.py templates/pages/paper_detail.html templates/partials/paper_row.html templates/pages/papers.html tests/test_frontend.py
git commit -m "feat: add paper star toggle UI"
```

### Task 3: Add starred search/list presentation and verification

**Files:**
- Modify: `src/research_auto/interfaces/web/routes.py:221-337`
- Modify: `src/research_auto/infrastructure/postgres/repositories.py:547-687`
- Modify: `tests/test_frontend.py`

- [ ] **Step 1: Write the failing test**

```python
def test_search_results_can_show_starred_state(client):
    response = client.get("/ui/search?q=testing")
    assert response.status_code == 200
    assert "Starred" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_frontend.py -k search_results_can_show_starred_state -v`
Expected: FAIL because starred metadata is not rendered yet.

- [ ] **Step 3: Write minimal implementation**

```python
# include starred in search/list rows and render a badge in paper_row.html
{% if row.starred %}<span class="badge text-bg-warning">Starred</span>{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_frontend.py -k search_results_can_show_starred_state -v`
Expected: PASS.

- [ ] **Step 5: Run the full frontend test file**

Run: `pytest tests/test_frontend.py -v`
Expected: PASS.
