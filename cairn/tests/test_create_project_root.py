from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_create_with_existing_project_root_persists_and_reads_back(tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    client = _client(tmp_path)
    r = client.post("/projects", json={
        "title": "t", "origin": "o", "goal": "g", "project_root": str(a),
    })
    assert r.status_code == 201, r.text
    assert r.json()["project"]["project_root"] == str(a)
    pid = r.json()["project"]["id"]
    got = client.get(f"/projects/{pid}")
    assert got.json()["project"]["project_root"] == str(a)


def test_create_without_project_root_is_none(tmp_path):
    client = _client(tmp_path)
    r = client.post("/projects", json={"title": "t", "origin": "o", "goal": "g"})
    assert r.status_code == 201
    assert r.json()["project"]["project_root"] is None


def test_create_with_missing_project_root_dir_returns_400(tmp_path):
    client = _client(tmp_path)
    r = client.post("/projects", json={
        "title": "t", "origin": "o", "goal": "g",
        "project_root": str(tmp_path / "does-not-exist"),
    })
    assert r.status_code == 400
