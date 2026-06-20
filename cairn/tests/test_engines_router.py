from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.runtime.local import resolve


def _client(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    monkeypatch.setattr(resolve, "engines_config_path", lambda: tmp_path / "engines.json")
    def fake_probe(t):
        if t == "opencode":
            return {"launchable": True, "path": "/usr/local/bin/opencode", "version": "1.17.8", "source": "path"}
        return {"launchable": False, "path": None, "version": None, "source": None}
    monkeypatch.setattr(resolve, "probe_engine", fake_probe)
    return TestClient(app)


def test_list_engines_returns_four_types_no_creds(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/engines")
    assert r.status_code == 200
    types = {e["type"] for e in r.json()}
    assert types == {"claudecode", "codex", "opencode", "pi"}
    oc = next(e for e in r.json() if e["type"] == "opencode")
    assert oc["launchable"] is True and oc["version"] == "1.17.8"
    assert "apiKey" not in r.text and "api_key" not in r.text


def test_put_and_delete_override(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.put("/engines/pi/override", json={"path": "/abs/pi", "launcher": "direct"})
    assert r.status_code == 200
    assert r.json()["override"]["path"] == "/abs/pi"
    pi = next(e for e in c.get("/engines").json() if e["type"] == "pi")
    assert pi["override"]["path"] == "/abs/pi"
    assert c.delete("/engines/pi/override").status_code == 200
    pi = next(e for e in c.get("/engines").json() if e["type"] == "pi")
    assert pi["override"] is None


def test_unknown_type_404(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.put("/engines/nope/override", json={"path": "/x"}).status_code == 404
    assert c.delete("/engines/nope/override").status_code == 404
