from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cairn.server import db, execstore
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    return TestClient(app)


def test_settings_get_put_roundtrip_toggles(tmp_path):
    c = _client(tmp_path)
    assert c.get("/settings").json()["execution_file_logging"] is True
    c.put("/settings", json={"intent_timeout": 15, "reason_timeout": 15,
                             "execution_record_enabled": True, "execution_file_logging": False})
    assert c.get("/settings").json()["execution_file_logging"] is False


def test_delete_project_removes_log_dir_and_rows(tmp_path):
    c = _client(tmp_path)
    payload = dict(phase="explore", worker_name="w", model="m", command=["x"], prompt="p",
                   outcome="success", started_at="2026-06-19-01-00-00",
                   ended_at="2026-06-19-01-00-01", duration_ms=1, stdout="o", stderr="")
    exec_id = c.post("/projects/p1/executions", json=payload).json()["id"]
    log_path = Path(c.get(f"/projects/p1/executions/{exec_id}").json()["log_path"])
    assert log_path.exists()
    c.delete("/projects/p1")
    assert not log_path.exists()
    assert not log_path.parent.exists()
