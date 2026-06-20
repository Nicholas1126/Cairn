from __future__ import annotations

from pathlib import Path

from cairn.server import db


def test_cairn_home_defaults_to_dot_cairn(monkeypatch):
    monkeypatch.delenv("CAIRN_HOME", raising=False)
    assert db.cairn_home() == Path.home() / ".cairn"


def test_cairn_home_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "custom"))
    assert db.cairn_home() == tmp_path / "custom"


def test_default_db_under_cairn_home(monkeypatch):
    monkeypatch.delenv("CAIRN_HOME", raising=False)
    assert db.default_db() == Path.home() / ".cairn" / "cairn.db"
