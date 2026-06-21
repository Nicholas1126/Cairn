from __future__ import annotations

import pytest
from cairn.dispatcher.config import WorkerConfig
from flockos.engine import CairnAgentEngine


def _claude_worker() -> WorkerConfig:
    return WorkerConfig(
        name="flock-claude",
        type="claudecode",
        task_types=["explore"],
        max_running=1,
        priority=0,
        env={
            "ANTHROPIC_MODEL": "m",
            "ANTHROPIC_BASE_URL": "http://x",
            "ANTHROPIC_AUTH_TOKEN": "t",
        },
    )


def test_build_argv_uses_cairn_driver():
    eng = CairnAgentEngine(worker=_claude_worker())
    argv, session = eng._build_argv("PROMPT-TEXT")
    assert argv[0] == "claude"
    assert "PROMPT-TEXT" in argv
    assert session is not None  # claudecode seeds a session uuid


def test_run_invokes_local_process(monkeypatch):
    eng = CairnAgentEngine(worker=_claude_worker())
    captured = {}

    class FakeProc:
        def __init__(self, command, env, cwd):
            captured["command"] = command
            captured["env"] = env
            captured["cwd"] = cwd

        def start(self):
            captured["started"] = True

        def communicate(self, timeout):
            from cairn.dispatcher.runtime.process import ProcessResult
            return ProcessResult(returncode=0, stdout="OUT", stderr="ERR",
                                 timed_out=False, cancelled=False, cancel_reason=None)

    monkeypatch.setattr("flockos.engine.LocalManagedProcess", FakeProc)
    stdout, stderr, rc = eng._run(["echo", "hi"], {"K": "V"}, None, timeout=5)
    assert (stdout, stderr, rc) == ("OUT", "ERR", 0)
    assert captured["started"] is True
    assert captured["command"] == ["echo", "hi"]
    # env 必须把 worker.env 合并进去
    assert captured["env"]["ANTHROPIC_MODEL"] == "m"
    assert captured["env"]["K"] == "V"
