from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db, chat
from cairn.server.app import app
from cairn.server.models import ChatTurnResult, ChatWorker


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_get_workers(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(chat, "list_workers", lambda: [ChatWorker(name="oc", type="opencode", model="m")])
    r = c.get("/chat/workers")
    assert r.status_code == 200
    assert r.json() == [{"name": "oc", "type": "opencode", "model": "m"}]


def test_post_turn(tmp_path, monkeypatch):
    c = _client(tmp_path)
    def fake_run_turn(worker, message, session, debug=False):
        return ChatTurnResult(reply="pong", session="ses_1", command=["opencode", "run"],
                              prompt=message, stdout="raw", exit_code=0, outcome="success", duration_ms=12)
    monkeypatch.setattr(chat, "run_turn", fake_run_turn)
    r = c.post("/chat/turn", json={"worker": "oc", "message": "ping"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "pong" and body["session"] == "ses_1" and body["outcome"] == "success"
