from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from cairn import skills_store
from cairn.server import db
from cairn.server.app import app


def _client(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    return TestClient(app)


def _md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\nbody\n"


def test_crud_flow(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/skills").json() == []
    assert c.post("/skills", json={"name": "decompile", "content": _md("decompile", "reverse")}).status_code == 201
    lst = c.get("/skills").json()
    assert lst[0]["name"] == "decompile" and lst[0]["description"] == "reverse" and lst[0]["enabled"] is True
    assert "reverse" in c.get("/skills/decompile").json()["content"]
    assert c.put("/skills/decompile", json={"name": "decompile", "content": _md("decompile", "edited")}).status_code == 200
    assert c.get("/skills").json()[0]["description"] == "edited"
    assert c.put("/skills/decompile/enabled", json={"enabled": False}).status_code == 200
    assert c.get("/skills").json()[0]["enabled"] is False
    assert c.delete("/skills/decompile").status_code == 200
    assert c.get("/skills").json() == []


def test_bad_name_rejected(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/skills", json={"name": "../evil", "content": "x"}).status_code == 400


def test_upload_zip(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mytool/SKILL.md", _md("mytool", "ziptool"))
    r = c.post("/skills/upload", files={"file": ("mytool.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201 and r.json()["name"] == "mytool"
    assert c.get("/skills").json()[0]["name"] == "mytool"
