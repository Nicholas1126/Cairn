from __future__ import annotations

from fastapi.testclient import TestClient
from flock.core.orchestrator import Flock

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
