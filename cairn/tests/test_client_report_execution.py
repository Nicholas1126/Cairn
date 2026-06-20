from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.protocol.client import CairnClient


class _Adapter:
    """Route CairnClient requests into the in-process FastAPI TestClient."""
    def __init__(self, tc): self.tc = tc

    def request(self, method, url, json=None, params=None, timeout=None):
        path = url.replace("http://test", "")
        return self.tc.request(method, path, json=json, params=params)


def test_report_execution_posts_and_returns_ok(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    tc = TestClient(app)
    client = CairnClient("http://test")
    monkeypatch.setattr(client, "_session", lambda: _Adapter(tc))
    result = client.report_execution("p1", {
        "phase": "explore", "worker_name": "w", "command": ["x"], "prompt": "p",
        "outcome": "success", "started_at": "t0", "ended_at": "t1", "duration_ms": 1,
        "stdout": "o", "stderr": "",
    })
    assert result.ok
    assert result.status_code in (201, 204)
