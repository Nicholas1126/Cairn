# Project Knowledge Injection (子项目 B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让后台 worker 复用每个项目的前期分析产物——把项目根目录 A 只读挂载进工作区 `./project`,并在 prompt 中告知 worker 资料在哪、怎么用、优先复用;同时保证 `graphify`/`codegraph` 两个查询工具在 docker(装入镜像)与 local(探测+告警)两端就绪。

**Architecture:** 每项目新增可选 `project_root`(宿主机绝对路径 A)。docker 后端在容器创建时把 A `bind-mount` 到 `/home/kali/workspace/project`(`mode=ro`);local 后端在工作区内建软链接 `project → A`。dispatcher 探测 A 下实际存在的约定子目录(`src-repo/docs-out/graphify-out/scan-out/codegraph-out`),渲染 `{project_knowledge}` 指令。依赖工具:Dockerfile 追加安装;local 引擎页新增工具就绪探测与告警。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / sqlite3 / docker SDK / pytest;前端 Alpine.js 单文件 `index.html`;Docker(kali 镜像)。

**Spec:** `docs/superpowers/specs/2026-06-21-project-knowledge-injection-design.md`

**分支:** `feat/project-knowledge-injection`(叠在 `feat/skills-management` 之上)。

---

## 测试约定(先读)

- 运行单测:`cd cairn && uv run pytest <path> -v`(项目用 uv;`cairn/` 是 Python 包根)。
- 全量:`cd cairn && uv run pytest -q`。
- 提交前确保新增/相关测试通过;`git add` 只加明确改到的文件(**严禁 `git add -A`** —— 会把生成物 `graphify-out/`、未跟踪的 `.claude/`/`CLAUDE.md`/`AGENTS.md` 带入)。

---

## 文件结构(决策锁定)

**B 主体**
- `cairn/src/cairn/server/db.py` — `projects` 加列 `project_root`(迁移守卫)。
- `cairn/src/cairn/server/models.py` — `CreateProjectRequest.project_root`、`ProjectMeta.project_root`、新增 `ToolInfo`(Dep-2 用)。
- `cairn/src/cairn/server/routers/projects.py` — 创建项目写入 + 目录存在校验。
- `cairn/src/cairn/server/services.py` — `project_meta_from_row` 回读 `project_root`。
- `cairn/src/cairn/dispatcher/runtime/base.py` — `Runtime.ensure_running` 签名加可选 `project_root`。
- `cairn/src/cairn/dispatcher/runtime/containers.py` — 容器创建时只读挂载 A。
- `cairn/src/cairn/dispatcher/runtime/local/runtime.py` — 工作区内软链接 `project → A`。
- `cairn/src/cairn/dispatcher/prompting.py` — `format_project_knowledge`。
- `cairn/src/cairn/dispatcher/tasks/common.py` — `prepare_project_knowledge`。
- `cairn/src/cairn/dispatcher/tasks/{bootstrap,reason,explore}.py` — 传 `project_root` + `{project_knowledge}`。
- `cairn/src/cairn/dispatcher/prompts/default/{bootstrap,bootstrap_conclude,reason,explore,explore_conclude}.md` — 加 `{project_knowledge}` 占位。
- `cairn/src/cairn/server/static/index.html` — New Project 表单加 Project root 字段。
- 测试 fakes:`cairn/tests/conftest.py`、`cairn/tests/test_mock_end_to_end.py`、`cairn/tests/test_chat_service.py` 的 `ensure_running` 签名。

**Dep-1**
- `container/Dockerfile` — 追加 `graphify` + `codegraph` 安装。

**Dep-2**
- `cairn/src/cairn/dispatcher/runtime/local/resolve.py` — `TOOLS` + `probe_tool`。
- `cairn/src/cairn/server/routers/engines.py` — `GET /tools`。
- `cairn/src/cairn/server/static/index.html` — /engines 页 Agents/Tools 两组 + 缺失告警。

**文档**
- `docs/specs/server-protocol.md`、`docs/specs/dispatcher-design.md`、`README`(若有)。

---

## Part 1 — B 主体

### Task 1: DB 加 `project_root` 列

**Files:**
- Modify: `cairn/src/cairn/server/db.py:149-158`(`_ensure_project_columns`)
- Test: `cairn/tests/test_db_project_root.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_db_project_root.py`:

```python
from __future__ import annotations

from cairn.server import db


def test_projects_table_has_project_root_column(tmp_path, monkeypatch):
    db._db_path = None  # reset module singleton for an isolated DB
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    assert "project_root" in cols


def test_ensure_project_columns_adds_project_root_to_legacy_table(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL)"
    )
    db._ensure_project_columns(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    assert "project_root" in cols
    conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_db_project_root.py -v`
Expected: FAIL（`project_root` 不在列里）。

- [ ] **Step 3: 实现**

在 `SCHEMA` 的 `projects` 建表里(`db.py:32-43`),在 `reason_last_heartbeat_at TEXT` 后加一行 `project_root TEXT`(放在最后一个字段后,注意上一行补逗号):

```python
    reason_last_heartbeat_at TEXT,
    project_root TEXT
);
```

在 `_ensure_project_columns`(`db.py:149-158`)末尾(`backend` 守卫之后)追加:

```python
    if "project_root" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN project_root TEXT")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_db_project_root.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/db.py cairn/tests/test_db_project_root.py
git commit -m "feat(B): add project_root column to projects table"
```

---

### Task 2: 模型加 `project_root`(+ `ToolInfo` 占位)

**Files:**
- Modify: `cairn/src/cairn/server/models.py:135-142`（`ProjectMeta`）、`:173-187`（`CreateProjectRequest`）、`:92-99` 后（新增 `ToolInfo`）
- Test: `cairn/tests/test_models_project_root.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_models_project_root.py`:

```python
from cairn.server.models import CreateProjectRequest, ProjectMeta, ToolInfo


def test_create_request_project_root_optional_default_none():
    req = CreateProjectRequest(title="t", origin="o", goal="g")
    assert req.project_root is None
    req2 = CreateProjectRequest(title="t", origin="o", goal="g", project_root="/data/A")
    assert req2.project_root == "/data/A"


def test_project_meta_carries_project_root():
    meta = ProjectMeta(id="p1", title="t", status="active",
                       bootstrap_enabled=True, backend="docker",
                       created_at="2026-06-21T00:00:00Z", project_root="/data/A")
    assert meta.project_root == "/data/A"


def test_tool_info_shape():
    t = ToolInfo(name="graphify", launchable=True, version="graphify 0.8.41", path="/usr/bin/graphify")
    assert t.name == "graphify" and t.launchable is True
    t2 = ToolInfo(name="codegraph", launchable=False)
    assert t2.version is None and t2.path is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_models_project_root.py -v`
Expected: FAIL（字段/类不存在）。

- [ ] **Step 3: 实现**

`ProjectMeta`（`models.py:135-142`）加字段:

```python
class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    bootstrap_enabled: bool
    backend: Literal["docker", "local"] = "docker"
    created_at: str
    reason: ProjectReason | None = None
    project_root: str | None = None
```

`CreateProjectRequest`（`models.py:173-179`）在 `hints` 字段后加:

```python
    hints: list[CreateHintInline] | None = None
    project_root: str | None = None
```

在 `EngineInfo` 之后（`models.py:99` 后空行处）新增:

```python
class ToolInfo(BaseModel):
    name: str
    launchable: bool
    version: str | None = None
    path: str | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_models_project_root.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/server/models.py cairn/tests/test_models_project_root.py
git commit -m "feat(B): project_root on project models + ToolInfo"
```

---

### Task 3: 创建项目写入 + 目录存在校验 + 回读

**Files:**
- Modify: `cairn/src/cairn/server/routers/projects.py:79-125`（`create_project`）
- Modify: `cairn/src/cairn/server/services.py:206-215`（`project_meta_from_row`）
- Test: `cairn/tests/test_create_project_root.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_create_project_root.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_create_with_existing_project_root_persists_and_reads_back(tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    client = _client(tmp_path)
    r = client.post("/projects", json={
        "title": "t", "origin": "o", "goal": "g", "project_root": str(a),
    })
    assert r.status_code == 201, r.text
    assert r.json()["project"]["project_root"] == str(a)
    pid = r.json()["project"]["id"]
    got = client.get(f"/projects/{pid}")
    assert got.json()["project"]["project_root"] == str(a)


def test_create_without_project_root_is_none(tmp_path):
    client = _client(tmp_path)
    r = client.post("/projects", json={"title": "t", "origin": "o", "goal": "g"})
    assert r.status_code == 201
    assert r.json()["project"]["project_root"] is None


def test_create_with_missing_project_root_dir_returns_400(tmp_path):
    client = _client(tmp_path)
    r = client.post("/projects", json={
        "title": "t", "origin": "o", "goal": "g",
        "project_root": str(tmp_path / "does-not-exist"),
    })
    assert r.status_code == 400
```

> 注:app 是模块级实例 `from cairn.server.app import app`(无工厂)。`db._db_path` 是模块单例:测试先置 `None` 再 `db.configure(tmp_path)`,使 lifespan 的 `db.configure(DEFAULT_DB)` 因已配置而早返回 → tmp DB 生效。该 setup 与既有 `cairn/tests/test_server_api.py` 完全一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_create_project_root.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`services.py` 的 `project_meta_from_row`(`:206-215`)加回读。注意旧行可能无该键,用 `row.keys()` 守卫:

```python
def project_meta_from_row(row: sqlite3.Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        bootstrap_enabled=bool(row["bootstrap_enabled"]),
        backend=row["backend"],
        created_at=row["created_at"],
        reason=project_reason_from_row(row),
        project_root=row["project_root"] if "project_root" in row.keys() else None,
    )
```

`projects.py` 顶部加 import:

```python
import os
```

`create_project`(`:80`)在 `now = utcnow()` 之后、INSERT 之前加校验:

```python
        if body.project_root is not None and not os.path.isdir(body.project_root):
            raise HTTPException(400, f"project_root is not an existing directory: {body.project_root}")
```

把 projects 的 INSERT(`:85-89`)改为带 `project_root`:

```python
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, backend, created_at, project_root) "
            "VALUES (?, ?, 'active', ?, ?, ?, ?)",
            (pid, body.title, body.bootstrap_enabled, body.backend, now, body.project_root),
        )
```

把返回的 `ProjectMeta(...)`(`:110-118`)加 `project_root=body.project_root`:

```python
            project=ProjectMeta(
                id=pid,
                title=body.title,
                status="active",
                bootstrap_enabled=body.bootstrap_enabled,
                backend=body.backend,
                created_at=now,
                reason=None,
                project_root=body.project_root,
            ),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_create_project_root.py -v`
Expected: PASS。回归:`cd cairn && uv run pytest tests/ -q -k "project or services"`。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/server/routers/projects.py cairn/src/cairn/server/services.py cairn/tests/test_create_project_root.py
git commit -m "feat(B): persist + validate + read back project_root on create"
```

---

### Task 4: 运行时只读挂载/软链接 + 协议 + fakes

**Files:**
- Modify: `cairn/src/cairn/dispatcher/runtime/base.py:13`（协议签名）
- Modify: `cairn/src/cairn/dispatcher/runtime/containers.py:40-77`（`ensure_running` / `_ensure_running_locked`）
- Modify: `cairn/src/cairn/dispatcher/runtime/local/runtime.py:29-35`（`ensure_running`）
- Modify: `cairn/tests/conftest.py:103`、`cairn/tests/test_mock_end_to_end.py:152`、`cairn/tests/test_chat_service.py:27`（fake 签名）
- Test: `cairn/tests/test_runtime_project_root.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_runtime_project_root.py`:

```python
from __future__ import annotations

import os

import docker
import pytest

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.local.runtime import LocalRuntime


# ---- docker: read-only bind mount at container creation ----
class _FakeContainers:
    def __init__(self):
        self.run_calls = []
    def run(self, image, command, **kwargs):
        self.run_calls.append({"image": image, "command": command, **kwargs})
        return object()


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()
    def close(self):
        pass


def _cm(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(docker, "from_env", lambda: fake)
    cfg = ContainerConfig(image="img:test", network_mode="none", completed_action="stop")
    cm = ContainerManager(cfg)
    monkeypatch.setattr(cm, "inspect_state", lambda name: None)  # force "create" path
    return cm, fake


def test_ensure_running_adds_readonly_volume_when_project_root(monkeypatch, tmp_path):
    a = tmp_path / "A"; a.mkdir()
    cm, fake = _cm(monkeypatch)
    cm.ensure_running("p1", str(a))
    kwargs = fake.containers.run_calls[0]
    assert kwargs["volumes"] == {str(a): {"bind": "/home/kali/workspace/project", "mode": "ro"}}


def test_ensure_running_no_volume_when_no_project_root(monkeypatch):
    cm, fake = _cm(monkeypatch)
    cm.ensure_running("p1")
    assert "volumes" not in fake.containers.run_calls[0] or fake.containers.run_calls[0]["volumes"] is None


# ---- local: symlink project -> A ----
def test_local_ensure_running_symlinks_project_root(tmp_path):
    a = tmp_path / "A"; a.mkdir()
    (a / "src-repo").mkdir()
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = rt.ensure_running("p1", str(a))
    link = os.path.join(ws, "project")
    assert os.path.islink(link)
    assert os.path.realpath(link) == os.path.realpath(str(a))
    assert os.path.isdir(os.path.join(link, "src-repo"))


def test_local_ensure_running_no_symlink_without_project_root(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = rt.ensure_running("p1")
    assert not os.path.exists(os.path.join(ws, "project"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_runtime_project_root.py -v`
Expected: FAIL（`ensure_running` 不接受第二参 / 无 volumes / 无 symlink）。

- [ ] **Step 3: 实现**

`base.py:13` 协议:

```python
    def ensure_running(self, project_id: str, project_root: str | None = None) -> str: ...
```

`containers.py` 常量(类体内,与其它常量同处,如 `_PREFIX` 旁)加挂载点常量:

```python
    _PROJECT_MOUNT = "/home/kali/workspace/project"
```

`ensure_running` / `_ensure_running_locked`(`:40-77`)改为透传 `project_root`,仅在创建分支加 `volumes`:

```python
    def ensure_running(self, project_id: str, project_root: str | None = None) -> str:
        name = self.container_name(project_id)
        with self._ensure_running_lock(name):
            return self._ensure_running_locked(project_id, name, project_root)

    def _ensure_running_locked(self, project_id: str, name: str, project_root: str | None = None) -> str:
        state = self.inspect_state(name)
        if state == "running":
            LOG.debug("container already running project=%s container=%s", project_id, name)
            return name
        if state is not None:
            LOG.info("starting existing container project=%s container=%s state=%s", project_id, name, state)
            self._start_existing(name)
            return name
        LOG.info("creating container project=%s container=%s image=%s", project_id, name, self._config.image)
        run_kwargs = dict(
            detach=True,
            name=name,
            network_mode=self._config.network_mode,
            cap_add=self._config.cap_add or None,
        )
        if project_root:
            run_kwargs["volumes"] = {project_root: {"bind": self._PROJECT_MOUNT, "mode": "ro"}}
        try:
            self._client.containers.run(
                self._config.image,
                ["sleep", "infinity"],
                **run_kwargs,
            )
            LOG.info("created container project=%s container=%s", project_id, name)
            return name
        except APIError as exc:
            if not self._is_name_conflict(exc):
                raise RuntimeError(f"failed to create container {name}: {exc}") from exc
        LOG.info("container name conflict, reusing existing container project=%s container=%s", project_id, name)
        state = self.inspect_state(name)
        if state == "running":
            return name
        if state is not None:
            LOG.info("starting conflicted existing container project=%s container=%s state=%s", project_id, name, state)
            self._start_existing(name)
            return name
        raise RuntimeError(f"failed to create container {name}")
```

`local/runtime.py:29-35` `ensure_running`:

```python
    def ensure_running(self, project_id: str, project_root: str | None = None) -> str:
        ws = self._workspace(project_id)
        first = not ws.exists()
        ws.mkdir(parents=True, exist_ok=True)
        if first:
            self._seed_agent_config(ws)
        if project_root:
            link = ws / "project"
            if not link.exists() and not link.is_symlink():
                try:
                    link.symlink_to(project_root, target_is_directory=True)
                except OSError:
                    pass  # knowledge is an enhancement, not required; don't fail the task
        return str(ws)
```

fakes(都加可选第二参,行为不变):
- `conftest.py:103`:`def ensure_running(self, project_id: str, project_root: str | None = None) -> str:`
- `test_mock_end_to_end.py:152`:`def ensure_running(self, project_id: str, project_root: str | None = None) -> str:`
- `test_chat_service.py:27`:`def ensure_running(self, key, project_root=None): return "/tmp/ws"`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_runtime_project_root.py tests/test_local_runtime.py tests/test_runtime_protocol.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/dispatcher/runtime/base.py cairn/src/cairn/dispatcher/runtime/containers.py cairn/src/cairn/dispatcher/runtime/local/runtime.py cairn/tests/conftest.py cairn/tests/test_mock_end_to_end.py cairn/tests/test_chat_service.py cairn/tests/test_runtime_project_root.py
git commit -m "feat(B): read-only mount project_root at ./project (docker bind + local symlink)"
```

---

### Task 5: `format_project_knowledge`

**Files:**
- Modify: `cairn/src/cairn/dispatcher/prompting.py:35-47` 后（新增函数）
- Test: `cairn/tests/test_format_project_knowledge.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_format_project_knowledge.py`:

```python
from cairn.dispatcher.prompting import format_project_knowledge


def test_empty_when_no_root():
    assert format_project_knowledge(None, []) == ""
    assert format_project_knowledge("/data/A", []) == ""


def test_lists_only_present_subdirs_with_usage():
    out = format_project_knowledge("/data/A", ["src-repo", "codegraph-out", "graphify-out"])
    assert "./project/src-repo" in out
    assert "codegraph" in out and "./project/codegraph-out" in out
    assert "graphify query" in out and "./project/graphify-out" in out
    # absent ones must not appear
    assert "scan-out" not in out
    assert "docs-out" not in out
    # reuse directive present
    assert "do NOT redo" in out or "Reuse" in out


def test_unknown_subdir_ignored():
    out = format_project_knowledge("/data/A", ["weird", "docs-out"])
    assert "./project/docs-out" in out
    assert "weird" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_format_project_knowledge.py -v`
Expected: FAIL（函数不存在）。

- [ ] **Step 3: 实现**

在 `prompting.py` 末尾（`format_skills` 之后）追加:

```python
# project knowledge subdir -> one-line usage directive (relative to ./project)
_PK_USAGE = {
    "src-repo": "source code: read / grep `./project/src-repo`",
    "codegraph-out": "code graph: query with the `codegraph` CLI (query / explore / node / callers / impact) over `./project/codegraph-out`",
    "graphify-out": "domain knowledge graph: run `graphify query \"<question>\"` over `./project/graphify-out`",
    "scan-out": "static scan findings: read `./project/scan-out`",
    "docs-out": "product docs: read `./project/docs-out`",
}
# canonical render order
_PK_ORDER = ["src-repo", "docs-out", "graphify-out", "scan-out", "codegraph-out"]


def format_project_knowledge(project_root, present_subdirs) -> str:
    if not project_root or not present_subdirs:
        return ""
    present = set(present_subdirs)
    items = [_PK_USAGE[name] for name in _PK_ORDER if name in present and name in _PK_USAGE]
    if not items:
        return ""
    lines = [
        "## Project Knowledge (prior analysis, read-only at ./project)",
        "Reuse these prior results to gain context; do NOT redo the upfront analysis they already contain. "
        "If a query tool is missing, fall back to reading the files directly.",
        "",
    ]
    lines += [f"- {item}" for item in items]
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_format_project_knowledge.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/dispatcher/prompting.py cairn/tests/test_format_project_knowledge.py
git commit -m "feat(B): format_project_knowledge directive renderer"
```

---

### Task 6: `prepare_project_knowledge`（探测 A 的子目录）

**Files:**
- Modify: `cairn/src/cairn/dispatcher/tasks/common.py:16`（import）、`:27-38` 后（新增函数）
- Test: `cairn/tests/test_prepare_project_knowledge.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_prepare_project_knowledge.py`:

```python
from cairn.dispatcher.tasks.common import prepare_project_knowledge


def test_none_root_returns_empty():
    assert prepare_project_knowledge(None) == ""


def test_probes_existing_subdirs(tmp_path):
    a = tmp_path / "A"
    (a / "src-repo").mkdir(parents=True)
    (a / "graphify-out").mkdir()
    # scan-out / docs-out / codegraph-out absent
    out = prepare_project_knowledge(str(a))
    assert "./project/src-repo" in out
    assert "./project/graphify-out" in out
    assert "scan-out" not in out
    assert "codegraph-out" not in out


def test_empty_when_no_known_subdirs(tmp_path):
    a = tmp_path / "A"
    (a / "unrelated").mkdir(parents=True)
    assert prepare_project_knowledge(str(a)) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_prepare_project_knowledge.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`common.py` import 行（`:16`,与 `format_skills` 同 import）改为:

```python
from cairn.dispatcher.prompting import format_skills, format_project_knowledge
```

在 `common.py` 顶部 import 区加(若尚无):

```python
import os
```

在 `prepare_skills`(`:27-38`)之后追加:

```python
# project root subdirs we probe for, in canonical layout
_PROJECT_KNOWLEDGE_SUBDIRS = ("src-repo", "docs-out", "graphify-out", "scan-out", "codegraph-out")


def prepare_project_knowledge(project_root: str | None) -> str:
    """Probe the project root A for known prior-analysis subdirs and render the
    {project_knowledge} prompt text. Empty when no root / no known subdirs.
    Probed on the dispatcher host (which is also the docker bind-mount source)."""
    if not project_root:
        return ""
    try:
        present = [name for name in _PROJECT_KNOWLEDGE_SUBDIRS
                   if os.path.isdir(os.path.join(project_root, name))]
    except OSError:
        return ""
    return format_project_knowledge(project_root, present)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_prepare_project_knowledge.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/dispatcher/tasks/common.py cairn/tests/test_prepare_project_knowledge.py
git commit -m "feat(B): prepare_project_knowledge probes A subdirs"
```

---

### Task 7: 接线三个 task + 五个模板占位

**Files:**
- Modify: `cairn/src/cairn/dispatcher/prompts/default/{bootstrap,bootstrap_conclude,reason,explore,explore_conclude}.md`（`{skills}` 旁加 `{project_knowledge}`）
- Modify: `cairn/src/cairn/dispatcher/tasks/bootstrap.py:56,111-114,320,322-325`
- Modify: `cairn/src/cairn/dispatcher/tasks/reason.py:18-30(import),56,123-137`
- Modify: `cairn/src/cairn/dispatcher/tasks/explore.py:54,109-121,326,328-341`
- Test: 既有 `cairn/tests/test_mock_end_to_end.py`（确保不破）+ 新增渲染断言

- [ ] **Step 1: 模板加占位**

五个模板里,把 `{skills}` 那一行下方各加一空行 + `{project_knowledge}`。各文件当前 `{skills}` 行号:`explore.md:30`、`bootstrap.md:41`、`reason.md:47`、`explore_conclude.md:33`、`bootstrap_conclude.md:44`。每处改成:

```
{skills}

{project_knowledge}
```

（即在 `{skills}` 行后插入空行与 `{project_knowledge}`。空占位渲染为 ""，多余空行无害。）

- [ ] **Step 2: 接线 import**

`reason.py` 的 `from cairn.dispatcher.tasks.common import (...)`(`:18-30`)在 `prepare_skills` 旁加 `prepare_project_knowledge`:

```python
from cairn.dispatcher.tasks.common import (
    prepare_skills,
    prepare_project_knowledge,
    ExecutionRecorder,
    ...
)
```

`explore.py`、`bootstrap.py` 同理:它们已 `from cairn.dispatcher.tasks.common import ...`(含 `prepare_skills`),把 `prepare_project_knowledge` 加入该 import 列表。先 `grep -n "prepare_skills" cairn/src/cairn/dispatcher/tasks/{explore,bootstrap}.py` 定位 import 块。

- [ ] **Step 3: bootstrap.py 接线**

execute 路径(`:56` 的 ensure_running):

```python
        container_name = container_manager.ensure_running(project.project.id, project.project.project_root)
```

render(`:111-114`)加 `project_knowledge`:

```python
        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "bootstrap.md"),
            {
                **_bootstrap_prompt_replacements(project),
                "skills": prepare_skills(container_manager, container_name),
                "project_knowledge": prepare_project_knowledge(project.project.project_root),
            },
        )
```

conclude 路径(`:320` ensure_running、`:322-325` render):

```python
    container_name = container_manager.ensure_running(project.project.id, project.project.project_root)

    prompt = render_prompt(
        load_prompt(config.runtime.prompt_group, "bootstrap_conclude.md"),
        {
            **_bootstrap_prompt_replacements(project),
            "skills": prepare_skills(container_manager, container_name),
            "project_knowledge": prepare_project_knowledge(project.project.project_root),
        },
    )
```

- [ ] **Step 4: reason.py 接线**

ensure_running(`:56`):

```python
        container_name = container_manager.ensure_running(project.project.id, project.project.project_root)
```

render(`:123-137`)在 `"skills": ...` 后加一行:

```python
                "skills": prepare_skills(container_manager, container_name),
                "project_knowledge": prepare_project_knowledge(project.project.project_root),
```

- [ ] **Step 5: explore.py 接线**

execute(`:54` ensure_running):

```python
        container_name = container_manager.ensure_running(project.project.id, project.project.project_root)
```

execute render(`:109-121`)在 `"skills": ...` 行后加:

```python
                "skills": prepare_skills(container_manager, container_name),
                "project_knowledge": prepare_project_knowledge(project.project.project_root),
```

conclude 路径只有 `project_id`(str),无 ProjectDetail。`:326` 的 `ensure_running(project_id)` **保持不变**(容器/工作区已在 execute 阶段建好,挂载/软链接已存在)。render(`:328-341`)需要 `project_knowledge`:用 `client` 回查 project_root:

```python
    container_name = container_manager.ensure_running(project_id)

    prompt = render_prompt(
        load_prompt(config.runtime.prompt_group, "explore_conclude.md"),
        {
            "graph_yaml": write_graph_snapshot_reference(
                container_manager,
                container_name,
                export_yaml.strip(),
                phase="explore_conclude",
            ),
            "intent_id": intent.id,
            "intent_description": intent.description,
            "skills": prepare_skills(container_manager, container_name),
            "project_knowledge": prepare_project_knowledge(
                client.get_project(project_id).project.project_root
            ),
        },
    )
```

> 确认 conclude 函数体内有 `client` 形参(该路径上方已多次调用 `best_effort_release(client, ...)`、`project_allows_conclude_fallback(client, ...)`,故 `client` 在作用域内)。`client.get_project` 返回 `ProjectDetail`,其 `.project.project_root` 即 A(Task 2/3 已让它回带)。

- [ ] **Step 6: 写渲染回归测试**

Create `cairn/tests/test_task_project_knowledge_wiring.py`:

```python
from cairn.dispatcher.prompting import load_prompt, render_prompt


def test_all_templates_have_project_knowledge_placeholder():
    for name in ("bootstrap", "bootstrap_conclude", "reason", "explore", "explore_conclude"):
        tpl = load_prompt("default", name + ".md")
        assert "{project_knowledge}" in tpl, name


def test_empty_project_knowledge_renders_clean():
    tpl = load_prompt("default", "reason.md")
    out = render_prompt(tpl, {
        "graph_yaml": "g", "fact_ids": "[]", "open_intents": "[]",
        "max_intents": "3", "skills": "", "project_knowledge": "",
    })
    assert "{project_knowledge}" not in out
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_task_project_knowledge_wiring.py tests/test_mock_end_to_end.py -v`
Expected: PASS（mock e2e 既有断言不破；新增渲染断言通过）。

- [ ] **Step 8: 提交**

```bash
git add cairn/src/cairn/dispatcher/prompts/default/bootstrap.md cairn/src/cairn/dispatcher/prompts/default/bootstrap_conclude.md cairn/src/cairn/dispatcher/prompts/default/reason.md cairn/src/cairn/dispatcher/prompts/default/explore.md cairn/src/cairn/dispatcher/prompts/default/explore_conclude.md cairn/src/cairn/dispatcher/tasks/bootstrap.py cairn/src/cairn/dispatcher/tasks/reason.py cairn/src/cairn/dispatcher/tasks/explore.py cairn/tests/test_task_project_knowledge_wiring.py
git commit -m "feat(B): inject {project_knowledge} + pass project_root in all tasks"
```

---

### Task 8: 前端 New Project 加 Project root 字段

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html:1219`(localEngine checkbox 后插入字段)、`:1528`(`newProject` 默认值)、`:3868-3879`(`createProject` body + reset)

- [ ] **Step 1: 表单加输入**

在 localEngine checkbox 这块(`:1218-1221` 一段 `<label>...</label>` 之后)插入:

```html
      <input x-model="newProject.projectRoot" placeholder="Project root (host path, optional) — e.g. /data/A"
        class="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-400 transition placeholder:text-slate-300 font-mono">
      <p class="text-[11px] text-slate-400 -mt-1">Read-only prior analysis (src-repo / docs-out / graphify-out / scan-out / codegraph-out) mounted at ./project.</p>
```

- [ ] **Step 2: newProject 默认值加字段**

`:1528` 改为(加 `projectRoot:''`):

```javascript
    newProject: { title: '', origin: '', goal: '', bootstrap: true, localEngine: false, projectRoot: '', hints: [{ content: '' }] },
```

- [ ] **Step 3: createProject 提交 + reset**

`createProject` body(`:3868-3874`)在 `backend` 后加 project_root(仅非空时):

```javascript
        const body = {
          title: this.newProject.title,
          origin: this.newProject.origin,
          goal: this.newProject.goal,
          bootstrap_enabled: this.newProject.bootstrap,
          backend: this.newProject.localEngine ? 'local' : 'docker'
        };
        const pr = this.newProject.projectRoot?.trim();
        if (pr) body.project_root = pr;
```

reset 行(`:3879`)同步加 `projectRoot:''`:

```javascript
        this.newProject = { title:'', origin:'', goal:'', bootstrap: true, localEngine: false, projectRoot:'', hints: [{ content:'' }] };
```

- [ ] **Step 4: 手测**

Run: `cd cairn && uv run cairn serve`(或既有启动方式),浏览器开 New Project:
- 填 Project root = 一个存在目录 → 创建成功,详情 `project.project_root` 正确。
- 填不存在目录 → toast 报错(400)。
- 留空 → 正常创建,`project_root` 为 null。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/server/static/index.html
git commit -m "feat(B): New Project form project_root field"
```

---

## Part 2 — Dep-1(Dockerfile 装工具)

### Task 9: Dockerfile 装 graphify + codegraph

**Files:**
- Modify: `container/Dockerfile`（在现有 agent npm 安装区附近追加,参考 `:67-70` 的 `sudo npm install -g`、`:18/:64` 的 `pip3 install ... --break-system-packages`）

- [ ] **Step 1: 追加安装层**

在 `container/Dockerfile` 现有 agent 安装行(`RUN sudo npm install -g opencode-ai@...` 之后)追加两行:

```dockerfile
RUN pip3 install graphifyy --break-system-packages
RUN sudo npm install -g @colbymchenry/codegraph
```

（graphify 的 PyPI 包名是 `graphifyy`,CLI 为 `graphify`;codegraph CLI 为 `codegraph`。本轮不接 MCP,故不加 `codegraph install`。）

- [ ] **Step 2: 构建验证(本地有 docker 时)**

Run: `docker build -t cairn-worker:pktest container/`（路径以仓库实际为准；先 `grep -n "image" cairn/dispatch*.yaml 2>/dev/null` 或查 ContainerConfig.image 默认确认镜像名/构建上下文)
Expected: 构建成功。验证:

```bash
docker run --rm cairn-worker:pktest sh -lc 'graphify --version && codegraph --version'
```
Expected: 各自打印版本(rc=0)。若无本地 docker,跳过构建,仅核对 Dockerfile 行正确并记录"需 CI/构建验证"。

- [ ] **Step 3: 提交**

```bash
git add container/Dockerfile
git commit -m "feat(B/dep1): install graphify + codegraph in worker image"
```

---

## Part 3 — Dep-2(本地工具探测 + /engines UX)

### Task 10: `resolve.TOOLS` + `probe_tool`

**Files:**
- Modify: `cairn/src/cairn/dispatcher/runtime/local/resolve.py:11`(加 `TOOLS`)、`:126-139` 后(加 `probe_tool`)
- Test: `cairn/tests/test_probe_tool.py`(Create)

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_probe_tool.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_probe_tool.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`resolve.py:11` 区(`BINARY` 旁)加:

```python
# host dependency tools the local engine needs (for project-knowledge queries)
TOOLS = ("graphify", "codegraph")
```

在 `probe_engine`(`:126-139`)之后追加,复用既有 helper(`augmented_path` / `_windows_candidates` / `_launcher_for` / `launch_argv` / `Resolved`):

```python
def probe_tool(name: str) -> dict:
    """Probe a bare host CLI tool (graphify / codegraph). Mirrors probe_engine
    but without override/BINARY mapping."""
    search = augmented_path(os.environ.get("PATH", ""))
    found = None
    if os.name == "nt":
        for cand in _windows_candidates(name):
            found = shutil.which(cand, path=search)
            if found:
                break
    else:
        found = shutil.which(name, path=search)
    if not found:
        return {"launchable": False, "path": None, "version": None}
    version, launchable = None, False
    try:
        argv = launch_argv(Resolved(path=found, launcher=_launcher_for(found), source="path"), ["--version"])
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        launchable = out.returncode == 0
        text = (out.stdout or out.stderr or "").strip()
        version = text.splitlines()[0] if text else None
    except (OSError, subprocess.SubprocessError):
        launchable = False
    return {"launchable": launchable, "path": found, "version": version}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_probe_tool.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/dispatcher/runtime/local/resolve.py cairn/tests/test_probe_tool.py
git commit -m "feat(B/dep2): resolve.probe_tool + TOOLS set"
```

---

### Task 11: `GET /tools` 端点

**Files:**
- Modify: `cairn/src/cairn/server/routers/engines.py:1-27`（加 `/tools`）
- Test: `cairn/tests/test_tools_endpoint.py`(Create)

> 确认 `engines.py` 的 router 已挂载到 app(它已有 `/engines`,同 router 加 `/tools` 即自动暴露)。

- [ ] **Step 1: 写失败测试**

Create `cairn/tests/test_tools_endpoint.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.runtime.local import resolve


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    return TestClient(app)


def test_tools_lists_both_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve, "probe_tool",
                        lambda name: {"launchable": name == "graphify",
                                      "path": "/x/" + name if name == "graphify" else None,
                                      "version": "v1" if name == "graphify" else None})
    client = _client(tmp_path)
    r = client.get("/tools")
    assert r.status_code == 200
    data = r.json()
    names = {t["name"]: t for t in data}
    assert set(names) == {"graphify", "codegraph"}
    assert names["graphify"]["launchable"] is True
    assert names["codegraph"]["launchable"] is False
```

> 与 Task 3 同一 client setup(`from cairn.server.app import app`)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd cairn && uv run pytest tests/test_tools_endpoint.py -v`
Expected: FAIL（404 无 `/tools`）。

- [ ] **Step 3: 实现**

`engines.py` import 加 `ToolInfo`:

```python
from cairn.server.models import EngineInfo, EngineOverride, ToolInfo
```

在 `list_engines`(`:25-27`)之后追加:

```python
@router.get("/tools", response_model=list[ToolInfo])
def list_tools():
    out = []
    for name in resolve.TOOLS:
        probe = resolve.probe_tool(name)
        out.append(ToolInfo(name=name, launchable=probe["launchable"],
                            version=probe["version"], path=probe["path"]))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd cairn && uv run pytest tests/test_tools_endpoint.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/server/routers/engines.py cairn/tests/test_tools_endpoint.py
git commit -m "feat(B/dep2): GET /tools dependency-tool readiness"
```

---

### Task 12: /engines 页分 Agents / Tools 两组 + 缺失告警

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html:428-491`（engines 视图正文）、`:1564-1565`（state 加 `tools`）、`:1618-1621`（`loadEngines` 同时拉 `/tools`）

- [ ] **Step 1: state 加 tools**

`:1564-1565` 区:

```javascript
    engines: [],
    tools: [],
    enginesLoading: false,
```

- [ ] **Step 2: loadEngines 同时拉 /tools**

`:1618-1621` 改为:

```javascript
    async loadEngines() {
      this.enginesLoading = true;
      try {
        const [re, rt] = await Promise.all([fetch('/engines'), fetch('/tools')]);
        if (re.ok) this.engines = await re.json();
        if (rt.ok) this.tools = await rt.json();
      } finally { this.enginesLoading = false; }
    },
```

- [ ] **Step 3: 视图加分组标题 + Tools 区**

把 engines 正文(`:428-491` 内 `max-w-3xl` 容器)调整为两组。在现有 `<p class="text-[11px] text-slate-400 mb-4">Reflects agents...` 之后、`<div class="space-y-3">`(Agents 列表)之前加一个 Agents 小标题;在 Agents 列表(以 `engines.length === 0` 模板收尾的 `</div>`)之后追加 Tools 区。具体:

在 `:430` 那段说明 `<p>` 后插入:

```html
      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Agents</h3>
```

在 Agents 列表块(`<div class="space-y-3"> ... </div>`,即 `:432-489`)的闭合 `</div>` 之后,追加 Tools 区:

```html
      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wide mt-8 mb-2">Tools (dependencies)</h3>
      <p class="text-[11px] text-slate-400 mb-3">Required on the host (Local mode) for project-knowledge queries. Install them yourself if missing.</p>
      <div class="space-y-3">
        <template x-for="t in tools" :key="t.name">
          <div class="bg-white rounded-2xl border p-5"
               :class="t.launchable ? 'border-slate-200/60' : 'border-amber-300'">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-[15px] font-semibold text-slate-700" x-text="t.name"></span>
              <span class="px-1.5 py-0.5 rounded-full text-[9px] font-semibold uppercase tracking-[0.12em]"
                :class="t.launchable ? 'bg-teal-50 text-teal-700 border border-teal-200' : 'bg-amber-50 text-amber-700 border border-amber-300'"
                x-text="t.launchable ? 'available' : 'not installed'"></span>
            </div>
            <div class="mt-2 space-y-1 text-[11px] text-slate-400">
              <template x-if="t.version"><div>version <span class="text-slate-600" x-text="t.version"></span></div></template>
              <template x-if="t.path"><div class="font-mono text-slate-500 break-all" x-text="t.path"></div></template>
            </div>
            <template x-if="!t.launchable">
              <div class="mt-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
                Not ready. Install:
                <span class="font-mono" x-text="t.name === 'graphify' ? 'uv tool install graphifyy' : 'npm i -g @colbymchenry/codegraph'"></span>
              </div>
            </template>
          </div>
        </template>
        <template x-if="!enginesLoading && tools.length === 0">
          <div class="text-center text-sm text-slate-400 py-6">No tools reported.</div>
        </template>
      </div>
```

- [ ] **Step 4: 手测**

Run: 启动 server,开 `#/engines`:
- Agents 组照旧列出 claude/codex/opencode/pi。
- Tools 组列出 graphify、codegraph。
- 本机已装 graphify → "available" + 版本;未装 codegraph → 琥珀色边框 "not installed" + 安装命令 `npm i -g @colbymchenry/codegraph`。
- Refresh 同时刷新两组。

- [ ] **Step 5: 提交**

```bash
git add cairn/src/cairn/server/static/index.html
git commit -m "feat(B/dep2): /engines page Agents/Tools groups + missing-tool warning"
```

---

## Part 4 — 文档

### Task 13: 文档增补

**Files:**
- Modify: `docs/specs/server-protocol.md`（新增 `project_root` 字段、`GET /tools`)
- Modify: `docs/specs/dispatcher-design.md`（`./project` 只读挂载 + `{project_knowledge}` + 依赖工具)
- Modify: `README`（若存在 quickstart:说明 project_root + 工具安装 `uv tool install graphifyy` / `npm i -g @colbymchenry/codegraph`)

- [ ] **Step 1: server-protocol**

在 `POST /projects` 请求体文档加可选 `project_root: string`(宿主机绝对路径,必须是存在目录,否则 400);`ProjectMeta` 响应加 `project_root`。新增 `GET /tools → [{name, launchable, version, path}]` 段落(依赖工具就绪探测,server 宿主机视角)。

- [ ] **Step 2: dispatcher-design**

加一节"项目知识注入":A 只读挂载到 `./project`(docker bind-mount ro / local symlink);dispatcher 探测 A 子目录渲染 `{project_knowledge}`;`ensure_running(project_id, project_root)` 在创建时挂载;依赖工具 graphify/codegraph(docker 装入镜像,local 探测+告警)。

- [ ] **Step 3: 提交**

```bash
git add docs/specs/server-protocol.md docs/specs/dispatcher-design.md
git commit -m "docs(B): project_root, ./project mount, /tools, dep tools"
```

---

## 全量回归 + 收尾

- [ ] **Step 1: 全量测试**

Run: `cd cairn && uv run pytest -q`
Expected: 全绿(含既有 172+ 与本计划新增)。

- [ ] **Step 2: 核对未污染**

Run: `git status` —— 确认无 `graphify-out/` 等生成物被暂存;只含本计划文件。

- [ ] **Step 3: 通知用户**(Telegram)实现完成、待人工验收(docker 构建 + local 工具探测 + 端到端挂载)。
