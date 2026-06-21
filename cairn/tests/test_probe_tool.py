from __future__ import annotations

import subprocess

from cairn.dispatcher.runtime.local import resolve


def test_tools_set_is_graphify_and_codegraph():
    assert resolve.TOOLS == ("graphify", "codegraph")


def test_probe_tool_not_found(monkeypatch):
    monkeypatch.setattr(resolve.shutil, "which", lambda *a, **k: None)
    out = resolve.probe_tool("codegraph")
    assert out == {"launchable": False, "path": None, "version": None}


def test_probe_tool_found_and_launchable(monkeypatch):
    monkeypatch.setattr(resolve.shutil, "which", lambda *a, **k: "/usr/bin/graphify")

    class _CP:
        returncode = 0
        stdout = "graphify 0.8.41\n"
        stderr = ""

    monkeypatch.setattr(resolve.subprocess, "run", lambda *a, **k: _CP())
    out = resolve.probe_tool("graphify")
    assert out["launchable"] is True
    assert out["path"] == "/usr/bin/graphify"
    assert out["version"] == "graphify 0.8.41"
