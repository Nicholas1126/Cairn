from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db, chat
from cairn.server.app import app
from cairn.server.models import ChatTurnResult


def test_chat_runtime_debug_seeds_separate_root():
    rt = chat._chat_runtime(debug=True)
    assert "chats-debug" in str(rt._root)
    assert rt._agents_source is not None  # seeds worker context


def test_chat_runtime_bare_when_not_debug():
    rt = chat._chat_runtime(debug=False)
    assert str(rt._root).rstrip("/").endswith("chats")
    assert rt._agents_source is None


def test_worker_context_files_includes_agents_md():
    files = chat.worker_context_files()
    names = [f["name"] for f in files]
    assert any(n == "AGENTS.md" for n in names)
    agents = next(f for f in files if f["name"] == "AGENTS.md")
    assert isinstance(agents["content"], str) and len(agents["content"]) > 0


def test_request_has_debug_default_false():
    from cairn.server.models import ChatTurnRequest
    assert ChatTurnRequest(worker="w", message="m").debug is False


def test_post_turn_passes_debug(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    captured = {}
    def fake_run_turn(worker, message, session, debug):
        captured["debug"] = debug
        return ChatTurnResult(reply="ok", command=["x"], prompt=message, stdout="", outcome="success")
    monkeypatch.setattr(chat, "run_turn", fake_run_turn)
    c = TestClient(app)
    c.post("/chat/turn", json={"worker": "w", "message": "hi", "debug": True})
    assert captured["debug"] is True


def test_get_context_endpoint(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    c = TestClient(app)
    r = c.get("/chat/context")
    assert r.status_code == 200
    assert any(f["name"] == "AGENTS.md" for f in r.json()["files"])
