from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
from click.testing import CliRunner

from flockos import cli


def test_status_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PID_FILE", tmp_path / "flockos.pid")
    res = CliRunner().invoke(cli.main, ["status"])
    assert res.exit_code == 0
    assert "not running" in res.output.lower()


def test_stop_removes_stale_pidfile(tmp_path, monkeypatch):
    pid_file = tmp_path / "flockos.pid"
    pid_file.write_text("999999999")  # 不存在的 pid
    monkeypatch.setattr(cli, "PID_FILE", pid_file)
    killed = {}
    def fake_kill(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(os, "kill", fake_kill)
    res = CliRunner().invoke(cli.main, ["stop"])
    assert res.exit_code == 0
    assert not pid_file.exists()


def test_start_foreground_invokes_uvicorn(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PID_FILE", tmp_path / "flockos.pid")
    called = {}
    def fake_run(app, host, port, **kw):
        called["host"] = host
        called["port"] = port
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "build_app", lambda: object())
    monkeypatch.setattr(cli, "_ensure_frontend", lambda: None)
    res = CliRunner().invoke(cli.main, ["start", "--foreground", "--port", "8123"])
    assert res.exit_code == 0
    assert called["port"] == 8123


def test_start_builds_frontend_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PID_FILE", tmp_path / "flockos.pid")
    monkeypatch.setattr(cli, "FLOCK_STATIC", tmp_path / "static" / "flock")  # 不存在
    built = {"n": 0}
    monkeypatch.setattr(cli, "_ensure_frontend", lambda: built.__setitem__("n", built["n"] + 1))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "build_app", lambda: object())
    res = CliRunner().invoke(cli.main, ["start", "--foreground"])
    assert res.exit_code == 0
    assert built["n"] == 1
