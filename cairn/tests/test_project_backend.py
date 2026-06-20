from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_backend_column_exists_and_defaults_docker(tmp_path):
    _client(tmp_path)
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "backend" in cols


def test_create_project_defaults_to_docker(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects", json={"title": "t", "origin": "o", "goal": "g"})
    assert r.status_code == 201
    assert r.json()["project"]["backend"] == "docker"


def test_create_project_with_local_backend(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects", json={"title": "t", "origin": "o", "goal": "g", "backend": "local"})
    assert r.status_code == 201
    pid = r.json()["project"]["id"]
    assert r.json()["project"]["backend"] == "local"
    assert c.get("/projects").json()[0]["backend"] == "local"
    assert c.get(f"/projects/{pid}").json()["project"]["backend"] == "local"
