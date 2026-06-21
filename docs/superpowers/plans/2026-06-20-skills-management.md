# Skills Management + Workspace Delivery (F+A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let docker + local worker agents use a platform-managed set of skills (CRUD via a `/skills` page): enabled skills are delivered into the agent workspace `.claude/skills/` at task start and advertised in the prompt so the agent prefers them.

**Architecture:** A shared `skills_store` module (filesystem under `~/.cairn/skills/`) used by both server (CRUD) and dispatcher (injection). A `Runtime.install_skills` method delivers skill dirs into the workspace (docker via tar `put_archive`, local via `copytree`). `prompting.format_skills` renders an enabled-skills catalog into a new `{skills}` prompt placeholder.

**Tech Stack:** Python 3, FastAPI, sqlite-free filesystem store, pytest, AlpineJS (`index.html`), docker-py.

**Spec:** `docs/superpowers/specs/2026-06-20-skills-management-design.md`

**Conventions:** tests via `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest`. Commit after each task. Prefer `graphify query` before grepping when exploring. Branch: `feat/skills-management`.

**Verified facts:** container WORKDIR is `/home/kali/workspace` (`container/Dockerfile`). claude auto-discovers `.claude/skills/`; codex/opencode have no native skills; pi has `--skill` but the driver passes `--no-skills` (out of scope this round). `render_prompt` does simple `{key}` replacement. `yaml` (PyYAML) is already a dependency (used by config loader).

---

### Task 1: `skills_store` shared module

Filesystem CRUD for skills under `~/.cairn/skills/`, self-contained (no server/dispatcher import), so both sides can use it.

**Files:**
- Create: `cairn/src/cairn/skills_store.py`
- Test: `cairn/tests/test_skills_store.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_skills_store.py
from __future__ import annotations

import io
import zipfile

import pytest

from cairn import skills_store


def _point(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    return tmp_path / "skills"


def _skill_md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\nbody\n"


def test_root_follows_cairn_home(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    assert skills_store.skills_root() == tmp_path / "skills"


def test_seed_if_empty_copies_repo_skills(monkeypatch, tmp_path):
    root = _point(monkeypatch, tmp_path)
    repo = tmp_path / "repo_skills"
    (repo / "decompile").mkdir(parents=True)
    (repo / "decompile" / "SKILL.md").write_text(_skill_md("decompile", "reverse"), encoding="utf-8")
    skills_store.seed_if_empty(repo)
    assert (root / "decompile" / "SKILL.md").exists()
    # second call is a no-op (not empty anymore)
    (repo / "decompile" / "SKILL.md").write_text("changed", encoding="utf-8")
    skills_store.seed_if_empty(repo)
    assert "changed" not in (root / "decompile" / "SKILL.md").read_text(encoding="utf-8")


def test_create_list_read_enabled_delete(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    skills_store.create_skill("graphify", _skill_md("graphify", "knowledge graph"))
    metas = skills_store.list_skills()
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "graphify" and m.description == "knowledge graph" and m.enabled is True
    assert "knowledge graph" in skills_store.read_skill_md("graphify")
    skills_store.set_enabled("graphify", False)
    assert skills_store.list_skills()[0].enabled is False
    assert skills_store.enabled_skill_dirs() == []
    skills_store.set_enabled("graphify", True)
    assert [p.name for p in skills_store.enabled_skill_dirs()] == ["graphify"]
    skills_store.delete_skill("graphify")
    assert skills_store.list_skills() == []


def test_corrupt_registry_tolerated(monkeypatch, tmp_path):
    root = _point(monkeypatch, tmp_path)
    skills_store.create_skill("a", _skill_md("a", "x"))
    (root / ".registry.json").write_text("{ broken", encoding="utf-8")
    assert skills_store.list_skills()[0].enabled is True  # default enabled


def test_import_zip(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mytool/SKILL.md", _skill_md("mytool", "does things"))
        zf.writestr("mytool/scripts/run.sh", "echo hi\n")
    name = skills_store.import_zip(buf.getvalue())
    assert name == "mytool"
    assert "does things" in skills_store.read_skill_md("mytool")
    assert (skills_store.skills_root() / "mytool" / "scripts" / "run.sh").exists()


def test_rejects_bad_names(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        skills_store.create_skill("../evil", "x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skills_store.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `cairn/src/cairn/skills_store.py`**

```python
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REGISTRY = ".registry.json"


@dataclass(slots=True)
class SkillMeta:
    name: str
    description: str
    enabled: bool
    path: str


def _cairn_home() -> Path:
    override = os.environ.get("CAIRN_HOME")
    return Path(override).expanduser() if override else Path.home() / ".cairn"


def skills_root() -> Path:
    return _cairn_home() / "skills"


def _validate_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid skill name: {name!r}")
    return name


def _registry_path() -> Path:
    return skills_root() / _REGISTRY


def _load_registry() -> dict:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(data: dict) -> None:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = _registry_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _registry_path())


def seed_if_empty(repo_skills_dir: Path) -> None:
    root = skills_root()
    if root.exists() and any(p.is_dir() for p in root.iterdir()):
        return
    if not Path(repo_skills_dir).is_dir():
        return
    root.mkdir(parents=True, exist_ok=True)
    for child in Path(repo_skills_dir).iterdir():
        if child.is_dir() and (child / "SKILL.md").is_file():
            shutil.copytree(child, root / child.name, dirs_exist_ok=True)


def _parse_description(skill_md_path: Path) -> str:
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return ""
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return ""
    desc = meta.get("description", "") if isinstance(meta, dict) else ""
    return str(desc) if desc else ""


def list_skills() -> list[SkillMeta]:
    root = skills_root()
    if not root.is_dir():
        return []
    reg = _load_registry()
    out: list[SkillMeta] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        enabled = bool(reg.get(child.name, {}).get("enabled", True))
        out.append(SkillMeta(name=child.name, description=_parse_description(skill_md),
                             enabled=enabled, path=str(child)))
    return out


def _skill_dir(name: str) -> Path:
    return skills_root() / _validate_name(name)


def read_skill_md(name: str) -> str:
    path = _skill_dir(name) / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def write_skill_md(name: str, content: str) -> None:
    d = _skill_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")


def create_skill(name: str, skill_md: str) -> None:
    d = _skill_dir(name)
    if d.exists():
        raise ValueError(f"skill already exists: {name}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")


def delete_skill(name: str) -> None:
    shutil.rmtree(_skill_dir(name), ignore_errors=True)
    reg = _load_registry()
    if name in reg:
        del reg[name]
        _save_registry(reg)


def set_enabled(name: str, enabled: bool) -> None:
    _validate_name(name)
    reg = _load_registry()
    reg.setdefault(name, {})["enabled"] = bool(enabled)
    _save_registry(reg)


def import_zip(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.startswith("/") and ".." not in n]
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) != 1:
            raise ValueError("zip must contain exactly one top-level skill directory")
        skill_name = _validate_name(next(iter(tops)))
        if not any(n == f"{skill_name}/SKILL.md" for n in names):
            raise ValueError("zip skill directory must contain SKILL.md")
        dest = skills_root() / skill_name
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        for n in names:
            if n.endswith("/"):
                continue
            rel = n.split("/", 1)[1] if "/" in n else n
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(n))
    return skill_name


def enabled_skill_dirs() -> list[Path]:
    return [Path(m.path) for m in list_skills() if m.enabled]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skills_store.py -v` → PASS (6).
Full suite: `uv run pytest -q 2>&1 | tail -3` → green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/skills_store.py cairn/tests/test_skills_store.py
git commit -m "feat: skills_store (filesystem CRUD for ~/.cairn/skills)"
```

---

### Task 2: Server models for skills

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Test: `cairn/tests/test_skill_models.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_skill_models.py
from cairn.server.models import SkillInfo, SkillContent, SkillCreate, SkillEnable


def test_skill_info():
    s = SkillInfo(name="decompile", description="reverse", enabled=True)
    assert s.enabled is True


def test_skill_content_and_create():
    assert SkillContent(name="a", content="x").content == "x"
    assert SkillCreate(name="a", content="x").name == "a"


def test_skill_enable():
    assert SkillEnable(enabled=False).enabled is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skill_models.py -v` → FAIL.

- [ ] **Step 3: Implement (add near the Engine* models in `models.py`)**

```python
class SkillInfo(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True


class SkillContent(BaseModel):
    name: str
    content: str


class SkillCreate(BaseModel):
    name: str
    content: str


class SkillEnable(BaseModel):
    enabled: bool
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skill_models.py -v` → PASS (3). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/models.py cairn/tests/test_skill_models.py
git commit -m "feat: skill pydantic models"
```

---

### Task 3: Skills router + app wiring + seed

**Files:**
- Create: `cairn/src/cairn/server/routers/skills.py`
- Modify: `cairn/src/cairn/server/app.py`
- Test: `cairn/tests/test_skills_router.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_skills_router.py
from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from cairn import skills_store
from cairn.server import db
from cairn.server.app import app


def _client(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    return TestClient(app)


def _md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\nbody\n"


def test_crud_flow(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/skills").json() == []
    assert c.post("/skills", json={"name": "decompile", "content": _md("decompile", "reverse")}).status_code == 201
    lst = c.get("/skills").json()
    assert lst[0]["name"] == "decompile" and lst[0]["description"] == "reverse" and lst[0]["enabled"] is True
    assert "reverse" in c.get("/skills/decompile").json()["content"]
    assert c.put("/skills/decompile", json={"name": "decompile", "content": _md("decompile", "edited")}).status_code == 200
    assert c.get("/skills")[0].json if False else c.get("/skills").json()[0]["description"] == "edited"
    assert c.put("/skills/decompile/enabled", json={"enabled": False}).status_code == 200
    assert c.get("/skills").json()[0]["enabled"] is False
    assert c.delete("/skills/decompile").status_code == 200
    assert c.get("/skills").json() == []


def test_bad_name_rejected(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/skills", json={"name": "../evil", "content": "x"}).status_code == 400


def test_upload_zip(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mytool/SKILL.md", _md("mytool", "ziptool"))
    r = c.post("/skills/upload", files={"file": ("mytool.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201 and r.json()["name"] == "mytool"
    assert c.get("/skills").json()[0]["name"] == "mytool"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skills_router.py -v` → FAIL (404s).

- [ ] **Step 3: Implement router + wiring**

```python
# cairn/src/cairn/server/routers/skills.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from cairn import skills_store
from cairn.server.models import SkillContent, SkillCreate, SkillEnable, SkillInfo

router = APIRouter(tags=["skills"])


def _info(meta: skills_store.SkillMeta) -> SkillInfo:
    return SkillInfo(name=meta.name, description=meta.description, enabled=meta.enabled)


def _find(name: str) -> skills_store.SkillMeta:
    for m in skills_store.list_skills():
        if m.name == name:
            return m
    raise HTTPException(404, f"skill not found: {name}")


@router.get("/skills", response_model=list[SkillInfo])
def list_skills():
    return [_info(m) for m in skills_store.list_skills()]


@router.get("/skills/{name}", response_model=SkillContent)
def get_skill(name: str):
    _find(name)
    return SkillContent(name=name, content=skills_store.read_skill_md(name))


@router.post("/skills", status_code=201, response_model=SkillInfo)
def create_skill(body: SkillCreate):
    try:
        skills_store.create_skill(body.name, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _info(_find(body.name))


@router.put("/skills/{name}", response_model=SkillInfo)
def update_skill(name: str, body: SkillContent):
    _find(name)
    skills_store.write_skill_md(name, body.content)
    return _info(_find(name))


@router.put("/skills/{name}/enabled", response_model=SkillInfo)
def set_enabled(name: str, body: SkillEnable):
    _find(name)
    skills_store.set_enabled(name, body.enabled)
    return _info(_find(name))


@router.delete("/skills/{name}", status_code=200)
def delete_skill(name: str):
    _find(name)
    skills_store.delete_skill(name)
    return {"deleted": name}


@router.post("/skills/upload", status_code=201, response_model=SkillInfo)
async def upload_skill(file: UploadFile):
    data = await file.read()
    try:
        name = skills_store.import_zip(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _info(_find(name))
```

In `app.py`:
1. Add `skills` to the routers import line and `app.include_router(skills.router)`.
2. In the `lifespan` function (after `db.configure(...)`), seed skills from the repo `skills/` dir:
```python
    from cairn import skills_store
    repo_skills = Path(__file__).resolve().parents[4] / "skills"
    skills_store.seed_if_empty(repo_skills)
```
VERIFY the `parents[4]` index resolves to the repo root containing `skills/` (file is `cairn/src/cairn/server/app.py`; print `Path(__file__).resolve().parents[i]` and pick the one whose `/skills` dir is the repo's). `Path` is imported in app.py.

NOTE the test on the `update_skill` line in Step 1 has an intentional simplification — if your test runner flags the `c.get("/skills")[0].json if False else ...` ternary as confusing, replace that whole line with:
```python
    assert c.get("/skills").json()[0]["description"] == "edited"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_skills_router.py -v` → PASS (3). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/routers/skills.py cairn/src/cairn/server/app.py cairn/tests/test_skills_router.py
git commit -m "feat: /skills endpoints (CRUD + enable + zip upload) + seed"
```

---

### Task 4: `Runtime.install_skills` (docker + local)

**Files:**
- Modify: `cairn/src/cairn/dispatcher/runtime/base.py` (protocol)
- Modify: `cairn/src/cairn/dispatcher/runtime/containers.py` (put_archive)
- Modify: `cairn/src/cairn/dispatcher/runtime/local/runtime.py` (copytree)
- Modify: test fakes (`cairn/tests/conftest.py`, `cairn/tests/test_mock_end_to_end.py`, `cairn/tests/test_runtime_protocol.py` `_FakeRuntime`) to add `install_skills`
- Test: `cairn/tests/test_install_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_install_skills.py
from __future__ import annotations

from pathlib import Path

from cairn.dispatcher.runtime.local.runtime import LocalRuntime


def test_local_install_skills_copies_into_dot_claude(tmp_path):
    src = tmp_path / "src" / "decompile"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("hi", encoding="utf-8")
    (src / "scripts" / "run.sh").write_text("echo", encoding="utf-8")
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = Path(rt.ensure_running("p1"))
    rt.install_skills("p1", [src])
    assert (ws / ".claude" / "skills" / "decompile" / "SKILL.md").read_text() == "hi"
    assert (ws / ".claude" / "skills" / "decompile" / "scripts" / "run.sh").exists()


def test_local_install_skills_empty_noop(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    rt.ensure_running("p1")
    rt.install_skills("p1", [])  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_install_skills.py -v` → FAIL (no `install_skills`).

- [ ] **Step 3: Implement**

(a) `runtime/base.py` — add to the `Runtime` Protocol:
```python
    def install_skills(self, workspace_key: str, skill_dirs: list) -> None: ...
```

(b) `runtime/local/runtime.py` `LocalRuntime` — add (uses `shutil`, already imported; `Path` imported):
```python
    def install_skills(self, name: str, skill_dirs: list) -> None:
        ws = self._workspace_for_key(name)
        dest_root = ws / ".claude" / "skills"
        for src in skill_dirs:
            src = Path(src)
            if not src.is_dir():
                continue
            shutil.copytree(src, dest_root / src.name, dirs_exist_ok=True)
```

(c) `runtime/containers.py` `ContainerManager` — add (uses `io`, `tarfile`; add `import io`, `import tarfile` at top if absent). Container WORKDIR is `/home/kali/workspace`:
```python
    _WORKSPACE_DIR = "/home/kali/workspace"

    def install_skills(self, container_name: str, skill_dirs: list) -> None:
        skill_dirs = [Path(s) for s in skill_dirs if Path(s).is_dir()]
        if not skill_dirs:
            return
        container = self._require_container(container_name)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for src in skill_dirs:
                tar.add(str(src), arcname=f".claude/skills/{src.name}")
        buf.seek(0)
        # ensure the dest dir exists, then extract the tar there
        container.exec_run(["mkdir", "-p", f"{self._WORKSPACE_DIR}/.claude/skills"], stdout=False, stderr=False)
        try:
            ok = container.put_archive(self._WORKSPACE_DIR, buf.getvalue())
        except DockerException as exc:
            raise RuntimeError(f"failed to install skills: {exc}") from exc
        if not ok:
            raise RuntimeError("failed to install skills (put_archive returned False)")
```
(`Path` is imported in containers.py; `DockerException` is imported there. Add `import io` and `import tarfile` if not already present.)

(d) Test fakes — add a no-op `install_skills(self, *a, **k): pass` (or `def install_skills(self, key, skill_dirs): pass`) to: `FakeContainerManager` (in `cairn/tests/conftest.py`), `LocalContainerManager` (in `cairn/tests/test_mock_end_to_end.py`), and `_FakeRuntime` (in `cairn/tests/test_runtime_protocol.py`). READ each file to add the method consistent with its style. (These mirror the `snapshot_root` addition from a prior task.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_install_skills.py -v` → PASS (2). Full suite green (the fakes now satisfy the new protocol method used in Task 6).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/base.py cairn/src/cairn/dispatcher/runtime/containers.py cairn/src/cairn/dispatcher/runtime/local/runtime.py cairn/tests/conftest.py cairn/tests/test_mock_end_to_end.py cairn/tests/test_runtime_protocol.py cairn/tests/test_install_skills.py
git commit -m "feat: Runtime.install_skills (docker tar put_archive + local copytree)"
```

---

### Task 5: `format_skills` + `{skills}` prompt placeholder

**Files:**
- Modify: `cairn/src/cairn/dispatcher/prompting.py`
- Modify: `cairn/src/cairn/dispatcher/prompts/default/{bootstrap,bootstrap_conclude,reason,explore,explore_conclude}.md`
- Test: `cairn/tests/test_format_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_format_skills.py
from cairn.dispatcher.prompting import format_skills


class _M:
    def __init__(self, name, desc): self.name = name; self.description = desc


def test_format_skills_empty():
    assert format_skills([]) == ""


def test_format_skills_lists_name_desc_path():
    out = format_skills([_M("decompile", "reverse binaries"), _M("graphify", "kg")])
    assert "decompile" in out and "reverse binaries" in out
    assert ".claude/skills/decompile/SKILL.md" in out
    assert "prefer" in out.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_format_skills.py -v` → FAIL.

- [ ] **Step 3: Implement**

(a) `prompting.py` — add:
```python
def format_skills(skills) -> str:
    if not skills:
        return ""
    lines = [
        "## Available Skills (prefer these)",
        "You have these skills installed at .claude/skills/<name>/SKILL.md. When a task matches "
        "a skill, READ its SKILL.md and follow it; prefer these skills over ad-hoc approaches.",
        "",
    ]
    for s in skills:
        desc = (s.description or "").strip()
        lines.append(f"- {s.name}: {desc}  (.claude/skills/{s.name}/SKILL.md)")
    return "\n".join(lines)
```

(b) Add a `{skills}` placeholder to each template. Read each file and insert `{skills}` on its own line in the Context/instructions area (e.g. just after the Origin/Goal/Graph context block, before the task instructions). Example for `explore.md` — add a line:
```

{skills}
```
Do this for all five: `bootstrap.md`, `bootstrap_conclude.md`, `reason.md`, `explore.md`, `explore_conclude.md`. (Since `render_prompt` does a plain `{key}` replace and Task 6 always supplies a `skills` value — empty string when none — an empty placeholder renders to nothing.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_format_skills.py -v` → PASS (2). Full suite green (existing prompt-render tests must still pass; if any asserts exact template text, update it to include the new placeholder/section).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/prompting.py cairn/src/cairn/dispatcher/prompts/default/*.md cairn/tests/test_format_skills.py
git commit -m "feat: format_skills + {skills} prompt placeholder"
```

---

### Task 6: Wire skills into the task layer

**Files:**
- Modify: `cairn/src/cairn/dispatcher/tasks/common.py` (`prepare_skills` helper)
- Modify: `cairn/src/cairn/dispatcher/tasks/{bootstrap,reason,explore}.py` (call it + pass `{skills}`)
- Test: `cairn/tests/test_prepare_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_prepare_skills.py
from __future__ import annotations

from pathlib import Path

from cairn.dispatcher.tasks import common
from cairn import skills_store


class _Runtime:
    def __init__(self): self.installed = None
    def install_skills(self, key, dirs): self.installed = (key, dirs)


def test_prepare_skills_installs_and_describes(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    skills_store.create_skill("decompile", "---\nname: decompile\ndescription: reverse\n---\n")
    rt = _Runtime()
    text = common.prepare_skills(rt, "p1")
    assert rt.installed[0] == "p1"
    assert [Path(d).name for d in rt.installed[1]] == ["decompile"]
    assert "decompile" in text and "reverse" in text


def test_prepare_skills_none(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    rt = _Runtime()
    assert common.prepare_skills(rt, "p1") == ""
    assert rt.installed == ("p1", [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_prepare_skills.py -v` → FAIL.

- [ ] **Step 3: Implement**

(a) `common.py` — add imports + helper:
```python
from cairn import skills_store
from cairn.dispatcher.prompting import format_skills


def prepare_skills(runtime, workspace_key: str) -> str:
    """Install enabled skills into the workspace and return the {skills} prompt text.
    Best-effort: failures to install must not break the task."""
    metas = [m for m in skills_store.list_skills() if m.enabled]
    dirs = [skills_store.Path(m.path) if hasattr(skills_store, "Path") else __import__("pathlib").Path(m.path) for m in metas]
    try:
        runtime.install_skills(workspace_key, dirs)
    except Exception:
        LOG.warning("skill install failed for %s", workspace_key)
    return format_skills(metas)
```
SIMPLIFY the `dirs` line — `skills_store` already exposes `enabled_skill_dirs()`. Use it directly:
```python
def prepare_skills(runtime, workspace_key: str) -> str:
    metas = [m for m in skills_store.list_skills() if m.enabled]
    try:
        runtime.install_skills(workspace_key, skills_store.enabled_skill_dirs())
    except Exception:
        LOG.warning("skill install failed for %s", workspace_key)
    return format_skills(metas)
```
(`LOG` already exists in common.py.)

(b) In each of `bootstrap.py`, `reason.py`, `explore.py`: after the workspace is ensured (`container_name = container_manager.ensure_running(...)`) and before/at `render_prompt`, compute `skills_text = prepare_skills(container_manager, container_name)` and add `"skills": skills_text` to the `render_prompt(...)` replacements dict. Import `prepare_skills` from `cairn.dispatcher.tasks.common`. Apply to every `render_prompt` call that uses a task template with the `{skills}` placeholder (the main execute render in each task; the conclude-fallback renders too if they use templates with `{skills}`). READ each task to place it correctly; reuse one `skills_text` per task run (compute once after ensure_running).

- [ ] **Step 4: Add an e2e assertion**

In `cairn/tests/test_mock_end_to_end.py`, the mock chain already runs tasks. Add an assertion that the prompt rendering didn't break and (if a skill is seeded) the `{skills}` value flows. Minimal safe check: the chain still completes (the fakes' `install_skills` no-op makes this pass). Run the whole suite to confirm:
Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest -q 2>&1 | tail -6` → all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/tasks/common.py cairn/src/cairn/dispatcher/tasks/bootstrap.py cairn/src/cairn/dispatcher/tasks/reason.py cairn/src/cairn/dispatcher/tasks/explore.py cairn/tests/test_prepare_skills.py
git commit -m "feat: install + advertise enabled skills per task run"
```

---

### Task 7: `/skills` SPA page

No JS test runner — verify by serving. Work in `cairn/src/cairn/server/static/index.html`.

- [ ] **Step 1: Orient**

Run: `grep -n "handleRoute\|view === 'engines'\|view = 'engines'\|location.hash = '/engines'\|loadEngines\|Engines" cairn/src/cairn/server/static/index.html`. Read the `#/engines` route branch + the Engines nav + the engines view container to mirror the pattern.

- [ ] **Step 2: Route + nav**

In `handleRoute()`, add `#/skills` → `this.view = 'skills'` + `this.loadSkills()`. Add a "Skills" nav button beside Engines/Chat (`location.hash = '/skills'`); a Back to `#/` on the skills view.

- [ ] **Step 3: State + methods**

Add data: `skills: []`, `skillsLoading: false`, `skillEdit: null` (currently-open `{name, content}`), `skillNew: {name:'', content:''}`. Methods:
```javascript
async loadSkills() {
  this.skillsLoading = true;
  try { const r = await fetch('/skills'); if (r.ok) this.skills = await r.json(); } finally { this.skillsLoading = false; }
},
async toggleSkill(s) {
  const r = await fetch(`/skills/${s.name}/enabled`, { method:'PUT', headers:{'content-type':'application/json'}, body: JSON.stringify({ enabled: !s.enabled }) });
  if (r.ok) { const u = await r.json(); this.skills = this.skills.map(x => x.name===u.name?u:x); }
},
async openSkill(name) {
  const r = await fetch(`/skills/${name}`); if (r.ok) this.skillEdit = await r.json();
},
async saveSkill() {
  const r = await fetch(`/skills/${this.skillEdit.name}`, { method:'PUT', headers:{'content-type':'application/json'}, body: JSON.stringify(this.skillEdit) });
  if (r.ok) { this.skillEdit = null; this.loadSkills(); }
},
async createSkill() {
  const r = await fetch('/skills', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(this.skillNew) });
  if (r.ok) { this.skillNew = {name:'',content:''}; this.loadSkills(); } else { alert((await r.json()).detail || 'create failed'); }
},
async deleteSkill(name) {
  if (!confirm('Delete skill '+name+'?')) return;
  const r = await fetch(`/skills/${name}`, { method:'DELETE' }); if (r.ok) this.loadSkills();
},
async uploadSkillZip(ev) {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/skills/upload', { method:'POST', body: fd });
  if (r.ok) this.loadSkills(); else alert((await r.json()).detail || 'upload failed');
  ev.target.value = '';
},
```

- [ ] **Step 4: Markup**

Add `<div x-show="view === 'skills'" x-cloak ...>` mirroring the engines view: header (title "Skills", Refresh `@click="loadSkills()"`, a ZIP upload `<input type="file" accept=".zip" @change="uploadSkillZip($event)">`, Back). A "new skill" mini-form (name + content textarea + Create). A list `<template x-for="s in skills" :key="s.name">`: name, description, an enable toggle (`@click="toggleSkill(s)"`), an "Edit" (`@click="openSkill(s.name)"`), a "Delete" (`@click="deleteSkill(s.name)"`). An edit panel shown when `skillEdit` (a textarea bound to `skillEdit.content` + Save/Cancel). Match existing Tailwind classes.

- [ ] **Step 5: Verify serves**

```bash
cd /Users/nicholas/project/ai/Cairn/cairn
( CAIRN_HOME=/tmp/cairn-sk uv run cairn serve --host 127.0.0.1 --port 8164 --db-path /tmp/cairn-sk/cairn.db --no-access-log >/dev/null 2>&1 & )
for i in $(seq 1 40); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8164/ 2>/dev/null | grep -q 200 && break; done
echo "index: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8164/)"
echo "skills view: $(curl -s http://127.0.0.1:8164/ | grep -c "view === 'skills'")"
echo "GET /skills: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8164/skills)"
pkill -f "port 8164" 2>/dev/null; pkill -f "cairn serve" 2>/dev/null; rm -rf /tmp/cairn-sk
```
Expected: `index: 200`, skills-view grep ≥ 1, `GET /skills` = 200. Don't leave a server running.

- [ ] **Step 6: Python suite** (HTML-only): `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest -q 2>&1 | tail -3` (no regressions).

- [ ] **Step 7: Manual browser check** — open `#/skills`: list shows seeded skills (decompile/graphify/...), toggle enable, edit a SKILL.md + save, create a new skill, upload a zip, delete.

- [ ] **Step 8: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/static/index.html
git commit -m "feat: Skills management page (list/toggle/edit/create/upload/delete)"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/specs/server-protocol.md`
- Modify: `docs/specs/dispatcher-design.md`
- Modify: `README.md`

- [ ] **Step 1: server-protocol.md** — add a "Skills" section documenting `GET /skills`, `GET /skills/{name}`, `POST /skills`, `PUT /skills/{name}`, `PUT /skills/{name}/enabled`, `DELETE /skills/{name}`, `POST /skills/upload` (multipart zip); note storage at `~/.cairn/skills/` + `.registry.json` enabled state; that secrets aren't involved.

- [ ] **Step 2: dispatcher-design.md** — under the Runtime backends section, document: enabled skills are installed into the workspace `.claude/skills/` at task start via `Runtime.install_skills` (docker tar `put_archive` to `/home/kali/workspace/.claude/skills`, local `copytree`), and advertised via the `{skills}` prompt directive (`format_skills`); claude discovers them natively, codex/opencode/pi rely on the prompt + reading the files; pi native `--skill` is out of scope.

- [ ] **Step 3: README.md** — add a short "Skills" subsection: bundled skills live in `skills/` and are seeded into `~/.cairn/skills/`; manage them at `#/skills`; enabled skills auto-inject into every project (docker + local); deployment note — a containerized dispatcher needs `~/.cairn` mounted to read skills.

- [ ] **Step 4: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add docs/specs/server-protocol.md docs/specs/dispatcher-design.md README.md
git commit -m "docs: skills management + workspace delivery"
```

---

## Self-Review Notes (author)

- **Spec coverage:** storage `~/.cairn/skills` + `.registry.json` (T1) · seed from repo skills/ (T1/T3) · CRUD + enable + zip upload endpoints (T2/T3) · `Runtime.install_skills` docker+local + fake runtimes (T4) · `format_skills` + `{skills}` placeholders (T5) · per-task install+advertise (T6) · `/skills` page (T7) · docs incl. deploy note (T8). All spec sections map to a task. B/AGENTS.md-injection/inline/pi-`--skill` correctly excluded (out of scope this round).
- **Type consistency:** `skills_store` API (`list_skills`/`read_skill_md`/`write_skill_md`/`create_skill`/`delete_skill`/`set_enabled`/`import_zip`/`enabled_skill_dirs`/`seed_if_empty`/`SkillMeta`) consistent across T1/T3/T6. `SkillInfo/SkillContent/SkillCreate/SkillEnable` identical across T2/T3. `Runtime.install_skills(key, skill_dirs)` consistent across T4/T6 + fakes. `format_skills(metas)` (objects with `.name`/`.description`) consistent T5/T6. `{skills}` placeholder added (T5) and always supplied (T6).
- **Verify-during-impl flags (not guesses):** `parents[4]` for the repo `skills/` seed path (T3) and the test fakes needing `install_skills` (T4) are explicitly called out to verify/adjust, not assumed silently. The container WORKDIR `/home/kali/workspace` is verified from the Dockerfile.
- **Best-effort injection:** `prepare_skills` swallows install errors so skills never break a task (T6); empty enabled set → no injection, empty `{skills}` (= current behavior).
- **Placeholder scan:** none. (Two test snippets in T3 note an intentional simplification line with the exact replacement to use.)