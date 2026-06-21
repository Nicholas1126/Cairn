from __future__ import annotations

import cairn.server.db as cairn_db
from fastapi.testclient import TestClient
from flock.core.orchestrator import Flock

from flockos.app import build_app
from flockos.flock_app import build_flock_app


def test_build_flock_app_exposes_api():
    flock = Flock("flockos")
    app = build_flock_app(flock)
    client = TestClient(app)
    # HealthAndMetricsComponent is wired with name="health_internal" and NO
    # prefix in _serve_dashboard, so the real registered path is "/health"
    # (NOT "/api/health"). Pin the exact behavior.
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unified_app_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    # Reset db state so configure() initialises a fresh test-scoped database.
    cairn_db._db_path = None
    cairn_db.configure(tmp_path / "cairn.db")

    app = build_app()
    client = TestClient(app, follow_redirects=False)

    # Root redirects to flock dashboard.
    r = client.get("/")
    assert r.status_code in (302, 307)
    assert r.headers["location"].rstrip("/").endswith("/flock")

    # Cairn's existing routes must remain intact.
    assert client.get("/engines").status_code == 200

    # /cairn serves the cairn SPA.
    assert client.get("/cairn").status_code == 200

    # Flock dashboard is reachable at /flock/ without running flock's lifespan.
    assert client.get("/flock/").status_code == 200
