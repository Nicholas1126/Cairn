# Local Engine Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Cairn run host-installed agents (claude/codex/opencode/pi) directly as workers — same bootstrap/reason/explore logic as Docker — selectable per project via a "Local Engine" checkbox, to avoid Docker's resource overhead.

**Architecture:** Introduce a `Runtime` protocol that the dispatcher's task layer already depends on (de-facto implemented by `ContainerManager`). Add `LocalRuntime` that runs the *same* `WorkerDriver` argv on the host via `subprocess`, in a per-project workspace under `~/.cairn/workspaces/<id>/`. Projects carry a `backend` field; the scheduler routes to the chosen runtime. All drivers/tasks/contracts/execution-logging are reused unchanged.

**Tech Stack:** Python 3, FastAPI, sqlite3, pytest, subprocess, AlpineJS (`index.html`).

**Spec:** `docs/superpowers/specs/2026-06-20-local-engine-worker-design.md`

**Conventions:** run tests with `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest`. Commit after each task. This repo has a graphify knowledge graph — subagents exploring code should prefer `graphify query "<question>"` before grepping.

**Cross-platform note baked into the design:** `pi`/`opencode` drivers emit argv that starts with `/bin/sh -lc <script>` and call the bare binary (`opencode`/`pi`) inside the script; `claude`/`codex` emit a direct argv (`claude`/`codex`). Therefore `LocalRuntime` (a) injects an augmented `PATH` into the child env so bare in-shell binaries resolve, and (b) rewrites argv[0] **only** when it is a known bare agent binary (claude/codex), leaving `/bin/sh` wrappers as-is. Consequence: on **Windows**, claude/codex work via the resolver; pi/opencode need an `sh` on PATH (e.g. Git Bash) — documented as a limitation.

---

### Task 1: Single data root `~/.cairn/`

**Files:**
- Modify: `cairn/src/cairn/server/db.py`
- Modify: `docker-compose.yaml`
- Test: `cairn/tests/test_cairn_home.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_cairn_home.py
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_cairn_home.py -v`
Expected: FAIL (no `cairn_home`/`default_db`).

- [ ] **Step 3: Implement in db.py**

Replace the module-level `DEFAULT_DB` definition (currently `DEFAULT_DB = Path.home() / ".local" / "share" / "cairn" / "cairn.db"`) with:

```python
import os


def cairn_home() -> Path:
    override = os.environ.get("CAIRN_HOME")
    return Path(override).expanduser() if override else Path.home() / ".cairn"


def default_db() -> Path:
    return cairn_home() / "cairn.db"


# Backwards-compatible module attribute used by app.py / cli.py defaults.
DEFAULT_DB = default_db()
```

(`executions_root()` already returns `_db_path.parent / "executions"`, so it becomes `~/.cairn/executions/` automatically — no change.)

In `docker-compose.yaml`, change the server volume target (line ~11) from `/root/.local/share/cairn/` to `/root/.cairn/`:

```yaml
      - ./datas/cairn/:/root/.cairn/
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_cairn_home.py -v` → PASS.
Then full suite: `uv run pytest -q 2>&1 | tail -3` → still green (no test depends on the old path; all use `tmp_path`).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/db.py cairn/tests/test_cairn_home.py docker-compose.yaml
git commit -m "feat: single data root ~/.cairn (cairn_home/default_db)"
```

---

### Task 2: `projects.backend` column + models + create/read

**Files:**
- Modify: `cairn/src/cairn/server/db.py` (schema + migration guard)
- Modify: `cairn/src/cairn/server/models.py` (`ProjectMeta`, `CreateProjectRequest`)
- Modify: `cairn/src/cairn/server/services.py` (`project_meta_from_row`)
- Modify: `cairn/src/cairn/server/routers/projects.py` (INSERT backend)
- Test: `cairn/tests/test_project_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_project_backend.py
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_backend_column_exists_and_defaults_docker(tmp_path):
    _client(tmp_path)
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "backend" in cols


def test_create_project_defaults_to_docker(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects", json={"title": "t", "origin": "o", "goal": "g"})
    assert r.status_code == 201
    assert r.json()["project"]["backend"] == "docker"


def test_create_project_with_local_backend(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects", json={"title": "t", "origin": "o", "goal": "g", "backend": "local"})
    assert r.status_code == 201
    pid = r.json()["project"]["id"]
    assert r.json()["project"]["backend"] == "local"
    # readback via list + detail
    assert c.get("/projects").json()[0]["backend"] == "local"
    assert c.get(f"/projects/{pid}").json()["project"]["backend"] == "local"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_project_backend.py -v`
Expected: FAIL (no backend column/field).

- [ ] **Step 3: Implement**

(a) `db.py` SCHEMA — add `backend` to the `projects` table definition:
```sql
    bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
    backend TEXT NOT NULL DEFAULT 'docker',
    created_at TEXT NOT NULL,
```
And in `_ensure_project_columns(conn)`, after the existing checks add:
```python
    if "backend" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN backend TEXT NOT NULL DEFAULT 'docker'")
```

(b) `models.py` — extend models:
```python
class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    bootstrap_enabled: bool
    backend: Literal["docker", "local"] = "docker"
    created_at: str
    reason: ProjectReason | None = None
```
```python
class CreateProjectRequest(BaseModel):
    title: str
    origin: str
    goal: str
    bootstrap_enabled: bool = True
    backend: Literal["docker", "local"] = "docker"
    hints: list[CreateHintInline] | None = None
    # keep the existing field_validator for title/origin/goal unchanged
```

(c) `services.py` `project_meta_from_row` — include backend (the row now has it):
```python
        backend=row["backend"],
```
(add this kwarg to the `ProjectMeta(...)` construction in `project_meta_from_row`).

(d) `routers/projects.py` `create_project` — store backend in the INSERT:
```python
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, backend, created_at) "
            "VALUES (?, ?, 'active', ?, ?, ?)",
            (pid, body.title, body.bootstrap_enabled, body.backend, now),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_project_backend.py -v` → PASS.
Full suite: `uv run pytest -q 2>&1 | tail -3`. If `test_server_api.py` asserts an exact project dict, add `"backend": "docker"` to its expectation.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/db.py cairn/src/cairn/server/models.py cairn/src/cairn/server/services.py cairn/src/cairn/server/routers/projects.py cairn/tests/test_project_backend.py
git commit -m "feat: per-project backend field (docker|local)"
```

---

### Task 3: `Runtime` protocol + runtime-provided snapshot root

**Files:**
- Create: `cairn/src/cairn/dispatcher/runtime/base.py`
- Modify: `cairn/src/cairn/dispatcher/runtime/containers.py` (add `snapshot_root()`)
- Modify: `cairn/src/cairn/dispatcher/tasks/common.py` (`write_graph_snapshot_reference` uses `runtime.snapshot_root()`)
- Test: `cairn/tests/test_runtime_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_runtime_protocol.py
from __future__ import annotations

from cairn.dispatcher.runtime.base import Runtime
from cairn.dispatcher.tasks import common


class _FakeRuntime:
    def __init__(self):
        self.written = []
    def snapshot_root(self) -> str:
        return "/fake/snap"
    def write_text_file(self, key, path, content):
        self.written.append((key, path, content))


def test_write_graph_snapshot_reference_uses_runtime_snapshot_root():
    rt = _FakeRuntime()
    ref = common.write_graph_snapshot_reference(rt, "proj_1", "graph: yaml", phase="explore_execute")
    # path is under the runtime-provided snapshot root, and the prompt references that same path
    assert rt.written, "graph file must be written via runtime"
    written_path = rt.written[0][1]
    assert written_path.startswith("/fake/snap/")
    assert written_path in ref


def test_runtime_protocol_is_runtime_checkable():
    # ContainerManager must structurally satisfy Runtime
    from cairn.dispatcher.runtime.containers import ContainerManager
    assert hasattr(ContainerManager, "ensure_running")
    assert hasattr(ContainerManager, "snapshot_root")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_runtime_protocol.py -v`
Expected: FAIL (no `runtime.base`, no `snapshot_root`).

- [ ] **Step 3: Implement**

(a) Create `cairn/src/cairn/dispatcher/runtime/base.py`:
```python
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cairn.dispatcher.runtime.process import ProcessResult  # noqa: F401  (re-export convenience)


@runtime_checkable
class Runtime(Protocol):
    """Execution backend the dispatcher task layer depends on.

    Implemented by ContainerManager (Docker) and LocalRuntime (host subprocess).
    """

    def ensure_running(self, project_id: str) -> str: ...
    def container_name(self, project_id: str) -> str: ...
    def build_exec_process(self, name: str, env: dict[str, str], command: list[str],
                           timeout_seconds: int | None = None, kill_after_seconds: int = 5): ...
    def write_text_file(self, name: str, path: str, content: str) -> None: ...
    def snapshot_root(self) -> str: ...
    def create_startup_container(self) -> str: ...
    def needs_completed_cleanup(self, project_id: str) -> bool: ...
    def needs_stopped_cleanup(self, project_id: str) -> bool: ...
    def cleanup_completed(self, project_id: str) -> bool: ...
    def cleanup_stopped(self, project_id: str) -> bool: ...
    def close(self) -> None: ...
```

(b) `containers.py` — add a method to `ContainerManager` (the constant `GRAPH_SNAPSHOT_ROOT` lives in `tasks/common.py`; hard-code the same container path here):
```python
    def snapshot_root(self) -> str:
        return "/tmp/cairn-prompts"
```

(c) `tasks/common.py` — change `write_graph_snapshot_reference` to use the runtime's root instead of the module constant:
```python
def write_graph_snapshot_reference(
    container_manager,
    container_name: str,
    graph_yaml: str,
    *,
    phase: str,
) -> str:
    path = f"{container_manager.snapshot_root()}/{phase}-{uuid.uuid4().hex[:12]}/graph.yaml"
    container_manager.write_text_file(container_name, path, graph_yaml)
    return (
        "The graph YAML snapshot is stored in this file inside the current container:\n\n"
        f"{path}\n\n"
        "Before using the graph, read the entire file and treat its contents as the YAML snapshot "
        "for this Graph section."
    )
```
(Leave the `GRAPH_SNAPSHOT_ROOT` constant in place for reference/back-compat; it is no longer read.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_runtime_protocol.py -v` → PASS.
Full suite: `uv run pytest -q 2>&1 | tail -3` → green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/base.py cairn/src/cairn/dispatcher/runtime/containers.py cairn/src/cairn/dispatcher/tasks/common.py cairn/tests/test_runtime_protocol.py
git commit -m "feat: Runtime protocol + runtime-provided snapshot root"
```

---

### Task 4: Cross-platform engine resolver + probe

**Files:**
- Create: `cairn/src/cairn/dispatcher/runtime/local/__init__.py` (empty)
- Create: `cairn/src/cairn/dispatcher/runtime/local/resolve.py`
- Test: `cairn/tests/test_engine_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_engine_resolve.py
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


def test_resolve_override_wins(monkeypatch, tmp_path):
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_resolve.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `resolve.py`**

```python
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# worker type -> bare agent binary name
BINARY = {"claudecode": "claude", "codex": "codex", "opencode": "opencode", "pi": "pi"}
# binaries we will rewrite argv[0] for when seen bare (direct-argv drivers)
DIRECT_BINARIES = set(BINARY.values())


@dataclass(slots=True)
class Resolved:
    path: str
    launcher: str  # "direct" | "cmd" | "powershell"
    source: str    # "override" | "path"


def _engines_config_path() -> Path:
    override = os.environ.get("CAIRN_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".cairn"
    return base / "engines.json"


def _load_overrides() -> dict:
    try:
        return json.loads(_engines_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _augmented_dirs() -> list[str]:
    dirs: list[str] = []
    try:
        out = subprocess.run(["npm", "config", "get", "prefix"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            prefix = out.stdout.strip()
            dirs.append(prefix)                       # Windows: shims live here
            dirs.append(str(Path(prefix) / "bin"))    # unix
    except (OSError, subprocess.SubprocessError):
        pass
    home = Path.home()
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local" / "bin")]
    nvm_bin = os.environ.get("NVM_BIN")
    if nvm_bin:
        dirs.append(nvm_bin)
    return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d)]


def augmented_path(base_path: str) -> str:
    extra = _augmented_dirs()
    parts = [p for p in [base_path] if p] + extra
    return os.pathsep.join(parts)


def _launcher_for(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".cmd", ".bat")):
        return "cmd"
    if lower.endswith(".ps1"):
        return "powershell"
    return "direct"


def _windows_candidates(name: str) -> list[str]:
    return [f"{name}.cmd", f"{name}.exe", f"{name}.bat", f"{name}.ps1", name]


def resolve_engine(worker_type: str) -> Resolved | None:
    binary = BINARY.get(worker_type, worker_type)
    ov = _load_overrides().get(worker_type) or _load_overrides().get(binary)
    if isinstance(ov, dict) and ov.get("path"):
        path = ov["path"]
        return Resolved(path=path, launcher=ov.get("launcher") or _launcher_for(path), source="override")
    search = augmented_path(os.environ.get("PATH", ""))
    if os.name == "nt":
        for cand in _windows_candidates(binary):
            found = shutil.which(cand, path=search)
            if found:
                return Resolved(path=found, launcher=_launcher_for(found), source="path")
        return None
    found = shutil.which(binary, path=search)
    return Resolved(path=found, launcher="direct", source="path") if found else None


def launch_argv(resolved: Resolved, args: list[str]) -> list[str]:
    if resolved.launcher == "cmd":
        return ["cmd", "/c", resolved.path, *args]
    if resolved.launcher == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved.path, *args]
    return [resolved.path, *args]


def probe_engine(worker_type: str) -> dict:
    resolved = resolve_engine(worker_type)
    if resolved is None:
        return {"launchable": False, "path": None, "version": None, "source": None}
    version, launchable = None, False
    try:
        out = subprocess.run(launch_argv(resolved, ["--version"]),
                             capture_output=True, text=True, timeout=10)
        launchable = out.returncode == 0
        text = (out.stdout or out.stderr or "").strip()
        version = text.splitlines()[0] if text else None
    except (OSError, subprocess.SubprocessError):
        launchable = False
    return {"launchable": launchable, "path": resolved.path, "version": version, "source": resolved.source}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_resolve.py -v` → PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/local/__init__.py cairn/src/cairn/dispatcher/runtime/local/resolve.py cairn/tests/test_engine_resolve.py
git commit -m "feat: cross-platform engine resolver + probe"
```

---

### Task 5: `LocalManagedProcess` (host subprocess, tree-kill, timeout)

**Files:**
- Create: `cairn/src/cairn/dispatcher/runtime/local/process.py`
- Test: `cairn/tests/test_local_process.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_local_process.py
from __future__ import annotations

import sys

from cairn.dispatcher.runtime.local.process import LocalManagedProcess


def _run(argv, timeout=30):
    p = LocalManagedProcess(argv, env={}, cwd=None)
    p.start()
    return p.communicate(timeout=timeout)


def test_runs_command_captures_stdout_and_exit():
    res = _run([sys.executable, "-c", "print('pong')"])
    assert res.returncode == 0
    assert "pong" in res.stdout
    assert res.timed_out is False


def test_nonzero_exit():
    res = _run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert res.returncode == 3


def test_timeout_marks_timed_out_and_kills():
    res = _run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert res.timed_out is True


def test_cwd_and_env_applied(tmp_path):
    p = LocalManagedProcess(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ.get('CAIRN_T',''))"],
        env={"CAIRN_T": "yes"}, cwd=str(tmp_path),
    )
    p.start()
    res = p.communicate(timeout=30)
    assert str(tmp_path) in res.stdout
    assert "yes" in res.stdout
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_local_process.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `process.py`**

```python
from __future__ import annotations

import os
import signal
import subprocess

from cairn.dispatcher.runtime.process import ProcessResult


class LocalManagedProcess:
    """Host-subprocess analog of the container ManagedProcess.

    Same surface used by the task layer + HeartbeatLease/TaskCancellation:
    start() / communicate(timeout) / kill() / cancel(reason).
    Timeout is enforced here (no container `timeout` coreutil). On expiry or
    cancel the whole process tree is killed (unix process group / Windows /T).
    """

    def __init__(self, command: list[str], env: dict[str, str], cwd: str | None):
        self.command = command
        self.env = env
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._timed_out = False
        self._cancel_reason: str | None = None

    def start(self) -> None:
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        full_env = {**os.environ, **self.env}
        self._proc = subprocess.Popen(
            self.command,
            cwd=self._cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )

    def communicate(self, timeout: float | None) -> ProcessResult:
        assert self._proc is not None
        stdout, stderr = "", ""
        try:
            stdout, stderr = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._timed_out = True
            self._kill_tree()
            try:
                stdout, stderr = self._proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        rc = self._proc.returncode
        if rc is None:
            rc = 137 if self._timed_out else 1
        return ProcessResult(
            returncode=rc,
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=self._timed_out,
            cancelled=self._cancel_reason is not None,
            cancel_reason=self._cancel_reason,
        )

    def kill(self) -> None:
        self._kill_tree()

    def cancel(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason
        self._kill_tree()

    def _kill_tree(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_local_process.py -v` → PASS (4 tests). (Skip-safe on Windows: tests use `sys.executable`.)

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/local/process.py cairn/tests/test_local_process.py
git commit -m "feat: LocalManagedProcess (host subprocess, tree-kill, timeout)"
```

---

### Task 6: `LocalRuntime`

**Files:**
- Create: `cairn/src/cairn/dispatcher/runtime/local/runtime.py`
- Test: `cairn/tests/test_local_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_local_runtime.py
from __future__ import annotations

import sys
from pathlib import Path

from cairn.dispatcher.runtime.local.runtime import LocalRuntime


def _rt(tmp_path):
    return LocalRuntime(workspaces_root=str(tmp_path / "ws"),
                        completed_action="remove",
                        agents_source=None)


def test_ensure_running_creates_workspace(tmp_path):
    rt = _rt(tmp_path)
    path = rt.ensure_running("proj_1")
    assert Path(path).is_dir()
    assert rt.container_name("proj_1") == "proj_1"


def test_seeds_agent_config_when_source_present(tmp_path):
    src = tmp_path / "src"
    (src / ".agents").mkdir(parents=True)
    (src / ".agents" / "a.md").write_text("hi", encoding="utf-8")
    (src / "AGENTS.md").write_text("agents", encoding="utf-8")
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="remove",
                      agents_source=str(src))
    ws = Path(rt.ensure_running("proj_1"))
    assert (ws / ".claude" / "a.md").read_text() == "hi"
    assert (ws / "AGENTS.md").read_text() == "agents"
    assert (ws / "CLAUDE.md").read_text() == "agents"


def test_write_text_file_lands_in_snapshot_root(tmp_path):
    rt = _rt(tmp_path)
    rt.ensure_running("proj_1")
    p = f"{rt.snapshot_root()}/explore-abc/graph.yaml"
    rt.write_text_file("proj_1", p, "graph: 1")
    assert Path(p).read_text() == "graph: 1"


def test_build_exec_process_runs_in_workspace(tmp_path):
    rt = _rt(tmp_path)
    ws = rt.ensure_running("proj_1")
    proc = rt.build_exec_process("proj_1", {}, [sys.executable, "-c", "import os;print(os.getcwd())"])
    proc.start()
    res = proc.communicate(timeout=30)
    assert ws in res.stdout


def test_cleanup_completed_removes_workspace(tmp_path):
    rt = _rt(tmp_path)
    ws = Path(rt.ensure_running("proj_1"))
    assert rt.needs_completed_cleanup("proj_1") is True
    assert rt.cleanup_completed("proj_1") is True
    assert not ws.exists()


def test_cleanup_keep_preserves_workspace(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="keep", agents_source=None)
    ws = Path(rt.ensure_running("proj_1"))
    assert rt.needs_completed_cleanup("proj_1") is False
    rt.cleanup_completed("proj_1")
    assert ws.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_local_runtime.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `runtime.py`**

```python
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from cairn.dispatcher.runtime.local import resolve
from cairn.dispatcher.runtime.local.process import LocalManagedProcess


class LocalRuntime:
    """Run worker argv on the host. Mirrors the ContainerManager surface."""

    def __init__(self, *, workspaces_root: str, completed_action: str, agents_source: str | None):
        self._root = Path(workspaces_root).expanduser()
        self._completed_action = completed_action  # "keep" | "remove"
        self._agents_source = Path(agents_source).expanduser() if agents_source else None
        self._snapshot_root = Path(tempfile.gettempdir()) / "cairn-prompts"

    # --- identity / lifecycle ---
    def container_name(self, project_id: str) -> str:
        return project_id

    def _workspace(self, project_id: str) -> Path:
        return self._root / project_id.replace("/", "-")

    def ensure_running(self, project_id: str) -> str:
        ws = self._workspace(project_id)
        first = not ws.exists()
        ws.mkdir(parents=True, exist_ok=True)
        if first:
            self._seed_agent_config(ws)
        return str(ws)

    def _seed_agent_config(self, ws: Path) -> None:
        src = self._agents_source
        if src is None or not src.exists():
            return
        agents = src / ".agents"
        if agents.is_dir():
            shutil.copytree(agents, ws / ".claude", dirs_exist_ok=True)
            shutil.copytree(agents, ws / ".agents", dirs_exist_ok=True)
        agents_md = src / "AGENTS.md"
        if agents_md.is_file():
            (ws / "AGENTS.md").write_text(agents_md.read_text(encoding="utf-8"), encoding="utf-8")
            (ws / "CLAUDE.md").write_text(agents_md.read_text(encoding="utf-8"), encoding="utf-8")

    def create_startup_container(self) -> str:
        tmp = self._root / f"_startup-{uuid.uuid4().hex[:12]}"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp.name

    # --- exec ---
    def build_exec_process(self, name: str, env: dict[str, str], command: list[str],
                           timeout_seconds: int | None = None, kill_after_seconds: int = 5
                           ) -> LocalManagedProcess:
        # timeout_seconds is enforced by communicate() (no container `timeout` coreutil),
        # so it is intentionally not turned into an argv prefix here.
        ws = self._workspace_for_key(name)
        child_env = dict(env)
        child_env["PATH"] = resolve.augmented_path(env.get("PATH") or os.environ.get("PATH", ""))
        argv = self._rewrite_argv(command)
        return LocalManagedProcess(argv, child_env, cwd=str(ws))

    def _workspace_for_key(self, name: str) -> Path:
        # `name` is whatever ensure_running/create_startup_container returned (project key or tmp name)
        candidate = self._root / name
        return candidate if candidate.exists() else self._workspace(name)

    @staticmethod
    def _rewrite_argv(command: list[str]) -> list[str]:
        if not command:
            return command
        head = command[0]
        # Only rewrite bare known agent binaries (claude/codex direct-argv drivers).
        # /bin/sh wrappers (pi/opencode) are left as-is and resolve their inner binary via PATH.
        if os.path.basename(head) == head and head in resolve.DIRECT_BINARIES:
            # head is a worker *binary* name; find the worker_type with that binary
            wtype = next((t for t, b in resolve.BINARY.items() if b == head), head)
            resolved = resolve.resolve_engine(wtype)
            if resolved is not None:
                return resolve.launch_argv(resolved, command[1:])
        return command

    def write_text_file(self, name: str, path: str, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def snapshot_root(self) -> str:
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        return str(self._snapshot_root)

    # --- cleanup ---
    def needs_completed_cleanup(self, project_id: str) -> bool:
        return self._completed_action == "remove" and self._workspace(project_id).exists()

    def needs_stopped_cleanup(self, project_id: str) -> bool:
        return False  # local processes are not long-lived containers; nothing to stop

    def cleanup_completed(self, project_id: str) -> bool:
        if self._completed_action == "remove":
            shutil.rmtree(self._workspace(project_id), ignore_errors=True)
        return True

    def cleanup_stopped(self, project_id: str) -> bool:
        return True

    def close(self) -> None:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_local_runtime.py -v` → PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/local/runtime.py cairn/tests/test_local_runtime.py
git commit -m "feat: LocalRuntime (host execution backend)"
```

---

### Task 7: `LocalConfig` in dispatch config

**Files:**
- Modify: `cairn/src/cairn/dispatcher/config.py`
- Modify: `dispatch.example.yaml` (document the optional block)
- Test: `cairn/tests/test_config_and_adapters.py` (append)

- [ ] **Step 1: Write the failing test (append to the existing file)**

```python
# append to cairn/tests/test_config_and_adapters.py
def test_local_config_defaults_when_absent():
    from cairn.dispatcher.config import DispatchConfig
    # build a minimal valid config dict reusing whatever helper/sample the file already uses;
    # if the file has a `_minimal_config_dict()` helper, use it, else construct inline.
    cfg = DispatchConfig.model_validate(_minimal_dispatch_dict())
    assert cfg.local.workspaces_root.endswith("workspaces")
    assert cfg.local.completed_action in ("keep", "remove")


def test_local_config_override():
    from cairn.dispatcher.config import DispatchConfig
    d = _minimal_dispatch_dict()
    d["local"] = {"workspaces_root": "/tmp/ws", "completed_action": "keep"}
    cfg = DispatchConfig.model_validate(d)
    assert cfg.local.workspaces_root == "/tmp/ws"
    assert cfg.local.completed_action == "keep"
```

If the test file has no `_minimal_dispatch_dict()` helper, add one at the top of the file that returns a minimal valid dispatch dict (server, runtime, tasks, container, one mock worker) — model it on an existing valid-config test already in that file.

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_config_and_adapters.py -k local_config -v`
Expected: FAIL (no `cfg.local`).

- [ ] **Step 3: Implement in config.py**

Add a model and field (default makes it optional; existing configs keep working):
```python
class LocalConfig(BaseModel):
    workspaces_root: str = "~/.cairn/workspaces"
    completed_action: CompletedAction = "keep"
    engines_config: str = "~/.cairn/engines.json"
```
In `DispatchConfig`, add the field with a default factory:
```python
    container: ContainerConfig
    local: LocalConfig = Field(default_factory=LocalConfig)
```
(`DispatchConfig` has `extra="forbid"`, so declaring the field is required for configs that set `local:`. The default factory keeps it optional.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_config_and_adapters.py -k local_config -v` → PASS.
Full suite: `uv run pytest -q 2>&1 | tail -3` → green.

Add a commented example to `dispatch.example.yaml`:
```yaml
# Optional: host (Local Engine) backend settings. Used by projects created with
# backend=local. Agents must be installed on the host.
# local:
#   workspaces_root: "~/.cairn/workspaces"
#   completed_action: keep      # keep | remove
#   engines_config: "~/.cairn/engines.json"   # optional explicit binary overrides
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/config.py cairn/tests/test_config_and_adapters.py dispatch.example.yaml
git commit -m "feat: LocalConfig block in dispatch config"
```

---

### Task 8: Scheduler routing (dual runtime per project)

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_runtime_logic.py` (append a routing test)

- [ ] **Step 1: Write the failing test**

```python
# append to cairn/tests/test_runtime_logic.py
def test_runtime_for_routes_by_backend():
    from cairn.dispatcher.scheduler.loop import DispatcherLoop

    class _Loop(DispatcherLoop):
        def __init__(self):  # bypass real __init__ / config / docker
            self.container_manager = object()
            self._local_runtime = object()

    loop = _Loop()

    class _Meta:
        def __init__(self, backend): self.backend = backend
    class _Proj:
        def __init__(self, backend): self.project = _Meta(backend)

    assert loop._runtime_for(_Proj("docker")) is loop.container_manager
    assert loop._runtime_for(_Proj("local")) is loop._local_runtime
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_runtime_logic.py -k runtime_for -v`
Expected: FAIL (no `_runtime_for` / `_local_runtime`).

- [ ] **Step 3: Implement in loop.py**

(a) In `__init__`, after `self.container_manager = ContainerManager(self.config.container)` add a lazy local runtime holder:
```python
        self._local_runtime = None
```
Add imports at top: `from cairn.dispatcher.runtime.local.runtime import LocalRuntime` and `from pathlib import Path` (if not present).

(b) Add helper methods on `DispatcherLoop`:
```python
    def _ensure_local_runtime(self):
        if self._local_runtime is None:
            agents_source = Path(__file__).resolve().parents[4] / "container"
            self._local_runtime = LocalRuntime(
                workspaces_root=self.config.local.workspaces_root,
                completed_action=self.config.local.completed_action,
                agents_source=str(agents_source) if agents_source.exists() else None,
            )
        return self._local_runtime

    def _runtime_for(self, project) -> object:
        if getattr(project.project, "backend", "docker") == "local":
            return self._ensure_local_runtime()
        return self.container_manager
```
Note on `agents_source`: it points at the repo's `container/` dir (which holds `.agents` and `AGENTS.md`). `parents[4]` resolves `.../cairn/src/cairn/dispatcher/scheduler/loop.py` → repo root; verify the index by printing during implementation and adjust if the repo layout differs.

(c) In `_dispatch_bootstrap`, `_dispatch_explore`, `_dispatch_reason`: replace the positional `self.container_manager` passed to `run_*_task(...)` with `self._runtime_for(project)`.

(d) In cleanup routing (`_queue_container_cleanups` and wherever `needs_completed_cleanup`/`cleanup_completed`/`needs_stopped_cleanup`/`cleanup_stopped` are called against `self.container_manager`): these are keyed by a project summary. Route them through the project's backend. Since cleanups operate on `ProjectSummary` (which now carries `backend` via `ProjectMeta`), pick the runtime:
```python
        runtime = self._ensure_local_runtime() if getattr(summary, "backend", "docker") == "local" else self.container_manager
```
and call the cleanup methods on `runtime` instead of `self.container_manager`. (Read the current `_queue_container_cleanups` body and apply this substitution at each call site.)

(e) In `close()`, also close the local runtime if created:
```python
        self.container_manager.close()
        if self._local_runtime is not None:
            self._local_runtime.close()
```

(f) Startup healthcheck (`_run_startup_healthchecks`) currently uses `self.container_manager`. Leave it on the container manager for docker workers; local availability is validated lazily at first dispatch + by the existing healthcheck running through whichever runtime executes. (No change required for this task; do not break the existing startup path.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_runtime_logic.py -k runtime_for -v` → PASS.
Full suite: `uv run pytest -q 2>&1 | tail -6` → green. If a mock end-to-end test constructs `DispatcherLoop` and asserts container usage, ensure docker-backed projects still route to `container_manager` (default backend is docker).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/scheduler/loop.py cairn/tests/test_runtime_logic.py
git commit -m "feat: scheduler routes per-project to docker or local runtime"
```

---

### Task 9: New Project "Local Engine" checkbox (frontend)

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html`

No JS test runner — verify by serving the page.

- [ ] **Step 1: Add the checkbox + state**

Find the New Project modal (search `Run bootstrap attempt`). Add a "Local Engine" checkbox next to it, bound to a new `newProject.backend`-style flag. In the Alpine component's New-Project form state, add `localEngine: false`. Render:
```html
<label class="flex items-center gap-2 cursor-pointer select-none">
  <input type="checkbox" x-model="newProject.localEngine" class="...match existing checkbox classes...">
  <span class="text-sm text-slate-600">Local Engine</span>
</label>
```

- [ ] **Step 2: Send `backend` on create**

In the create-project submit handler (search the `fetch('/projects'` POST), add `backend` to the JSON body:
```javascript
body: JSON.stringify({
  title: ..., origin: ..., goal: ...,
  bootstrap_enabled: ...,
  backend: this.newProject.localEngine ? 'local' : 'docker',
  hints: ...,
})
```
Reset `localEngine` to `false` when the modal opens/closes (wherever the form is reset).

- [ ] **Step 3: Verify the page serves and carries the field**

```bash
cd /Users/nicholas/project/ai/Cairn/cairn
( uv run cairn serve --host 127.0.0.1 --port 8155 --db-path /tmp/cairn-le/cairn.db --no-access-log >/dev/null 2>&1 & )
for i in $(seq 1 30); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8155/ | grep -q 200 && break; done
curl -s -o /dev/null -w "index: %{http_code}\n" http://127.0.0.1:8155/
curl -s http://127.0.0.1:8155/ | grep -c "Local Engine"
# create a local project end-to-end through the API the UI will hit:
curl -s -X POST http://127.0.0.1:8155/projects -H 'content-type: application/json' \
  -d '{"title":"t","origin":"o","goal":"g","backend":"local"}' | grep -o '"backend":"local"'
pkill -f "port 8155"; rm -rf /tmp/cairn-le
```
Expected: `index: 200`, `Local Engine` count ≥ 1, and `"backend":"local"` printed.

- [ ] **Step 4: Manual browser check**

Open a project list, click New Project, confirm the "Local Engine" checkbox renders and that creating with it checked yields a project whose backend is `local` (visible via the API / later in detail).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/static/index.html
git commit -m "feat: New Project Local Engine checkbox -> backend=local"
```

---

### Task 10: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/specs/dispatcher-design.md`

- [ ] **Step 1: README — local mode + deployment**

Add a "Local Engine (host) worker" subsection: prerequisites (agents installed on host: `claude`/`codex`/`opencode`/`pi`), that **Local mode runs the dispatcher on the host** (`uv run cairn dispatch`, not the compose `cairn-dispatcher` container), workspaces at `~/.cairn/workspaces/<id>/`, optional `~/.cairn/engines.json` overrides, and the cross-platform note (Windows: claude/codex work; pi/opencode need `sh` on PATH). State the data root is now `~/.cairn/`.

- [ ] **Step 2: dispatcher-design.md — Runtime backends**

Document: the `Runtime` protocol; `ContainerManager` (docker) vs `LocalRuntime` (host); per-project `backend` routing; engine resolver/`probe_engine` + `engines.json` overrides; that timeout/cancel for local is enforced via process-group/Job tree-kill; security/limitation notes (no isolation, no image tools).

- [ ] **Step 3: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add README.md docs/specs/dispatcher-design.md
git commit -m "docs: local engine worker (Runtime backends, deployment, limits)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** Runtime protocol (T3) · LocalRuntime + workspace seeding + lifecycle (T6) · LocalManagedProcess tree-kill/timeout (T5) · cross-platform resolver + engines.json override + probe (T4) · backend field + New Project checkbox + routing (T2/T9/T8) · LocalConfig (T7) · single root ~/.cairn (T1) · deployment + limitations docs (T10). All spec sections map to a task.
- **`/bin/sh` reality handled:** T6 `_rewrite_argv` rewrites only bare claude/codex; pi/opencode `/bin/sh` wrappers run as-is with augmented PATH injected (T6 `build_exec_process`). Windows pi/opencode limitation documented (header + T10).
- **Snapshot path:** made runtime-provided (T3) so local uses a host temp dir (Windows-safe), keeping the written path and the prompt reference consistent.
- **Type consistency:** `Runtime` method names (T3) match `LocalRuntime` (T6) and `ContainerManager`. `resolve.Resolved(path,launcher,source)` + `launch_argv` + `augmented_path` + `BINARY`/`DIRECT_BINARIES` consistent across T4/T6. `ProcessResult` reused from existing `runtime/process.py` in T5. `backend` field name identical across DB/model/router/scheduler (T2/T8/T9).
- **No migration:** T1 changes the default root only; dev data is disposable (per user). Tests use `tmp_path`, unaffected.
