from __future__ import annotations

from fastapi.testclient import TestClient

from research_auto.interfaces.api.app import create_app


def test_fastapi_mounts_mcp_path() -> None:
    app = create_app()

    mounted_paths = sorted(route.path for route in app.routes)

    assert "/mcp" in mounted_paths


def test_mcp_mount_accepts_http_requests() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        response = client.get("/mcp")

    assert response.status_code in {200, 404, 405, 406}


def test_mcp_mount_coexists_with_health_endpoint() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        health = client.get("/healthz")
        mcp = client.get("/mcp")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert mcp.status_code in {200, 404, 405, 406}
