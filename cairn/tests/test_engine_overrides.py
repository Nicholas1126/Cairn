from __future__ import annotations

import json

from cairn.dispatcher.runtime.local import resolve


def _point_config_at(monkeypatch, tmp_path):
    cfg = tmp_path / "engines.json"
    monkeypatch.setattr(resolve, "engines_config_path", lambda: cfg)
    return cfg


def test_load_overrides_empty_when_missing(monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    assert resolve.load_overrides() == {}


def test_set_override_creates_and_merges(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    resolve.set_override("pi", "/abs/pi", "direct")
    resolve.set_override("opencode", "/abs/opencode", "direct")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["pi"] == {"path": "/abs/pi", "launcher": "direct"}
    assert data["opencode"]["path"] == "/abs/opencode"
    assert not any(p.name.endswith(".tmp") for p in cfg.parent.iterdir())


def test_remove_override_only_target(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    resolve.set_override("pi", "/abs/pi", "direct")
    resolve.set_override("opencode", "/abs/opencode", "direct")
    resolve.remove_override("pi")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "pi" not in data
    assert "opencode" in data


def test_load_overrides_tolerates_corrupt_json(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    cfg.write_text("{ not json", encoding="utf-8")
    assert resolve.load_overrides() == {}


def test_engines_config_path_follows_cairn_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "h"))
    assert resolve.engines_config_path() == tmp_path / "h" / "engines.json"
