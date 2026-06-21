from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.runtime.local import resolve


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_tools_lists_both_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve, "probe_tool",
                        lambda name: {"launchable": name == "graphify",
                                      "path": "/x/" + name if name == "graphify" else None,
                                      "version": "v1" if name == "graphify" else None})
    client = _client(tmp_path)
    r = client.get("/tools")
    assert r.status_code == 200
    data = r.json()
    names = {t["name"]: t for t in data}
    assert set(names) == {"graphify", "codegraph"}
    assert names["graphify"]["launchable"] is True
    assert names["codegraph"]["launchable"] is False
