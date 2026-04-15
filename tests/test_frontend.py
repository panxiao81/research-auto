from __future__ import annotations

from fastapi.testclient import TestClient

from research_auto.interfaces.api.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_ui_home_renders() -> None:
    client = _client()
    response = client.get("/ui")
    assert response.status_code == 200
    assert "Paper Library" in response.text
    assert "Recent Ready Papers" in response.text


def test_ui_papers_sheet_renders() -> None:
    client = _client()
    response = client.get("/ui/papers")
    assert response.status_code == 200
    assert "Sheet view for browsing papers" in response.text
    assert "Summarized first" in response.text


def test_ui_search_renders_results() -> None:
    client = _client()
    response = client.get("/ui/search?q=single+tester+limits")
    assert response.status_code == 200
    assert "Search" in response.text
    assert "Breaking Single-Tester Limits" in response.text


def test_ui_paper_detail_renders_summary() -> None:
    client = _client()
    response = client.get("/ui/papers/a7ccafea-b80f-4a01-bc18-42347badee49")
    assert response.status_code == 200
    assert "Structured Summary" in response.text
    assert "Citation" in response.text
    assert "@inproceedings{" in response.text
    assert "研究问题" in response.text
    assert "未来工作" in response.text


def test_ui_stats_and_jobs_render() -> None:
    client = _client()
    stats = client.get("/ui/stats")
    jobs = client.get("/ui/jobs")
    assert stats.status_code == 200
    assert jobs.status_code == 200
    assert "Summary Providers" in stats.text
    assert "Jobs" in jobs.text
