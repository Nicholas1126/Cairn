# Engine Management Page (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local-only "Engines" page that probes host-installed agents (claude/codex/opencode/pi) — showing launchable/version/path/source — and lets the user edit `~/.cairn/engines.json` overrides for engines that aren't auto-found.

**Architecture:** Reuse `runtime/local/resolve.probe_engine` (built in sub-project A) on the server host; expose it via `GET /engines`; add override read/write (`PUT/DELETE /engines/{type}/override`) that edits `~/.cairn/engines.json`; render a new `#/engines` page in the Alpine SPA. Docker agents are out of scope (baked into the image).

**Tech Stack:** Python 3, FastAPI, sqlite-free (filesystem), pytest, AlpineJS (`index.html`).

**Spec:** `docs/superpowers/specs/2026-06-20-engine-management-page-design.md`

**Conventions:** tests via `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest`. Commit after each task. This repo has a graphify knowledge graph — prefer `graphify query` before grepping when exploring.

**Branch:** `feat/local-engine-worker` (continues the sub-project A work).

---

### Task 1: Public override read/write in resolve.py

Currently `resolve.py` has private `_engines_config_path()` and `_load_overrides()`. Make them public and add atomic write helpers for editing one engine's override.

**Files:**
- Modify: `cairn/src/cairn/dispatcher/runtime/local/resolve.py`
- Test: `cairn/tests/test_engine_overrides.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_engine_overrides.py
from __future__ import annotations

import json

from cairn.dispatcher.runtime.local import resolve


def _point_config_at(monkeypatch, tmp_path):
    cfg = tmp_path / "engines.json"
    monkeypatch.setattr(resolve, "engines_config_path", lambda: cfg)
    return cfg


def test_load_overrides_empty_when_missing(monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    assert resolve.load_overrides() == {}


def test_set_override_creates_and_merges(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    resolve.set_override("pi", "/abs/pi", "direct")
    resolve.set_override("opencode", "/abs/opencode", "direct")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["pi"] == {"path": "/abs/pi", "launcher": "direct"}
    assert data["opencode"]["path"] == "/abs/opencode"
    # no leftover temp file
    assert not any(p.name.endswith(".tmp") for p in cfg.parent.iterdir())


def test_remove_override_only_target(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    resolve.set_override("pi", "/abs/pi", "direct")
    resolve.set_override("opencode", "/abs/opencode", "direct")
    resolve.remove_override("pi")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "pi" not in data
    assert "opencode" in data


def test_load_overrides_tolerates_corrupt_json(monkeypatch, tmp_path):
    cfg = _point_config_at(monkeypatch, tmp_path)
    cfg.write_text("{ not json", encoding="utf-8")
    assert resolve.load_overrides() == {}


def test_engines_config_path_follows_cairn_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "h"))
    assert resolve.engines_config_path() == tmp_path / "h" / "engines.json"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_overrides.py -v`
Expected: FAIL (no public `engines_config_path`/`load_overrides`/`set_override`/`remove_override`).

- [ ] **Step 3: Implement in resolve.py**

Add `import os` is already present. Replace the private definitions with public ones + keep private aliases, and add write helpers. Specifically:

Rename `_engines_config_path` → `engines_config_path` and `_load_overrides` → `load_overrides` (update their internal references in `resolve_engine`), then add private aliases + write helpers:

```python
def engines_config_path() -> Path:
    override = os.environ.get("CAIRN_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".cairn"
    return base / "engines.json"


def load_overrides() -> dict:
    try:
        return json.loads(engines_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_overrides(data: dict) -> None:
    path = engines_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def set_override(worker_type: str, path: str, launcher: str) -> None:
    data = load_overrides()
    data[worker_type] = {"path": path, "launcher": launcher}
    _write_overrides(data)


def remove_override(worker_type: str) -> None:
    data = load_overrides()
    if worker_type in data:
        del data[worker_type]
        _write_overrides(data)


# Back-compat private aliases (existing internal callers)
_engines_config_path = engines_config_path
_load_overrides = load_overrides
```
Update `resolve_engine` to call `load_overrides()` (was `_load_overrides()`) — or rely on the alias; either is fine since the alias points to the same function.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_overrides.py -v` → PASS (5).
Full suite: `uv run pytest -q 2>&1 | tail -3` → green (existing resolver tests must still pass; `test_engine_resolve.py` monkeypatches `_load_overrides`/`_engines_config_path` which now alias the public names — confirm those tests still pass; if a test patched `resolve._load_overrides` and that no longer affects `resolve_engine` because it calls `load_overrides`, fix by having `resolve_engine` call `load_overrides()` AND keep the alias assigned AFTER the function defs so patching either name works — simplest: in `resolve_engine` call `load_overrides()`, and in `test_engine_resolve.py` if it patches `_load_overrides`, it will still work only if the alias is used; to be safe, update any failing existing test to patch `load_overrides`).

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/dispatcher/runtime/local/resolve.py cairn/tests/test_engine_overrides.py
# include test_engine_resolve.py if you had to update a patched name
git commit -m "feat: public engines.json override read/write (set/remove)"
```

---

### Task 2: Server models EngineInfo + EngineOverride

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Test: `cairn/tests/test_engine_models.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_engine_models.py
from cairn.server.models import EngineInfo, EngineOverride


def test_engine_override_defaults_launcher_direct():
    o = EngineOverride(path="/abs/pi")
    assert o.launcher == "direct"


def test_engine_info_minimal():
    e = EngineInfo(type="pi", binary="pi", launchable=False, path=None, version=None, source=None)
    assert e.override is None
    assert e.launchable is False


def test_engine_info_with_override():
    e = EngineInfo(type="pi", binary="pi", launchable=True, path="/abs/pi",
                   version="1.0", source="override",
                   override=EngineOverride(path="/abs/pi", launcher="direct"))
    assert e.override.path == "/abs/pi"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_models.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement in models.py**

Add near the other models (e.g. after the Execution* models). `Literal` is already imported:
```python
class EngineOverride(BaseModel):
    path: str
    launcher: Literal["direct", "cmd", "powershell"] = "direct"


class EngineInfo(BaseModel):
    type: str
    binary: str
    launchable: bool
    path: str | None = None
    version: str | None = None
    source: str | None = None
    override: EngineOverride | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engine_models.py -v` → PASS (3). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/models.py cairn/tests/test_engine_models.py
git commit -m "feat: EngineInfo/EngineOverride models"
```

---

### Task 3: Engines router (GET list, PUT/DELETE override) + app wiring

**Files:**
- Create: `cairn/src/cairn/server/routers/engines.py`
- Modify: `cairn/src/cairn/server/app.py` (import + include)
- Test: `cairn/tests/test_engines_router.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_engines_router.py
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.runtime.local import resolve


def _client(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    monkeypatch.setattr(resolve, "engines_config_path", lambda: tmp_path / "engines.json")
    # deterministic probe: opencode launchable, others not
    def fake_probe(t):
        if t == "opencode":
            return {"launchable": True, "path": "/usr/local/bin/opencode", "version": "1.17.8", "source": "path"}
        return {"launchable": False, "path": None, "version": None, "source": None}
    monkeypatch.setattr(resolve, "probe_engine", fake_probe)
    return TestClient(app)


def test_list_engines_returns_four_types_no_creds(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/engines")
    assert r.status_code == 200
    types = {e["type"] for e in r.json()}
    assert types == {"claudecode", "codex", "opencode", "pi"}
    oc = next(e for e in r.json() if e["type"] == "opencode")
    assert oc["launchable"] is True and oc["version"] == "1.17.8"
    # nothing leaks env/keys
    assert "apiKey" not in r.text and "api_key" not in r.text


def test_put_and_delete_override(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.put("/engines/pi/override", json={"path": "/abs/pi", "launcher": "direct"})
    assert r.status_code == 200
    assert r.json()["override"]["path"] == "/abs/pi"
    # reflected in list
    pi = next(e for e in c.get("/engines").json() if e["type"] == "pi")
    assert pi["override"]["path"] == "/abs/pi"
    # delete
    assert c.delete("/engines/pi/override").status_code == 200
    pi = next(e for e in c.get("/engines").json() if e["type"] == "pi")
    assert pi["override"] is None


def test_unknown_type_404(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.put("/engines/nope/override", json={"path": "/x"}).status_code == 404
    assert c.delete("/engines/nope/override").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engines_router.py -v` → FAIL (404s, router not mounted).

- [ ] **Step 3: Implement router + wiring**

```python
# cairn/src/cairn/server/routers/engines.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cairn.dispatcher.runtime.local import resolve
from cairn.server.models import EngineInfo, EngineOverride

router = APIRouter(tags=["engines"])


def _engine_info(worker_type: str) -> EngineInfo:
    binary = resolve.BINARY[worker_type]
    probe = resolve.probe_engine(worker_type)
    ov_raw = resolve.load_overrides().get(worker_type)
    override = None
    if isinstance(ov_raw, dict) and ov_raw.get("path"):
        override = EngineOverride(path=ov_raw["path"], launcher=ov_raw.get("launcher", "direct"))
    return EngineInfo(
        type=worker_type, binary=binary,
        launchable=probe["launchable"], path=probe["path"],
        version=probe["version"], source=probe["source"], override=override,
    )


@router.get("/engines", response_model=list[EngineInfo])
def list_engines():
    return [_engine_info(t) for t in resolve.BINARY]


@router.put("/engines/{worker_type}/override", response_model=EngineInfo)
def put_override(worker_type: str, body: EngineOverride):
    if worker_type not in resolve.BINARY:
        raise HTTPException(404, "Unknown engine type")
    resolve.set_override(worker_type, body.path, body.launcher)
    return _engine_info(worker_type)


@router.delete("/engines/{worker_type}/override", response_model=EngineInfo)
def delete_override(worker_type: str):
    if worker_type not in resolve.BINARY:
        raise HTTPException(404, "Unknown engine type")
    resolve.remove_override(worker_type)
    return _engine_info(worker_type)
```

In `app.py` line 10, add `engines` to the import; add `app.include_router(engines.router)` with the others:
```python
from cairn.server.routers import engines, executions, export, hints, intents, projects, settings
```
```python
app.include_router(engines.router)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nicholas/project/ai/Cairn/cairn && uv run pytest tests/test_engines_router.py -v` → PASS (3). Full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/routers/engines.py cairn/src/cairn/server/app.py cairn/tests/test_engines_router.py
git commit -m "feat: /engines endpoints (probe list + override put/delete)"
```

---

### Task 4: Engines page in the SPA (route + nav + table + override editor)

No JS test runner — verify by serving the page. Work in `cairn/src/cairn/server/static/index.html`.

- [ ] **Step 1: Orient**

Run:
- `grep -n "handleRoute\|location.hash\|view = '\|view === '\|#/projects" cairn/src/cairn/server/static/index.html`
- Read `handleRoute()` (around line 1290) to see how routes set `view` and how `#/projects/<id>` is parsed.
- Find where the top-level views render (`x-show="view === 'list'"` ~line 232, `view === 'graph'` ~line 403) and where a header/nav lives (the "Cairn" title / share icon area near the project list).

- [ ] **Step 2: Add route + nav**

In `handleRoute()`, add a branch: if `hash` is `#/engines` (or `/engines`), set `this.view = 'engines'` and (optionally) load engines. Add a nav control on the list view header that sets `location.hash = '/engines'` (an "Engines" link/button styled like existing header controls). Add a back-to-list affordance on the engines view (set `location.hash = '/'`).

- [ ] **Step 3: State + fetch**

In the Alpine component data add `engines: []`, `enginesLoading: false`, and an editing buffer e.g. `engineEdit: {}` (map type→{path,launcher,open}). Add methods:
```javascript
async loadEngines() {
  this.enginesLoading = true;
  try { const r = await fetch('/engines'); if (r.ok) this.engines = await r.json(); }
  finally { this.enginesLoading = false; }
},
async saveEngineOverride(type) {
  const buf = this.engineEdit[type] || {};
  const r = await fetch(`/engines/${type}/override`, {
    method: 'PUT', headers: {'content-type': 'application/json'},
    body: JSON.stringify({ path: buf.path, launcher: buf.launcher || 'direct' }),
  });
  if (r.ok) { const e = await r.json(); this._replaceEngine(e); this.engineEdit[type] = { open: false }; }
},
async clearEngineOverride(type) {
  const r = await fetch(`/engines/${type}/override`, { method: 'DELETE' });
  if (r.ok) { this._replaceEngine(await r.json()); }
},
_replaceEngine(e) { this.engines = this.engines.map(x => x.type === e.type ? e : x); },
```
Call `this.loadEngines()` when entering the engines route.

- [ ] **Step 4: Markup**

Add `<div x-show="view === 'engines'" ...>` panel with:
- A header row: title "Engines", a "Refresh" button (`@click="loadEngines()"`), a "Back" link to `#/`.
- A note: "Reflects the agents available to the server host (Local mode)."
- `<template x-for="e in engines" :key="e.type">` rows showing: `e.type`, a launchable badge (`e.launchable ? green 'available' : grey 'not found'`), `e.version`, `e.path`, `e.source`.
- Per row, an "Override" toggle that reveals inputs bound to `engineEdit[e.type].path` and a `launcher` select (`direct`/`cmd`/`powershell`), a "Save" (`@click="saveEngineOverride(e.type)"`), and when `e.override` exists a "Clear override" (`@click="clearEngineOverride(e.type)"`).
Match existing Tailwind classes/spacing used elsewhere in the file for visual consistency.

- [ ] **Step 5: Verify serves + endpoints**

```bash
cd /Users/nicholas/project/ai/Cairn/cairn
( uv run cairn serve --host 127.0.0.1 --port 8157 --db-path /tmp/cairn-eng/cairn.db --no-access-log >/dev/null 2>&1 & )
for i in $(seq 1 40); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8157/ 2>/dev/null | grep -q 200 && break; done
echo "index: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8157/)"
echo "engines view present: $(curl -s http://127.0.0.1:8157/ | grep -c "view === 'engines'")"
echo "GET /engines types: $(curl -s http://127.0.0.1:8157/engines | python3 -c "import sys,json;print(sorted(e['type'] for e in json.load(sys.stdin)))")"
pkill -f "port 8157" 2>/dev/null; rm -rf /tmp/cairn-eng
```
Expected: `index: 200`, engines-view grep ≥ 1, and the types list `['claudecode','codex','opencode','pi']`. `pkill -f "cairn serve" || true` to be safe — don't leave a server running.

- [ ] **Step 6: Manual browser check**

Open the app, click the Engines nav, confirm the 4 engines render with status/version/path, set an override for a not-found engine and confirm it reflects, clear it, Refresh works.

- [ ] **Step 7: Commit**

```bash
cd /Users/nicholas/project/ai/Cairn
git add cairn/src/cairn/server/static/index.html
git commit -m "feat: Engines management page (probe + override editor)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** override read/write public funcs (T1) · EngineInfo/EngineOverride (T2) · GET /engines + PUT/DELETE override + no-creds (T3) · SPA page/route/nav + override editor + Refresh (T4). Level-2 connectivity correctly absent (deferred to C). Docker exclusion honored (probe only the 4 host binaries on the server host).
- **Type consistency:** `resolve.BINARY`, `probe_engine`, `load_overrides`/`set_override`/`remove_override`/`engines_config_path` names consistent across T1/T3. `EngineInfo`/`EngineOverride` field names identical across T2/T3. Frontend reads `type/launchable/version/path/source/override` exactly as T2/T3 return.
- **No creds leak:** `GET /engines` only returns probe + override (path/launcher) — no env; T3 test asserts `apiKey`/`api_key` absent.
- **Placeholder scan:** none.
