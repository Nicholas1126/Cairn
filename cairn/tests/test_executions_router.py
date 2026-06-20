from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    return TestClient(app)


def _payload(**over):
    base = dict(
        phase="explore", worker_name="w", model="m", command=["opencode", "run", "sk-secret123"],
        prompt="do", outcome="success", started_at="2026-06-19-01-00-00",
        ended_at="2026-06-19-01-00-05", duration_ms=5, stdout="hello", stderr="",
        intent_id="i001", produced_fact_id="f001",
    )
    base.update(over)
    return base


def test_post_records_redacts_and_writes_file(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects/p1/executions", json=_payload())
    assert r.status_code == 201
    exec_id = r.json()["id"]
    detail = c.get(f"/projects/p1/executions/{exec_id}").json()
    assert "sk-secret123" not in "".join(detail["command"])  # redacted
    assert detail["has_log"] is True
    log = c.get(f"/projects/p1/executions/{exec_id}/log")
    assert log.status_code == 200 and "hello" in log.text


def test_list_and_zip(tmp_path):
    c = _client(tmp_path)
    c.post("/projects/p1/executions", json=_payload())
    c.post("/projects/p1/executions", json=_payload(phase="reason", intent_id=None, produced_fact_id=None))
    assert len(c.get("/projects/p1/executions").json()) == 2
    z = c.get("/projects/p1/executions/logs.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_file_logging_off_skips_file_no_download(tmp_path):
    c = _client(tmp_path)
    with db.get_conn() as conn:
        conn.execute("UPDATE settings SET execution_file_logging = 0 WHERE rowid = 1")
    exec_id = c.post("/projects/p1/executions", json=_payload()).json()["id"]
    assert c.get(f"/projects/p1/executions/{exec_id}").json()["has_log"] is False
    assert c.get(f"/projects/p1/executions/{exec_id}/log").status_code == 404
    assert c.get("/projects/p1/executions/logs.zip").status_code == 404


def test_record_disabled_returns_204_and_stores_nothing(tmp_path):
    c = _client(tmp_path)
    with db.get_conn() as conn:
        conn.execute("UPDATE settings SET execution_record_enabled = 0 WHERE rowid = 1")
    assert c.post("/projects/p1/executions", json=_payload()).status_code == 204
    assert c.get("/projects/p1/executions").json() == []
