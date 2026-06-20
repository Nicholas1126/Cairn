from __future__ import annotations

import json

from cairn.dispatcher.runtime.local import resolve


def test_resolve_unix_direct(monkeypatch):
    monkeypatch.setattr(resolve.os, "name", "posix")
    monkeypatch.setattr(resolve, "_load_overrides", lambda: {})
    monkeypatch.setattr(resolve, "_augmented_dirs", lambda: [])
    monkeypatch.setattr(resolve.shutil, "which", lambda name, path=None: "/usr/local/bin/opencode" if name == "opencode" else None)
    r = resolve.resolve_engine("opencode")
    assert r is not None and r.path == "/usr/local/bin/opencode" and r.launcher == "direct"


def test_resolve_windows_prefers_cmd(monkeypatch):
    monkeypatch.setattr(resolve.os, "name", "nt")
    monkeypatch.setattr(resolve, "_load_overrides", lambda: {})
    monkeypatch.setattr(resolve, "_augmented_dirs", lambda: [])
    found = {"opencode.cmd": r"C:\\npm\\opencode.cmd"}
    monkeypatch.setattr(resolve.shutil, "which", lambda name, path=None: found.get(name))
    r = resolve.resolve_engine("opencode")
    assert r is not None and r.path.endswith("opencode.cmd") and r.launcher == "cmd"


def test_resolve_override_wins(monkeypatch):
    monkeypatch.setattr(resolve, "_load_overrides", lambda: {"pi": {"path": "/custom/pi", "launcher": "direct"}})
    r = resolve.resolve_engine("pi")
    assert r is not None and r.path == "/custom/pi" and r.source == "override"


def test_resolve_missing_returns_none(monkeypatch):
    monkeypatch.setattr(resolve.os, "name", "posix")
    monkeypatch.setattr(resolve, "_load_overrides", lambda: {})
    monkeypatch.setattr(resolve, "_augmented_dirs", lambda: [])
    monkeypatch.setattr(resolve.shutil, "which", lambda name, path=None: None)
    assert resolve.resolve_engine("opencode") is None


def test_launch_argv_per_launcher():
    assert resolve.launch_argv(resolve.Resolved("/a/opencode", "direct", "path"), ["x"]) == ["/a/opencode", "x"]
    assert resolve.launch_argv(resolve.Resolved(r"C:\\o.cmd", "cmd", "path"), ["x"]) == ["cmd", "/c", r"C:\\o.cmd", "x"]
    ps = resolve.launch_argv(resolve.Resolved(r"C:\\o.ps1", "powershell", "path"), ["x"])
    assert ps[0] == "powershell" and ps[-2:] == [r"C:\\o.ps1", "x"]


def test_overrides_loaded_from_engines_json(monkeypatch, tmp_path):
    cfg = tmp_path / "engines.json"
    cfg.write_text(json.dumps({"opencode": {"path": "/x/opencode"}}), encoding="utf-8")
    monkeypatch.setattr(resolve, "_engines_config_path", lambda: cfg)
    assert resolve._load_overrides() == {"opencode": {"path": "/x/opencode"}}
