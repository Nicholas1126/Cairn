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


import json
from pydantic import BaseModel
from flock.core.orchestrator import Flock


class Idea(BaseModel):
    topic: str


class Pizza(BaseModel):
    name: str
    toppings: list[str]


@pytest.mark.asyncio
async def test_evaluate_parses_agent_json_into_output_type(monkeypatch):
    flock = Flock("test")
    agent = (
        flock.agent("chef")
        .consumes(Idea)
        .publishes(Pizza)
        .with_engines(CairnAgentEngine(worker=_claude_worker()))
    )

    def fake_run(self, argv, extra_env, cwd, timeout):
        # prompt 应包含输入 payload 与输出 schema
        prompt = argv[-1]
        assert "topic" in prompt and "toppings" in prompt
        return (json.dumps({"name": "Margherita", "toppings": ["basil"]}), "", 0)

    monkeypatch.setattr(CairnAgentEngine, "_run", fake_run)

    artifacts = await flock.invoke(agent, Idea(topic="classic"))
    assert len(artifacts) == 1
    assert artifacts[0].payload["name"] == "Margherita"
    assert artifacts[0].payload["toppings"] == ["basil"]


@pytest.mark.asyncio
async def test_evaluate_repairs_invalid_json(monkeypatch):
    flock = Flock("test")
    agent = (
        flock.agent("chef2")
        .consumes(Idea)
        .publishes(Pizza)
        .with_engines(CairnAgentEngine(worker=_claude_worker(), retries=1))
    )

    calls = {"n": 0}

    def fake_run(self, argv, extra_env, cwd, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("sorry, here is your pizza!", "", 0)  # not JSON
        return (json.dumps({"name": "Repaired", "toppings": []}), "", 0)

    monkeypatch.setattr(CairnAgentEngine, "_run", fake_run)
    artifacts = await flock.invoke(agent, Idea(topic="x"))
    assert calls["n"] == 2
    assert artifacts[0].payload["name"] == "Repaired"


@pytest.mark.asyncio
async def test_evaluate_returns_empty_with_error_after_retries(monkeypatch):
    """After all retries exhausted, evaluate() returns EvalResult with no artifacts.

    Tested directly on the engine (not via flock.invoke) because Flock's output
    processor raises a contract-violation ValueError when an engine returns an empty
    EvalResult for a declared output — that's Flock's behaviour, not ours.
    """

    class _MockArtifact:
        payload = {"topic": "x"}

    class _MockInputs:
        artifacts = [_MockArtifact()]
        state: dict = {}

    class _MockSpec:
        model = Pizza

    class _MockOutput:
        spec = _MockSpec()

    class _MockOutputGroup:
        outputs = [_MockOutput()]

    class _MockAgent:
        name = "chef3"
        description = ""

    engine = CairnAgentEngine(worker=_claude_worker(), retries=1)
    monkeypatch.setattr(CairnAgentEngine, "_run",
                        lambda self, *a, **k: ("never json", "", 0))
    result = await engine.evaluate(
        _MockAgent(), None, _MockInputs(), _MockOutputGroup()
    )
    assert result.artifacts == []
    # EvalResult has no `errors` field — error message is stored in logs
    assert any("Pizza" in e for e in result.logs)


from flockos.engine import CairnConfig, cairn_agent


def test_cairn_config_resolves_worker_by_alias():
    cfg = CairnConfig(workers={"claude": _claude_worker()})
    eng = cfg.build_engine("claude", timeout=42)
    assert isinstance(eng, CairnAgentEngine)
    assert eng.worker.type == "claudecode"
    assert eng.timeout == 42


def test_cairn_config_unknown_alias_raises():
    cfg = CairnConfig(workers={})
    with pytest.raises(ValueError):
        cfg.build_engine("nope")


def test_cairn_agent_helper_attaches_engine():
    flock = Flock("test")
    cfg = CairnConfig(workers={"claude": _claude_worker()})
    agent = cairn_agent(flock, cfg, "claude", "chef").consumes(Idea).publishes(Pizza)
    built = agent  # AgentBuilder (PublishBuilder wraps it, ._agent is the real Agent)
    # with_engines stores into self._agent.engines (verified via AgentBuilder.with_engines source)
    assert any(isinstance(e, CairnAgentEngine) for e in built._agent.engines)


# --- direct-evaluate helpers (bypass Flock's output-contract layer) ---


def _mock_eval_objects(model=Pizza, payloads=({"topic": "x"},)):
    arts = [type("A", (), {"payload": dict(p)})() for p in payloads]
    inputs = type("I", (), {"artifacts": arts, "state": {}})()
    spec = type("S", (), {"model": model})()
    out = type("O", (), {"spec": spec})()
    group = type("G", (), {"outputs": [out]})()
    agent = type("Ag", (), {"name": "x", "description": ""})()
    return agent, inputs, group


@pytest.mark.asyncio
async def test_evaluate_nonzero_exit_short_circuits_without_retry(monkeypatch):
    """A nonzero process exit with no JSON is a hard failure: no retry, stderr surfaced."""
    engine = CairnAgentEngine(worker=_claude_worker(), retries=3)
    calls = {"n": 0}

    def fake_run(self, argv, extra_env, cwd, timeout):
        calls["n"] += 1
        return ("", "auth error: bad token", 1)

    monkeypatch.setattr(CairnAgentEngine, "_run", fake_run)
    agent, inputs, group = _mock_eval_objects()
    result = await engine.evaluate(agent, None, inputs, group)
    assert calls["n"] == 1  # did NOT burn all 4 attempts
    assert result.artifacts == []
    assert any("exited 1" in log and "auth error" in log for log in result.logs)


@pytest.mark.asyncio
async def test_evaluate_multi_input_uses_inputs_plural_in_prompt(monkeypatch):
    engine = CairnAgentEngine(worker=_claude_worker())
    seen = {}

    def fake_run(self, argv, extra_env, cwd, timeout):
        seen["prompt"] = argv[-1]
        return (json.dumps({"name": "P", "toppings": []}), "", 0)

    monkeypatch.setattr(CairnAgentEngine, "_run", fake_run)
    agent, inputs, group = _mock_eval_objects(
        payloads=({"topic": "a"}, {"topic": "b"})
    )
    result = await engine.evaluate(agent, None, inputs, group)
    assert result.artifacts and result.artifacts[0].payload["name"] == "P"
    assert "Inputs:" in seen["prompt"]  # plural branch (2 inputs)


def test_from_dispatch_maps_workers_by_name(monkeypatch):
    from types import SimpleNamespace

    import cairn.dispatcher.config as cfgmod

    worker = _claude_worker()
    monkeypatch.setattr(
        cfgmod.DispatchConfig, "load",
        lambda path: SimpleNamespace(workers=[worker]),
    )
    cfg = CairnConfig.from_dispatch("/whatever/dispatch.yaml", default_timeout=99)
    assert set(cfg.workers) == {"flock-claude"}
    assert cfg.default_timeout == 99
    assert cfg.build_engine("flock-claude").worker is worker
