# Engine Chat Mode (C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cairn-styled chat page to talk to a configured worker's engine on the host (request/response, multi-turn via the driver's session), to verify connectivity and debug prompts/commands/output.

**Architecture:** Reuse the existing `WorkerDriver` (session primitives), `LocalRuntime`/`LocalManagedProcess`, `resolve.probe_engine`, and `execlog` redaction. A new server-side `chat.py` loads the `DispatchConfig` (path from `CAIRN_DISPATCH_CONFIG`, default `dispatch.yaml`), lists workers (no creds), and runs one turn on the host. Endpoints `GET /chat/workers` + `POST /chat/turn`; a `#/chat` SPA page. No SDK, no token streaming, no persistence in v1.

**Tech Stack:** Python 3, FastAPI, pytest, AlpineJS (`index.html`).

**Spec:** `docs/superpowers/specs/2026-06-20-engine-chat-mode-design.md`

**Conventions:** tests via `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest`. Commit after each task. Prefer `graphify query` before grepping when exploring. Branch: `feat/local-engine-worker`.

**Reused signatures (already in the codebase):**
- `cairn.dispatcher.config.DispatchConfig.load(path: Path) -> DispatchConfig`; `config.workers: list[WorkerConfig]`; `WorkerConfig.name/type/env`.
- `cairn.dispatcher.workers.registry.get_driver(type) -> WorkerDriver`.
- `WorkerDriver.prepare_session() -> str|None`; `build_execute(worker, prompt, session) -> DriverResult` (has `.argv`, `.session`); `extract_session(session, stdout, stderr) -> str|None`; `extract_response_text(stdout, stderr) -> str`.
- `cairn.dispatcher.runtime.local.runtime.LocalRuntime(*, workspaces_root, completed_action, agents_source)`; `.ensure_running(key) -> str`; `.build_exec_process(key, env, argv) -> LocalManagedProcess` (has `.start()`, `.communicate(timeout) -> ProcessResult`).
- `cairn.dispatcher.runtime.local.resolve.probe_engine(type) -> {launchable, path, version, source}`.
- `cairn.execlog.redact_command/redact_text/truncate_head_tail`; `cairn.dispatcher.tasks.common.model_env_key(worker) -> str`.
- `cairn.server.db.cairn_home() -> Path`.

---

### Task 1: Chat models

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Test: `cairn/tests/test_chat_models.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_chat_models.py
from cairn.server.models import ChatWorker, ChatTurnRequest, ChatTurnResult


def test_chat_worker():
    w = ChatWorker(name="opencode_x", type="opencode", model="deepseek-v4-pro")
    assert w.model == "deepseek-v4-pro"


def test_chat_turn_request_defaults_session_none():
    r = ChatTurnRequest(worker="w", message="hi")
    assert r.session is None


def test_chat_turn_result_minimal():
    r = ChatTurnResult(reply="pong", command=["opencode", "run"], prompt="ping",
                       stdout="...", outcome="success")
    assert r.session is None and r.exit_code is None and r.duration_ms == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_models.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement in models.py**

Add near the Engine* models:
```python
class ChatWorker(BaseModel):
    name: str
    type: str
    model: str | None = None


class ChatTurnRequest(BaseModel):
    worker: str
    message: str
    session: str | None = None


class ChatTurnResult(BaseModel):
    reply: str
    session: str | None = None
    command: list[str]
    prompt: str
    stdout: str
    exit_code: int | None = None
    outcome: str
    duration_ms: int = 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_models.py -v` → PASS (3). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/models.py cairn/tests/test_chat_models.py
git commit -m "feat: chat models (ChatWorker/ChatTurnRequest/ChatTurnResult)"
```

---

### Task 2: Chat service (`server/chat.py`)

Pure orchestration `_run_turn(driver, runtime, worker, message, session, timeout)` (unit-testable with fakes) + thin wrappers `load_dispatch_config`, `list_workers`, `run_turn`.

**Files:**
- Create: `cairn/src/cairn/server/chat.py`
- Test: `cairn/tests/test_chat_service.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_chat_service.py
from __future__ import annotations

from cairn.server import chat
from cairn.dispatcher.runtime.process import ProcessResult


class _FakeDriver:
    def prepare_session(self): return None
    def build_execute(self, worker, prompt, session):
        class R:  # mimics DriverResult
            argv = ["opencode", "run", "--", prompt] + (["-s", session] if session else [])
            session = None
        R.session = session
        return R
    def extract_session(self, session, stdout, stderr): return session or "ses_new"
    def extract_response_text(self, stdout, stderr): return "the reply"


class _FakeProc:
    def __init__(self, argv): self.argv = argv
    def start(self): pass
    def communicate(self, timeout):
        return ProcessResult(returncode=0, stdout="raw out sk-SECRET123 done", stderr="")


class _FakeRuntime:
    def ensure_running(self, key): return "/tmp/ws"
    def build_exec_process(self, key, env, argv): return _FakeProc(argv)


class _Worker:
    def __init__(self): self.name = "opencode_x"; self.type = "opencode"; self.env = {"OPENCODE_API_KEY": "sk-SECRET123"}


def test_run_turn_new_session(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": True, "path": "/x", "version": "1", "source": "path"})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "ping", None, timeout=5)
    assert res.reply == "the reply"
    assert res.session == "ses_new"
    assert res.outcome == "success"
    assert "sk-SECRET123" not in "".join(res.command)   # command redacted
    assert "sk-SECRET123" not in res.stdout              # stdout redacted


def test_run_turn_resumes_session(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": True, "path": "/x", "version": "1", "source": "path"})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "again", "ses_keep", timeout=5)
    assert res.session == "ses_keep"
    assert "-s" in res.command and "ses_keep" in res.command  # resume flag threaded


def test_run_turn_guard_when_not_launchable(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": False, "path": None, "version": None, "source": None})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "ping", None, timeout=5)
    assert res.outcome == "failed"
    assert "opencode" in res.reply.lower() or "not" in res.reply.lower()


def test_list_workers_excludes_credentials(monkeypatch, tmp_path):
    cfg = tmp_path / "dispatch.yaml"
    cfg.write_text("""\
server: http://localhost:8000
runtime: {max_workers: 1, max_running_projects: 1, max_project_workers: 1, interval: 5, healthcheck_timeout: 30, prompt_group: default}
tasks: {bootstrap: {timeout: 10, conclude_timeout: 10}, reason: {timeout: 10}, explore: {timeout: 10, conclude_timeout: 10}}
container: {image: img, network_mode: bridge, completed_action: remove}
workers:
  - {name: oc, type: opencode, task_types: [explore], max_running: 1, priority: 0, env: {OPENCODE_MODEL: deepseek, OPENCODE_BASE_URL: http://x, OPENCODE_API_KEY: sk-SECRET}}
""", encoding="utf-8")
    monkeypatch.setenv("CAIRN_DISPATCH_CONFIG", str(cfg))
    workers = chat.list_workers()
    assert len(workers) == 1
    assert workers[0].name == "oc" and workers[0].type == "opencode" and workers[0].model == "deepseek"
    # serialize and confirm no secret leaks
    import json
    assert "sk-SECRET" not in json.dumps([w.model_dump() for w in workers])
```

If the minimal `dispatch.yaml` above fails validation (the exact `tasks`/`runtime` required fields differ), read `cairn/tests/conftest.py` / an existing config test for the correct minimal shape and adapt the YAML — do not weaken the no-secret assertion.

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_service.py -v` → FAIL (no module).

- [ ] **Step 3: Implement `cairn/src/cairn/server/chat.py`**

```python
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import HTTPException

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.runtime.local import resolve
from cairn.dispatcher.runtime.local.runtime import LocalRuntime
from cairn.dispatcher.tasks.common import model_env_key
from cairn.dispatcher.workers.registry import get_driver
from cairn.execlog import redact_command, redact_text, truncate_head_tail
from cairn.server.db import cairn_home
from cairn.server.models import ChatTurnResult, ChatWorker

CHAT_TIMEOUT_SECONDS = 300
STDOUT_CAP = 64 * 1024


def dispatch_config_path() -> Path:
    return Path(os.environ.get("CAIRN_DISPATCH_CONFIG", "dispatch.yaml"))


def load_dispatch_config() -> DispatchConfig:
    path = dispatch_config_path()
    if not path.exists():
        raise HTTPException(400, f"dispatch config not found at {path}; set CAIRN_DISPATCH_CONFIG")
    return DispatchConfig.load(path)


def list_workers() -> list[ChatWorker]:
    config = load_dispatch_config()
    out: list[ChatWorker] = []
    for w in config.workers:
        out.append(ChatWorker(name=w.name, type=w.type, model=w.env.get(model_env_key(w))))
    return out


def _chat_runtime() -> LocalRuntime:
    agents_source = Path(__file__).resolve().parents[3] / "container"
    return LocalRuntime(
        workspaces_root=str(cairn_home() / "chats"),
        completed_action="stop",  # keep chat workspaces
        agents_source=str(agents_source) if agents_source.exists() else None,
    )


def _find_worker(config: DispatchConfig, name: str) -> WorkerConfig:
    for w in config.workers:
        if w.name == name:
            return w
    raise HTTPException(404, f"worker not found: {name}")


def run_turn(worker_name: str, message: str, session: str | None) -> ChatTurnResult:
    config = load_dispatch_config()
    worker = _find_worker(config, worker_name)
    driver = get_driver(worker.type)
    runtime = _chat_runtime()
    return _run_turn(driver, runtime, worker, message, session, timeout=CHAT_TIMEOUT_SECONDS)


def _run_turn(driver, runtime, worker, message: str, session: str | None, *, timeout: int) -> ChatTurnResult:
    probe = resolve.probe_engine(worker.type)
    if not probe["launchable"]:
        return ChatTurnResult(
            reply=f"Engine '{resolve.BINARY.get(worker.type, worker.type)}' is not launchable on this host "
                  f"(not installed / not on PATH). Configure it on the Engines page.",
            session=session, command=[], prompt=message, stdout="", exit_code=None,
            outcome="failed", duration_ms=0,
        )
    session_in = session or driver.prepare_session()
    result = driver.build_execute(worker, message, session_in)
    runtime.ensure_running(worker.name)
    proc = runtime.build_exec_process(worker.name, dict(worker.env), result.argv)
    proc.start()
    started = time.perf_counter()
    res = proc.communicate(timeout=timeout)
    duration_ms = int((time.perf_counter() - started) * 1000)
    session_out = driver.extract_session(result.session, res.stdout, res.stderr)
    reply = driver.extract_response_text(res.stdout, res.stderr)
    if res.timed_out:
        outcome = "timeout"
    elif res.returncode == 0:
        outcome = "success"
    else:
        outcome = "failed"
    out = truncate_head_tail(redact_text(res.stdout or ""), STDOUT_CAP)
    return ChatTurnResult(
        reply=reply, session=session_out, command=redact_command(result.argv),
        prompt=message, stdout=out.text, exit_code=res.returncode,
        outcome=outcome, duration_ms=duration_ms,
    )
```
Note: `_chat_runtime` uses `parents[3]` to reach the repo root containing `container/` from `cairn/src/cairn/server/chat.py` — VERIFY the index empirically during implementation (print `Path(__file__).resolve().parents[i]`) and use the index whose value contains `container/`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_service.py -v` → PASS (4). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/chat.py cairn/tests/test_chat_service.py
git commit -m "feat: chat service (list_workers + run_turn on host)"
```

---

### Task 3: Chat router + app wiring

**Files:**
- Create: `cairn/src/cairn/server/routers/chat.py`
- Modify: `cairn/src/cairn/server/app.py`
- Test: `cairn/tests/test_chat_router.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_chat_router.py
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db, chat
from cairn.server.app import app
from cairn.server.models import ChatTurnResult, ChatWorker


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_get_workers(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(chat, "list_workers", lambda: [ChatWorker(name="oc", type="opencode", model="m")])
    r = c.get("/chat/workers")
    assert r.status_code == 200
    assert r.json() == [{"name": "oc", "type": "opencode", "model": "m"}]


def test_post_turn(tmp_path, monkeypatch):
    c = _client(tmp_path)
    def fake_run_turn(worker, message, session):
        return ChatTurnResult(reply="pong", session="ses_1", command=["opencode", "run"],
                              prompt=message, stdout="raw", exit_code=0, outcome="success", duration_ms=12)
    monkeypatch.setattr(chat, "run_turn", fake_run_turn)
    r = c.post("/chat/turn", json={"worker": "oc", "message": "ping"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "pong" and body["session"] == "ses_1" and body["outcome"] == "success"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_router.py -v` → FAIL (404s).

- [ ] **Step 3: Implement router + wiring**

```python
# cairn/src/cairn/server/routers/chat.py
from __future__ import annotations

from fastapi import APIRouter

from cairn.server import chat
from cairn.server.models import ChatTurnRequest, ChatTurnResult, ChatWorker

router = APIRouter(tags=["chat"])


@router.get("/chat/workers", response_model=list[ChatWorker])
def get_chat_workers():
    return chat.list_workers()


@router.post("/chat/turn", response_model=ChatTurnResult)
def post_chat_turn(body: ChatTurnRequest):
    return chat.run_turn(body.worker, body.message, body.session)
```
In `app.py`, add `chat` to the routers import and `app.include_router(chat.router)`:
```python
from cairn.server.routers import chat, engines, executions, export, hints, intents, projects, settings
```
```python
app.include_router(chat.router)
```
(If sub-project B's `engines` router isn't merged yet, omit `engines` from this line — add only `chat`. Keep the import list valid for whatever routers exist.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_chat_router.py -v` → PASS (2). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/routers/chat.py cairn/src/cairn/server/app.py cairn/tests/test_chat_router.py
git commit -m "feat: /chat endpoints (workers list + turn)"
```

---

### Task 4: Chat page in the SPA (route + nav + conversation + debug)

No JS test runner — verify by serving. Work in `cairn/src/cairn/server/static/index.html`.

- [ ] **Step 1: Orient**

Run:
- `grep -n "handleRoute\|view === '\|view = '\|#/projects\|#/engines" cairn/src/cairn/server/static/index.html`
- Read `handleRoute()` and the header/nav area (where the Engines link from sub-project B was added, if present — place a "Chat" nav link beside it). Read an existing modal/expandable block to mirror the debug-disclosure style.

- [ ] **Step 2: Route + nav**

In `handleRoute()`, add: hash `#/chat` → `this.view = 'chat'` and load workers. Add a "Chat" nav control on the list header (sets `location.hash = '/chat'`). Add a back-to-list affordance on the chat view.

- [ ] **Step 3: State + methods**

Add to the Alpine data: `chatWorkers: []`, `chatWorker: ''`, `chatMessages: []` (each `{role, text, debug?}`), `chatSession: null`, `chatInput: ''`, `chatSending: false`. Methods:
```javascript
async loadChatWorkers() {
  try { const r = await fetch('/chat/workers'); if (r.ok) { this.chatWorkers = await r.json(); if (!this.chatWorker && this.chatWorkers[0]) this.chatWorker = this.chatWorkers[0].name; } } catch (e) {}
},
newChat() { this.chatMessages = []; this.chatSession = null; },
async sendChat() {
  const msg = (this.chatInput || '').trim();
  if (!msg || !this.chatWorker || this.chatSending) return;
  this.chatMessages.push({ role: 'user', text: msg });
  this.chatInput = ''; this.chatSending = true;
  try {
    const r = await fetch('/chat/turn', { method: 'POST', headers: {'content-type':'application/json'},
      body: JSON.stringify({ worker: this.chatWorker, message: msg, session: this.chatSession }) });
    const t = await r.json();
    this.chatSession = t.session || this.chatSession;
    this.chatMessages.push({ role: 'assistant', text: t.reply || '(no reply)', debug: t, open: false });
  } catch (e) {
    this.chatMessages.push({ role: 'assistant', text: 'request failed', debug: null });
  } finally { this.chatSending = false; }
},
```
Call `loadChatWorkers()` when entering the chat route.

- [ ] **Step 4: Markup**

Add `<div x-show="view === 'chat'" ...>`:
- Header: worker `<select x-model="chatWorker">` (`<option>` per `chatWorkers` showing `name` + ` · ` + `model`), a "New chat" button (`@click="newChat()"`), a Back link to `#/`.
- Conversation: `<template x-for="(m, i) in chatMessages" :key="i">` — user bubbles right/neutral, assistant bubbles left; for assistant messages with `m.debug`, a small "detail" toggle (`@click="m.open = !m.open"`) revealing `m.debug.command.join(' ')`, `m.debug.prompt`, `m.debug.stdout`, `m.debug.session`, `m.debug.outcome`, `m.debug.duration_ms`. Mark `outcome !== 'success'` in red.
- Footer: `<textarea x-model="chatInput">` + Send button (`@click="sendChat()"`, disabled while `chatSending`; show a spinner/“…” while sending). Enter-to-send optional.
Match existing Tailwind styling.

- [ ] **Step 5: Verify serves + endpoints**

```bash
cd /Users/nicholas/project/ai/Cairn/cairn
( uv run cairn serve --host 127.0.0.1 --port 8158 --db-path /tmp/cairn-chat/cairn.db --no-access-log >/dev/null 2>&1 & )
for i in $(seq 1 40); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8158/ 2>/dev/null | grep -q 200 && break; done
echo "index: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8158/)"
echo "chat view present: $(curl -s http://127.0.0.1:8158/ | grep -c "view === 'chat'")"
# /chat/workers will 400 if no dispatch.yaml is found — that's expected here; just confirm the endpoint is wired:
echo "workers endpoint status: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8158/chat/workers)"
pkill -f "port 8158" 2>/dev/null; rm -rf /tmp/cairn-chat
```
Expected: `index: 200`, chat-view grep ≥ 1, workers endpoint returns 200 (if a `dispatch.yaml` is in cwd) or 400 (if not) — either proves it's wired (not 404). `pkill -f "cairn serve" || true`.

- [ ] **Step 6: Manual browser check**

With server + a valid `dispatch.yaml` in cwd (or `CAIRN_DISPATCH_CONFIG` set) and a host-installed engine: open Chat, pick a worker, send a message, confirm a reply comes back, expand the debug to see command/prompt/stdout/session, send a follow-up and confirm session continuity, "New chat" resets.

- [ ] **Step 7: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/static/index.html
git commit -m "feat: engine chat/debug page (request-response, multi-turn, debug trace)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** D1 worker source (T2 `list_workers`/`_find_worker`) · D2 CLI request/response (T2 `_run_turn`) · D3 multi-turn session (T2 `prepare_session`/`build_execute(...,session)`/`extract_session`; T4 holds session client-side) · D4 debug trace (T2 returns command/prompt/stdout/session; T4 disclosure) · D5 host execution via LocalRuntime + server endpoint (T2/T3) · D6 synchronous POST (T3) · no creds in `GET /chat/workers` (T2 test asserts) · `CAIRN_DISPATCH_CONFIG` (T2). v1 no persistence (conversation is client-side, T4).
- **Type consistency:** `ChatWorker/ChatTurnRequest/ChatTurnResult` field names identical across T1 (def), T2 (returns), T3 (router), T4 (reads). `_run_turn` signature matches its test and `run_turn` caller. `result.argv`/`result.session` from `build_execute` (DriverResult) used in T2.
- **Redaction:** command + stdout go through `execlog` before leaving the process (T2 test asserts secret absent).
- **Placeholder scan:** none. The two `parents[N]` indices (T2) and the minimal dispatch YAML (T2 test) are explicitly flagged to verify/adapt during implementation rather than guessed silently.
