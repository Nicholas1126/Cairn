# FlockOS 移植与 Agent 适配(第一阶段)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Flock 框架移植进 Cairn 仓库组成 FlockOS,新增 `CairnAgentEngine` 让 flock agent 复用 cairn 的宿主机引擎(claude code/codex/opencode/pi),统一单进程 Web(flock 看板 + cairn 界面)并提供一键启动器。

**Architecture:** 单仓库三包工作区 `cairn/` + `flock/` + `flockos/`。`flockos/` 是薄整合层:`engine.py`(Flock `EngineComponent` 子类,进程内复用 cairn worker driver + `LocalManagedProcess`)、`app.py`(以 cairn FastAPI app 为父、把 flock 看板 app 挂到 `/flock`、`/` 重定向到 `/flock/`、cairn 界面挂 `/cairn`)、`cli.py`(`flockos start/stop/status`,单 uvicorn 进程 + PID 文件)。

**Tech Stack:** Python 3.12 / FastAPI / uvicorn / Pydantic v2 / Flock(黑板编排)/ Cairn(worker driver + 宿主机进程)/ uv workspace / Vite(flock 前端预构建为静态)。

设计来源:`docs/superpowers/specs/2026-06-20-flockos-migration-design.md`。

---

## 关键既有接口(实现时据此写代码,勿臆造)

- **Flock 引擎契约**:`flock.components.agent.base.EngineComponent`(Pydantic 模型)。子类实现
  `async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult`。
- **运行时信封**:`flock.utils.runtime` 提供 `EvalInputs`(`.artifacts`,`.first_as(Model)`,`.all_as(Model)`)、
  `EvalResult`(`.from_object(obj, agent=agent)` / `.from_objects(*objs, agent=agent)` / `.empty()`)、`Context`。
- **输出类型解析**:`output_group.outputs` 是 `list[AgentOutput]`;`output_group.outputs[0].spec.model` 是输出的
  Pydantic 模型类,`.model_json_schema()` 得 schema,`output_group.outputs[0].spec.type_name` 是类型名。
- **输入工件**:`inputs.artifacts[i].payload` 是 `dict`。
- **Cairn 驱动**:`cairn.dispatcher.workers.registry.get_driver(type)` → `WorkerDriver`;
  `driver.prepare_session()`(claudecode 返回 uuid,其余 None)、
  `driver.build_execute(worker: WorkerConfig, prompt: str, session: str|None) -> DriverResult(argv=[...])`、
  `driver.extract_response_text(stdout, stderr) -> str`。
  注意 codex/opencode/pi 的 `build_execute` 会读 `worker.env` 取 model/base_url,故必须传真实 `WorkerConfig`。
- **Cairn 配置**:`cairn.dispatcher.config.WorkerConfig`(字段:`name,type,task_types,max_running,priority,env`;
  `validate_env` 要求该 type 的 `WORKER_ENV_KEYS` 齐全)、`DispatchConfig.load(path)`(读 dispatch.yaml,合并 common_env)。
- **Cairn 宿主机进程**:`cairn.dispatcher.runtime.local.process.LocalManagedProcess(command, env, cwd)`;
  `.start()`,`.communicate(timeout) -> ProcessResult(returncode, stdout, stderr, timed_out, cancelled, ...)`,`.kill()`。
- **Cairn Web**:`cairn.server.app.app`(FastAPI,root 路由 `/engines /projects /skills /chat /hints /intents /export /executions`,
  `/static` 静态挂载,`/` 返回 `index.html`)。`index.html` 全用**绝对路径**。
- **Flock 看板组件配方**:见 `flock/orchestrator/server_manager.py::_serve_dashboard` —
  `BaseHTTPService(orchestrator).add_components([...]).configure()` 后 `.app` 即 FastAPI 应用;
  前端默认 API base 为 `/api`(`frontend/src/services/api.ts`:`import.meta.env.VITE_API_BASE_URL || '/api'`)。

---

## File Structure

- `pyproject.toml`(**新建**,仓库根):uv workspace,成员 `cairn` / `flock` / `flockos`。
- `flock/`(**新建**,移植):`flock/src/flock/...` + `flock/pyproject.toml` + `flock/src/flock/frontend/` + `flock/tests/` + `flock/examples/`。
- `flockos/`(**新建**):
  - `flockos/pyproject.toml` — 依赖 `cairn` + `flock-core`。
  - `flockos/src/flockos/__init__.py`
  - `flockos/src/flockos/engine.py` — `CairnAgentEngine` + `CairnConfig` + `cairn_agent` 绑定。
  - `flockos/src/flockos/app.py` — 统一 FastAPI 装配。
  - `flockos/src/flockos/cli.py` — `flockos start/stop/status`。
  - `flockos/tests/test_engine.py` / `test_app.py` / `test_cli.py`。
- `flockos/static/flock/` — flock 前端预构建产物(由 `flockos start` 或手动 build 产生)。

---

## Phase A — 移植与打包

### Task 1: 把 flock 移植进 `flock/` 子目录

**Files:**
- Create: `flock/`(从 `/Users/nicholas/project/ai/flock` 复制,排除 `.git`、生成物)

- [ ] **Step 1: 复制 flock 源(排除 .git / 生成物 / 缓存)**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
rsync -a --exclude '.git' --exclude 'graphify-out' --exclude '.beads' \
  --exclude '__pycache__' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'frontend/dist' --exclude '.pytest_cache' \
  /Users/nicholas/project/ai/flock/ flock/
ls flock/src/flock/core/orchestrator.py flock/pyproject.toml flock/src/flock/frontend/package.json
```
Expected: 三个路径都存在(复制成功)。

- [ ] **Step 2: 校验 flock 可导入(临时装入隔离环境)**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn/flock
uv run --with-editable . python -c "from flock.core.orchestrator import Flock; from flock.components.agent.base import EngineComponent; print('flock import ok')"
```
Expected: 打印 `flock import ok`(确认移植后的包自洽、依赖可解析)。

- [ ] **Step 3: 提交**

```bash
cd /Users/nicholas/project/ai/Cairn
git add flock
git commit -m "chore(flockos): vendor flock framework into flock/ subpackage"
```

> 备注:`git add flock` 会纳入 flock 的 `.gitignore` 等点文件;若仓库根 `.gitignore` 已忽略某些,按需 `git add -f`。不要 `git add -A`。

---

### Task 2: 建立 uv workspace,让 cairn / flock / flockos 同环境共存

**Files:**
- Create: `pyproject.toml`(仓库根)
- Create: `flockos/pyproject.toml`
- Create: `flockos/src/flockos/__init__.py`

- [ ] **Step 1: 写仓库根 workspace `pyproject.toml`**

```toml
[project]
name = "flockos"
version = "0.1.0"
description = "FlockOS — Flock orchestration + Cairn hypergraph verification"
requires-python = ">=3.12"
dependencies = [
    "cairn",
    "flock-core",
    "flockos-integration",
]

[tool.uv.sources]
cairn = { workspace = true }
flock-core = { workspace = true }
flockos-integration = { workspace = true }

[tool.uv.workspace]
members = ["cairn", "flock", "flockos"]

[[tool.uv.index]]
url = "https://mirrors.aliyun.com/pypi/simple"
default = true
```

- [ ] **Step 2: 写 `flockos/pyproject.toml`**

```toml
[project]
name = "flockos-integration"
version = "0.1.0"
description = "FlockOS integration layer: CairnAgentEngine + unified web + launcher"
requires-python = ">=3.12"
dependencies = [
    "cairn",
    "flock-core",
    "click>=8.1",
    "uvicorn[standard]>=0.34",
]

[project.scripts]
flockos = "flockos.cli:main"

[tool.uv.sources]
cairn = { workspace = true }
flock-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.8.9,<0.9.0"]
build-backend = "uv_build"
```

- [ ] **Step 3: 建包入口**

`flockos/src/flockos/__init__.py`:
```python
"""FlockOS integration layer."""

__version__ = "0.1.0"
```

- [ ] **Step 4: 同步并校验三包共存**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
uv sync
uv run python -c "import cairn, flock, flockos; from cairn.server.app import app; from flock.core.orchestrator import Flock; print('workspace ok')"
```
Expected: 打印 `workspace ok`(三包在同一环境可一起导入)。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml flockos/pyproject.toml flockos/src/flockos/__init__.py
git commit -m "chore(flockos): uv workspace for cairn + flock + flockos"
```

---

## Phase B — CairnAgentEngine(核心适配,TDD)

### Task 3: `CairnAgentEngine` 骨架 + 子进程执行 seam

**Files:**
- Create: `flockos/src/flockos/engine.py`
- Test: `flockos/tests/test_engine.py`

- [ ] **Step 1: 写失败测试(argv 经真实 driver 构造 + `_run` seam)**

`flockos/tests/test_engine.py`:
```python
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
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: FAIL(`ModuleNotFoundError: flockos.engine` 或 `CairnAgentEngine` 未定义)。

- [ ] **Step 3: 写最小实现**

`flockos/src/flockos/engine.py`:
```python
"""CairnAgentEngine: run a Cairn host agent (claude/codex/opencode/pi) as a
one-shot, structured-output Flock engine. Mirrors OpenClawEngine but executes
in-process on the host instead of calling an HTTP gateway."""

from __future__ import annotations

from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.runtime.local.process import LocalManagedProcess
from cairn.dispatcher.workers.registry import get_driver
from flock.components.agent.base import EngineComponent
from flock.utils.runtime import EvalResult


class CairnAgentEngine(EngineComponent):
    """Flock engine that delegates evaluation to a Cairn host agent CLI."""

    worker: WorkerConfig
    timeout: int = 600
    retries: int = 1
    cwd: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    def _build_argv(self, prompt: str) -> tuple[list[str], str | None]:
        driver = get_driver(self.worker.type)
        session = driver.prepare_session()
        result = driver.build_execute(self.worker, prompt, session)
        return result.argv, result.session

    def _run(
        self, argv: list[str], extra_env: dict[str, str], cwd: str | None, timeout: int
    ) -> tuple[str, str, int]:
        env = {**self.worker.env, **extra_env}
        proc = LocalManagedProcess(argv, env, cwd)
        proc.start()
        res = proc.communicate(timeout=timeout)
        return res.stdout, res.stderr, res.returncode
```

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: PASS(2 passed)。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/engine.py flockos/tests/test_engine.py
git commit -m "feat(flockos): CairnAgentEngine skeleton (driver argv + host process run)"
```

---

### Task 4: `evaluate()` — 拼 prompt、跑 agent、解析回 Pydantic(单输出)

**Files:**
- Modify: `flockos/src/flockos/engine.py`
- Test: `flockos/tests/test_engine.py`

- [ ] **Step 1: 追加失败测试(用真实 Flock agent + 单输出契约,monkeypatch `_run`)**

在 `flockos/tests/test_engine.py` 末尾追加:
```python
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
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: FAIL(`evaluate` 仍是基类的 `NotImplementedError`)。

- [ ] **Step 3: 实现 `evaluate` + prompt 构造 + 解析**

在 `flockos/src/flockos/engine.py` 顶部 import 增加 `import json`,并给 `CairnAgentEngine` 增加方法:
```python
    def _build_prompt(self, agent: Any, inputs, output_group) -> str:
        decl = output_group.outputs[0]
        schema = decl.spec.model.model_json_schema()
        input_payloads = [dict(a.payload) for a in inputs.artifacts]
        description = (getattr(agent, "description", "") or "").strip()
        lines = [
            "Your ENTIRE response must be a single valid JSON object matching the schema below.",
            "Do not include any text, explanation, markdown fences, or commentary — only the raw JSON object.",
            "The response will be parsed directly by a JSON schema validator.",
        ]
        if description:
            lines.append(f"Task: {description}")
        lines.append(f"Schema: {json.dumps(schema, ensure_ascii=False)}")
        if len(input_payloads) == 1:
            lines.append(f"Input: {json.dumps(input_payloads[0], ensure_ascii=False)}")
        else:
            lines.append(f"Inputs: {json.dumps(input_payloads, ensure_ascii=False)}")
        return "\n".join(lines)

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found in agent output")
        return json.loads(text[start : end + 1])

    async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult:
        if not inputs.artifacts or not output_group.outputs:
            return EvalResult.empty(state=dict(inputs.state))

        model_cls = output_group.outputs[0].spec.model
        driver = get_driver(self.worker.type)
        prompt = self._build_prompt(agent, inputs, output_group)

        argv, session = self._build_argv(prompt)
        stdout, stderr, rc = self._run(argv, {}, self.cwd, self.timeout)
        text = driver.extract_response_text(stdout, stderr)
        data = self._extract_json(text)
        instance = model_cls(**data)
        return EvalResult.from_object(instance, agent=agent)
```

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: PASS(3 passed)。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/engine.py flockos/tests/test_engine.py
git commit -m "feat(flockos): CairnAgentEngine.evaluate parses agent JSON into output type"
```

---

### Task 5: 解析失败时 repair/retry

**Files:**
- Modify: `flockos/src/flockos/engine.py`
- Test: `flockos/tests/test_engine.py`

- [ ] **Step 1: 追加失败测试(第一次返回脏文本,第二次返回合法 JSON)**

在 `flockos/tests/test_engine.py` 末尾追加:
```python
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
    flock = Flock("test")
    agent = (
        flock.agent("chef3")
        .consumes(Idea)
        .publishes(Pizza)
        .with_engines(CairnAgentEngine(worker=_claude_worker(), retries=1))
    )
    monkeypatch.setattr(CairnAgentEngine, "_run",
                        lambda self, *a, **k: ("never json", "", 0))
    artifacts = await flock.invoke(agent, Idea(topic="x"))
    assert artifacts == []
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: FAIL(当前 `evaluate` 不重试,首个脏输出即抛错)。

- [ ] **Step 3: 用重试循环替换 `evaluate` 的执行段**

把 Task 4 中 `evaluate` 内从 `argv, session = ...` 到 `return EvalResult.from_object(...)` 的部分替换为:
```python
        attempts = max(1, self.retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            run_prompt = prompt
            if attempt > 0:
                run_prompt = (
                    prompt
                    + "\n\nYour previous response was not valid JSON. "
                    "Respond with ONLY the raw JSON object, nothing else."
                )
            argv, session = self._build_argv(run_prompt)
            stdout, stderr, rc = self._run(argv, {}, self.cwd, self.timeout)
            text = driver.extract_response_text(stdout, stderr)
            try:
                data = self._extract_json(text)
                instance = model_cls(**data)
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                last_error = exc
                continue
            return EvalResult.from_object(instance, agent=agent)

        return EvalResult.empty(
            state=dict(inputs.state),
            errors=[f"CairnAgentEngine failed to produce valid {model_cls.__name__}: {last_error}"],
        )
```

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: PASS(5 passed)。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/engine.py flockos/tests/test_engine.py
git commit -m "feat(flockos): CairnAgentEngine repair/retry on invalid JSON"
```

---

### Task 6: `CairnConfig` + `cairn_agent()` 便捷构造(镜像 openclaw)

**Files:**
- Modify: `flockos/src/flockos/engine.py`
- Test: `flockos/tests/test_engine.py`

- [ ] **Step 1: 追加失败测试**

在 `flockos/tests/test_engine.py` 末尾追加:
```python
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
    built = agent  # AgentBuilder
    assert any(isinstance(e, CairnAgentEngine) for e in built._engines) \
        or any(isinstance(e, CairnAgentEngine) for e in getattr(built, "engines", []))
```

> 说明:`AgentBuilder` 暂存引擎的属性名以实现为准(实现 Step 3 后此断言应成立;若属性名不同,改断言为对应名)。

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: FAIL(`CairnConfig` / `cairn_agent` 未定义)。

- [ ] **Step 3: 实现 `CairnConfig` 与 `cairn_agent`**

在 `flockos/src/flockos/engine.py` 增加(文件顶部 import `from pathlib import Path`):
```python
from pydantic import BaseModel


class CairnConfig(BaseModel):
    """Alias -> Cairn WorkerConfig mapping for flock.cairn_agent()."""

    workers: dict[str, WorkerConfig] = {}
    default_timeout: int = 600
    default_retries: int = 1

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_dispatch(cls, path: str | Path, **kw) -> "CairnConfig":
        from cairn.dispatcher.config import DispatchConfig

        dispatch = DispatchConfig.load(Path(path).expanduser())
        return cls(workers={w.name: w for w in dispatch.workers}, **kw)

    def build_engine(self, alias: str, *, timeout: int | None = None,
                     retries: int | None = None, cwd: str | None = None) -> CairnAgentEngine:
        if alias not in self.workers:
            raise ValueError(f"unknown cairn worker alias: {alias}")
        return CairnAgentEngine(
            worker=self.workers[alias],
            timeout=timeout if timeout is not None else self.default_timeout,
            retries=retries if retries is not None else self.default_retries,
            cwd=cwd,
        )


def cairn_agent(flock, config: CairnConfig, alias: str, name: str):
    """Mirror of Flock.openclaw_agent: build an AgentBuilder pre-wired with a
    CairnAgentEngine for the given worker alias."""
    builder = flock.agent(name)
    builder.with_engines(config.build_engine(alias))
    return builder
```

- [ ] **Step 4: 运行,确认通过(若引擎属性名断言失败,按实际名修正测试再跑)**

Run: `uv run pytest flockos/tests/test_engine.py -q`
Expected: PASS(8 passed)。

- [ ] **Step 5: 导出符号 + 提交**

`flockos/src/flockos/__init__.py` 追加:
```python
from flockos.engine import CairnAgentEngine, CairnConfig, cairn_agent

__all__ = ["CairnAgentEngine", "CairnConfig", "cairn_agent"]
```

```bash
git add flockos/src/flockos/engine.py flockos/src/flockos/__init__.py flockos/tests/test_engine.py
git commit -m "feat(flockos): CairnConfig + cairn_agent helper (mirror openclaw_agent)"
```

---

## Phase C — 统一 Web

### Task 7: 构建 flock 看板 app(组件配方,无 npm/无 launcher)

**Files:**
- Create: `flockos/src/flockos/flock_app.py`
- Test: `flockos/tests/test_app.py`

实现要点:复刻 `server_manager.py::_serve_dashboard` 的组件装配,但**不**调用 `DashboardLauncher`(不跑 npm)、**不**调用 `service.run_async`(不起 uvicorn);只 `service.configure()` 后取 `service.app`。静态目录指向我们预构建的 `flockos/static/flock`。

- [ ] **Step 1: 写失败测试(能拿到一个含 `/api` 健康路由的 FastAPI app)**

`flockos/tests/test_app.py`:
```python
from __future__ import annotations

from fastapi.testclient import TestClient
from flock.core.orchestrator import Flock
from flockos.flock_app import build_flock_app


def test_build_flock_app_exposes_api():
    flock = Flock("flockos")
    app = build_flock_app(flock)
    client = TestClient(app)
    # flock 健康/指标端点在 /api 下(HealthAndMetricsComponent)
    resp = client.get("/api/health")
    assert resp.status_code in (200, 404)  # 路由存在即可(具体路径以组件实现为准)
```

> 实现时请打开 `flock/components/server/health/health_component.py` 确认健康路由的确切路径,并把断言改成精确路径(去掉 404 容忍)。这是计划要求的精确化步骤,不是占位。

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_app.py -q`
Expected: FAIL(`flockos.flock_app` 不存在)。

- [ ] **Step 3: 实现 `build_flock_app`**

`flockos/src/flockos/flock_app.py`:
```python
"""Build flock's dashboard FastAPI app WITHOUT starting uvicorn or npm.

Mirrors flock.orchestrator.server_manager._serve_dashboard's component wiring,
serving the prebuilt frontend from flockos/static/flock.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from flock.api.base_service import BaseHTTPService
from flock.api.collector import DashboardEventCollector
from flock.api.graph_builder import GraphAssembler
from flock.api.websocket import WebSocketManager
from flock.components.server import (
    AgentsServerComponent,
    AgentsServerComponentConfig,
    ArtifactComponentConfig,
    ArtifactsComponent,
    ControlRoutesComponent,
    ControlRoutesComponentConfig,
    CORSComponent,
    CORSComponentConfig,
    HealthAndMetricsComponent,
    StaticFilesComponentConfig,
    StaticFilesServerComponent,
    ThemesComponent,
    ThemesComponentConfig,
    WebSocketComponentConfig,
    WebSocketServerComponent,
)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "flock"


def build_flock_app(orchestrator, *, static_dir: Path | None = None) -> FastAPI:
    static_dir = static_dir or STATIC_DIR

    websocket_manager = WebSocketManager(enable_heartbeat=False, heartbeat_interval="120")
    event_collector = DashboardEventCollector(store=orchestrator.store)
    event_collector.set_websocket_manager(manager=websocket_manager)
    orchestrator._dashboard_collector = event_collector
    orchestrator._websocket_manager = websocket_manager
    orchestrator._event_emitter.set_websocket_manager(websocket_manager)

    service = BaseHTTPService(orchestrator=orchestrator, version="0.5.0").add_components(
        [
            HealthAndMetricsComponent(name="health_internal"),
            AgentsServerComponent(
                name="agents_internal",
                config=AgentsServerComponentConfig(enabled=True, prefix="/api/v1/", tags=["Agents"]),
            ),
            ControlRoutesComponent(
                name="api_internal",
                config=ControlRoutesComponentConfig(enabled=True, prefix="/api/", tags=["Control"]),
                graph_assembler=GraphAssembler(
                    store=orchestrator.store, collector=event_collector, orchestrator=orchestrator
                ),
            ),
            ArtifactsComponent(
                name="artifacts_internal",
                config=ArtifactComponentConfig(enabled=True, prefix="/api/v1/", tags=["Artifacts"]),
            ),
            WebSocketServerComponent(
                name="websocket_internal",
                config=WebSocketComponentConfig(
                    enabled=True, enable_heartbeat=False, hearbeat_interval="120",
                    prefix="/", tags=["WebSocket"],
                ),
            ),
            CORSComponent(
                name="cors_internal",
                config=CORSComponentConfig(
                    enabled=True, prefix="", tags=["CORS"], allow_origins=["*"],
                    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                ),
            ),
            ThemesComponent(
                name="themes_internal", themes_dir=None,
                config=ThemesComponentConfig(enabled=True, prefix="/api/", tags=["Themes"]),
            ),
            StaticFilesServerComponent(
                name="static_files_internal",
                config=StaticFilesComponentConfig(
                    enabled=True, prefix="", tags=["Static"], static_files_path=static_dir,
                ),
            ),
        ]
    )
    service.configure()
    return service.app
```

> 实现注意:`flock.components.server` 的导出名以该包 `__init__.py` 为准;若某组件名/配置字段不同(如 `hearbeat_interval` 拼写),按真实源改。`StaticFilesServerComponent` 在 `static_dir` 不存在时可能报错——若如此,实现时先 `static_dir.mkdir(parents=True, exist_ok=True)` 并放一个占位 `index.html`(Task 9 会用真构建覆盖)。

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_app.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/flock_app.py flockos/tests/test_app.py
git commit -m "feat(flockos): build flock dashboard app without npm/uvicorn"
```

---

### Task 8: 统一 app — cairn 为父,flock 挂 `/flock`,`/` 重定向,cairn 界面挂 `/cairn`

**Files:**
- Create: `flockos/src/flockos/app.py`
- Modify: `flockos/tests/test_app.py`

设计:父应用 = cairn 的 FastAPI `app`(其 root 路由与 `/static` 不动)。在其上:
- `GET /` → 307 重定向到 `/flock/`。
- `GET /cairn` → 返回 cairn 的 `index.html`(其 JS 用绝对 `/engines`、`/static`,在父 root 下仍可用)。
- `app.mount("/flock", flock_app)` — flock 看板与 API 全在 `/flock` 下自洽(前端构建时设 base=`/flock/`、API base=`/flock/api`、ws=`/flock/ws`,见 Task 9)。

- [ ] **Step 1: 追加失败测试**

在 `flockos/tests/test_app.py` 追加:
```python
import cairn.server.db as cairn_db
from flockos.app import build_app


def test_unified_app_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    cairn_db._db_path = None
    cairn_db.configure(tmp_path / "cairn.db")
    app = build_app()
    client = TestClient(app, follow_redirects=False)

    # 根重定向到 flock 主页
    r = client.get("/")
    assert r.status_code in (302, 307)
    assert r.headers["location"].rstrip("/").endswith("/flock")

    # cairn 现有 root 路由仍在
    assert client.get("/engines").status_code == 200

    # cairn 页面在 /cairn
    assert client.get("/cairn").status_code == 200

    # flock 看板挂在 /flock
    assert client.get("/flock/").status_code in (200, 404)  # 取决于是否已 build 前端
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_app.py -q`
Expected: FAIL(`flockos.app` 不存在)。

- [ ] **Step 3: 实现 `build_app`**

`flockos/src/flockos/app.py`:
```python
"""Unified FlockOS FastAPI app: cairn app as parent, flock dashboard mounted at /flock."""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from flock.core.orchestrator import Flock

from cairn.server.app import app as cairn_app, STATIC_DIR as CAIRN_STATIC
from flockos.flock_app import build_flock_app


def build_app(orchestrator: Flock | None = None):
    flock = orchestrator or Flock("flockos")
    flock_app = build_flock_app(flock)

    @cairn_app.get("/", include_in_schema=False)
    def _home():
        return RedirectResponse(url="/flock/")

    @cairn_app.get("/cairn", include_in_schema=False)
    def _cairn_home():
        return FileResponse(Path(CAIRN_STATIC) / "index.html")

    cairn_app.mount("/flock", flock_app)
    return cairn_app
```

> 注意:cairn `app.py` 已定义 `GET /`。FastAPI 后注册的同路径路由不会覆盖先注册的。实现时需让重定向生效——两种做法择一并在实现中落实:(a) 在 `cairn/server/app.py` 把 `index()` 改为可被 FlockOS 覆盖(例如仅当未挂 flock 时返回 index);或(b) 在 `build_app` 里直接改写/移除 cairn app 路由表中 `path=="/"` 的那条 route 后再注册新 `/`。推荐 (b),无需改 cairn 源:遍历 `cairn_app.router.routes` 删除 `getattr(r,'path',None)=="/"` 的项,再注册 `_home`。测试已固定行为(根必须 307 到 `/flock`),按此实现至测试通过。

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_app.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/app.py flockos/tests/test_app.py
git commit -m "feat(flockos): unified app (cairn root + flock at /flock + /cairn page + / redirect)"
```

---

### Task 9: flock 前端预构建(base/API/ws)+ 跳转入口 + 主题贴近 cairn

**Files:**
- Modify: `flock/src/flock/frontend/vite.config.ts`(设 `base: '/flock/'`)
- Modify: `flock/src/flock/frontend/src/services/api.ts`(API base 默认 `/flock/api`)
- Modify: flock 前端 websocket URL 构造处(指向 `/flock/ws`,具体文件见下)
- Modify: flock 前端顶栏组件(加"进入 Cairn"链接 → `/cairn`)
- Create: `flockos/static/flock/`(构建产物)

- [ ] **Step 1: 设 Vite base**

`flock/src/flock/frontend/vite.config.ts` 在 `defineConfig({...})` 顶层加入(若已有 base 则改值):
```ts
  base: '/flock/',
```

- [ ] **Step 2: API base 默认指向 `/flock/api`**

`flock/src/flock/frontend/src/services/api.ts` 第 12 行:
```ts
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/flock/api';
```

- [ ] **Step 3: WebSocket URL 指向 `/flock/ws`**

打开 `flock/src/flock/frontend/src/services/websocket.ts`,定位构造 `url`(传给 `new WebSocketClient(url)`/`new WebSocket(this.url)`)的位置,把其路径部分改为 `/flock/ws`(保留 `ws://`/`wss://` + host 推导逻辑)。例如把默认路径常量改为:
```ts
const WS_PATH = import.meta.env.VITE_WS_PATH || '/flock/ws';
```
并在 url 拼接处使用 `WS_PATH`。

> 实现时确认后端 ws 实际挂载路径:`build_flock_app` 里 `WebSocketServerComponent` prefix=`/`,挂到父 `/flock` 后即 `/flock/<ws-route>`。打开 `flock/components/server/websocket/websocket_component.py` 看具体子路径(如 `/ws`),令前端 `WS_PATH` 与 `/flock` + 子路径一致。

- [ ] **Step 4: 顶栏加"进入 Cairn"入口 + 轻量主题贴近 cairn**

在 flock 前端顶栏/头部组件(在 `flock/src/flock/frontend/src/` 下搜索渲染标题栏的组件,如 `App.tsx` 或 `components/` 中的 header)加入一个链接:
```tsx
<a href="/cairn" className="flockos-cairn-link">Cairn 控制台 →</a>
```
主题:为"轻微改动界面",在 `flock/src/flock/frontend/src/styles/variables.css` 覆盖主色为 cairn 的 slate 浅色系(与 cairn `index.html` 的 `slate-*` 一致),例如把主背景/主文字/强调色变量改为浅色 slate 值(具体变量名以该文件为准,改 3-5 个根变量即可)。

- [ ] **Step 5: 构建到 `flockos/static/flock`**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn/flock/frontend
npm install
npm run build
rm -rf ../../../../flockos/static/flock
mkdir -p ../../../../flockos/static/flock
cp -R dist/* ../../../../flockos/static/flock/
ls ../../../../flockos/static/flock/index.html
```
Expected: `index.html` 存在(产物就位)。

- [ ] **Step 6: 端到端校验统一 app 提供 flock 主页**

Run: `uv run pytest flockos/tests/test_app.py -q`(把 Task 8 测试里 `/flock/` 的断言收紧为 `== 200`)
Expected: PASS,且 `GET /flock/` 返回 200(看板 index)。

- [ ] **Step 7: 提交**

```bash
git add flock/src/flock/frontend/vite.config.ts flock/src/flock/frontend/src/services/api.ts flock/src/flock/frontend/src/services/websocket.ts flock/src/flock/frontend/src/styles/variables.css flock/src/flock/frontend/src flockos/static/flock
git commit -m "feat(flockos): flock frontend under /flock (base/api/ws) + Cairn entry + cairn-like theme"
```

> 备注:`flockos/static/flock` 为构建产物;若团队偏好不提交产物,可改为 `.gitignore` 并依赖 Task 11 的按需构建。本计划默认提交产物以保证一键启动稳健。

---

## Phase D — 一键启动器

### Task 10: `flockos start/stop/status`(单 uvicorn 进程 + PID 文件)

**Files:**
- Create: `flockos/src/flockos/cli.py`
- Test: `flockos/tests/test_cli.py`

- [ ] **Step 1: 写失败测试(PID 生命周期,mock uvicorn)**

`flockos/tests/test_cli.py`:
```python
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
    res = CliRunner().invoke(cli.main, ["start", "--foreground", "--port", "8123"])
    assert res.exit_code == 0
    assert called["port"] == 8123
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_cli.py -q`
Expected: FAIL(`flockos.cli` 不存在)。

- [ ] **Step 3: 实现 CLI**

`flockos/src/flockos/cli.py`:
```python
"""FlockOS one-shot launcher: start/stop/status a single uvicorn serving the unified app."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import uvicorn

from flockos.app import build_app

RUN_DIR = Path(os.environ.get("FLOCKOS_HOME", Path.home() / ".cairn")) / "run"
PID_FILE = RUN_DIR / "flockos.pid"
LOG_FILE = RUN_DIR / "flockos.log"


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@click.group()
def main():
    """FlockOS launcher."""


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--foreground", is_flag=True, help="Run in the foreground (do not daemonize).")
def start(host: str, port: int, foreground: bool):
    """Start FlockOS (unified flock + cairn web)."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_pid()
    if existing and _alive(existing):
        click.echo(f"FlockOS already running (pid {existing})")
        return

    if foreground:
        uvicorn.run(build_app(), host=host, port=port)
        return

    log = open(LOG_FILE, "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "flockos.app:build_app", "--factory",
         "--host", host, "--port", str(port)],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    click.echo(f"FlockOS started (pid {proc.pid}) on http://{host}:{port}  logs: {LOG_FILE}")


@main.command()
def stop():
    """Stop FlockOS."""
    pid = _read_pid()
    if pid is None:
        click.echo("FlockOS not running (no pid file)")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        click.echo("Removing stale pid file")
        PID_FILE.unlink(missing_ok=True)
        return
    for _ in range(10):
        if not _alive(pid):
            break
        time.sleep(0.3)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    click.echo(f"FlockOS stopped (pid {pid})")


@main.command()
def status():
    """Show FlockOS status."""
    pid = _read_pid()
    if pid and _alive(pid):
        click.echo(f"FlockOS running (pid {pid})")
    else:
        click.echo("FlockOS not running")
```

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_cli.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/cli.py flockos/tests/test_cli.py
git commit -m "feat(flockos): flockos start/stop/status launcher (single uvicorn + pid file)"
```

---

### Task 11: `start` 缺前端产物时自动构建

**Files:**
- Modify: `flockos/src/flockos/cli.py`
- Test: `flockos/tests/test_cli.py`

- [ ] **Step 1: 追加失败测试**

在 `flockos/tests/test_cli.py` 追加:
```python
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
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run pytest flockos/tests/test_cli.py::test_start_builds_frontend_if_missing -q`
Expected: FAIL(`_ensure_frontend`/`FLOCK_STATIC` 未定义)。

- [ ] **Step 3: 实现 `_ensure_frontend` 并在 `start` 开头调用**

在 `flockos/src/flockos/cli.py` 增加常量与函数,并在 `start` 函数体首行(`PID_FILE.parent.mkdir` 之前)调用 `_ensure_frontend()`:
```python
FLOCK_STATIC = Path(__file__).resolve().parents[2] / "static" / "flock"
FLOCK_FRONTEND = Path(__file__).resolve().parents[3] / "flock" / "src" / "flock" / "frontend"


def _ensure_frontend() -> None:
    if (FLOCK_STATIC / "index.html").exists():
        return
    click.echo("Building flock frontend (first run)...")
    subprocess.run(["npm", "install"], cwd=FLOCK_FRONTEND, check=True)
    subprocess.run(["npm", "run", "build"], cwd=FLOCK_FRONTEND, check=True)
    FLOCK_STATIC.mkdir(parents=True, exist_ok=True)
    dist = FLOCK_FRONTEND / "dist"
    for item in dist.iterdir():
        dest = FLOCK_STATIC / item.name
        if item.is_dir():
            import shutil
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            dest.write_bytes(item.read_bytes())
```

- [ ] **Step 4: 运行,确认通过**

Run: `uv run pytest flockos/tests/test_cli.py -q`
Expected: PASS(全部 cli 测试)。

- [ ] **Step 5: 提交**

```bash
git add flockos/src/flockos/cli.py flockos/tests/test_cli.py
git commit -m "feat(flockos): start builds flock frontend when static is missing"
```

---

## Phase E — 收尾

### Task 12: 全量回归 + flock 自有测试冒烟

**Files:**(无新增)

- [ ] **Step 1: 跑 flockos + cairn 测试**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
uv run pytest flockos/tests -q
uv run pytest cairn/tests -q
```
Expected: 均 PASS(cairn 测试不受影响,证明 cairn 未被破坏)。

- [ ] **Step 2: flock 自有测试冒烟(快速子集)**

Run:
```bash
uv run pytest flock/tests/core -q
```
Expected: PASS(或与移植前一致;若有环境型失败,记录但不阻断——移植未改 flock 源)。

- [ ] **Step 3: 手动校验一键启动(人工)**

Run:
```bash
uv run flockos start --foreground --port 8000
```
然后浏览器:`http://127.0.0.1:8000/` 应跳转 `/flock/`(看板);顶栏"进入 Cairn"→ `/cairn`(cairn 界面,`#/engines` 等照常工作)。Ctrl-C 停止。
Expected: 两个界面都可用,cairn 操作方式不变。

- [ ] **Step 4: 提交(若有快照/微调)**

```bash
git add -u
git commit -m "test(flockos): regression sweep (flockos + cairn green; flock smoke)"
```

> 仅当确有改动时提交;不要 `git add -A`/`git add .`。

---

### Task 13: 文档

**Files:**
- Create: `docs/flockos.md`
- Modify: `README.md`(加 FlockOS 启动一节,可选)

- [ ] **Step 1: 写 `docs/flockos.md`**

内容覆盖:架构(两子系统 + 整合层)、`uv sync`、`flockos start/stop/status`、`CairnAgentEngine` / `cairn_agent` 用法示例(指向 `flockos.engine`)、路由地图(`/`→`/flock/`、`/cairn`、`/flock/api`、cairn root 路由)、第二阶段(需求 5)预告。

- [ ] **Step 2: 提交**

```bash
git add docs/flockos.md README.md
git commit -m "docs(flockos): architecture, launcher, CairnAgentEngine usage"
```

---

## Self-Review(写计划后自检结论)

**Spec 覆盖:**
- 需求 1(移植 + 两子系统 + 整合层):Task 1-2 ✓
- 需求 2(flock 主页 + cairn 入口 + cairn 不变):Task 7-9 ✓(`/`→`/flock/`、`/cairn`、cairn root 路由保留)
- 需求 3(agent 适配,复用本地引擎,仅宿主机):Task 3-6 ✓(`CairnAgentEngine` 用 `LocalManagedProcess`,不碰 docker)
- 需求 4(一键启停):Task 10-11 ✓
- 需求 5(HTTP 工件验证对接):**明确不在本计划**(spec 第二阶段)✓

**已知风险/实现期需精确化的点(已在对应 Task 内标注,非占位):**
- Task 7:`flock.components.server` 的精确导出名/配置字段(如 `hearbeat_interval` 拼写、健康路由确切路径)——实现时读源校正断言。
- Task 8:覆盖 cairn 已注册的 `GET /`——用遍历删除 root route 的方式实现(无需改 cairn 源)。
- Task 9:flock ws 实际子路径——读 `websocket_component.py` 对齐 `VITE_WS_PATH`。
- 子应用(`mount("/flock", flock_app)`)的 lifespan:`BaseHTTPService` 用 lifespan 跑组件 `on_startup`;若挂载后子 app lifespan 不触发,改为在 `build_app` 里手动驱动 flock 组件启动(或父 app lifespan 内调用)。Task 12 Step 3 的人工校验会暴露此问题。
- 依赖版本:workspace 解析以 flock 的钉版为准(fastapi==0.121.0 满足 cairn 的 `>=0.115`;pydantic v2 双方兼容)。`uv sync`(Task 2 Step 4)是第一道关。

**类型一致性:** `CairnAgentEngine` 字段 `worker/timeout/retries/cwd`、方法 `_build_argv/_run/_build_prompt/_extract_json/evaluate`、`CairnConfig.build_engine`、`cairn_agent(flock,config,alias,name)` 在各 Task 中签名一致 ✓。
