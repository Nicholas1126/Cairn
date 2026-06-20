# Execution Logging System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every worker invocation (rendered prompt, executed command, agent output) so the Web UI can show a real-time `Runtime` tab and bind each result (fact/intent) back to how it was produced.

**Architecture:** The dispatcher (separate process, holds prompt/command/output) reports each execution over HTTP to the server; the server stores a redacted, truncated record in sqlite and writes the full ≤1MB log to a per-project file it owns; the frontend reads new endpoints. Secrets are redacted; outputs are head/tail truncated; logging is toggleable; log files are cascade-deleted with their project.

**Tech Stack:** Python 3, FastAPI, sqlite3, pytest, requests, AlpineJS (single `index.html`).

**Spec:** `docs/superpowers/specs/2026-06-19-execution-logging-design.md`

**Conventions for every task:** run tests with `cd cairn && uv run pytest`. Commit after each task passes.

---

### Task 1: Shared redaction + truncation utilities

Pure functions used by both dispatcher (pre-upload) and server (defense-in-depth + inline derivation). Top-level module so neither sub-package imports the other.

**Files:**
- Create: `cairn/src/cairn/execlog.py`
- Test: `cairn/tests/test_execlog.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_execlog.py
from __future__ import annotations

from cairn.execlog import redact_text, redact_command, redact_env, truncate_head_tail


def test_redact_text_masks_api_keys_tokens_and_bearer():
    assert "sk-abcd1234efgh" not in redact_text("key=sk-abcd1234efgh done")
    assert "sk-***" in redact_text("key=sk-abcd1234efgh done")
    masked = redact_text('{"apiKey":"sk-secretsecret","baseURL":"https://x"}')
    assert "sk-secretsecret" not in masked
    assert "https://x" in masked  # non-secret preserved
    assert "Bearer ***" in redact_text("Authorization: Bearer eyJabc.def-ghi")


def test_redact_command_masks_each_arg_and_preserves_structure():
    argv = ["opencode", "run", '{"provider":{"options":{"apiKey":"sk-zzzzzzzz"}}}', "--", "hello"]
    out = redact_command(argv)
    assert len(out) == len(argv)
    assert "sk-zzzzzzzz" not in "".join(out)
    assert out[0] == "opencode" and out[-1] == "hello"


def test_redact_env_masks_secret_named_keys_only():
    env = {"OPENCODE_API_KEY": "sk-zzzzzzzz", "OPENCODE_MODEL": "glm-5.1", "PATH": "/bin"}
    out = redact_env(env)
    assert out["OPENCODE_API_KEY"] == "***"
    assert out["OPENCODE_MODEL"] == "glm-5.1"
    assert out["PATH"] == "/bin"


def test_truncate_head_tail_keeps_head_and_tail_and_marks():
    text = "A" * 100 + "B" * 100
    res = truncate_head_tail(text, limit_bytes=40)
    assert res.truncated is True
    assert res.original_bytes == 200
    assert res.text.startswith("A")
    assert res.text.rstrip().endswith("B")
    assert "truncated" in res.text


def test_truncate_head_tail_noop_when_within_limit():
    res = truncate_head_tail("short", limit_bytes=1000)
    assert res.truncated is False
    assert res.text == "short"
    assert res.original_bytes == 5


def test_truncate_head_tail_does_not_split_multibyte():
    text = "中" * 100  # 3 bytes each in utf-8
    res = truncate_head_tail(text, limit_bytes=50)
    # must decode cleanly (no replacement chars / exceptions)
    assert "�" not in res.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_execlog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cairn.execlog'`.

- [ ] **Step 3: Implement the module**

```python
# cairn/src/cairn/execlog.py
from __future__ import annotations

import re
from dataclasses import dataclass

# Mask "key: value" / "key=value" / JSON "key":"value" where key looks secret.
_KV_SECRET = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password|authorization)["\']?\s*[:=]\s*["\']?)'
    r'([^"\'\s,}\]]+)'
)
_SK_TOKEN = re.compile(r'sk-[A-Za-z0-9_\-]{6,}')
_BEARER = re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]+')

# env var names whose VALUE is always a secret regardless of content
_SECRET_ENV = re.compile(r'(?i)(api[_-]?key|apikey|token|secret|password|auth)')


def redact_text(text: str) -> str:
    if not text:
        return text
    out = _KV_SECRET.sub(lambda m: m.group(1) + "***", text)
    out = _SK_TOKEN.sub("sk-***", out)
    out = _BEARER.sub("Bearer ***", out)
    return out


def redact_command(argv: list[str]) -> list[str]:
    return [redact_text(arg) for arg in argv]


def redact_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if _SECRET_ENV.search(k) else v) for k, v in env.items()}


@dataclass(slots=True)
class TruncResult:
    text: str
    original_bytes: int
    truncated: bool


def truncate_head_tail(text: str, limit_bytes: int) -> TruncResult:
    raw = text.encode("utf-8")
    n = len(raw)
    if n <= limit_bytes:
        return TruncResult(text=text, original_bytes=n, truncated=False)
    half = max(1, limit_bytes // 2)
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[n - half:].decode("utf-8", errors="ignore")
    dropped = n - (2 * half)
    marker = f"\n…… [truncated {dropped} bytes of {n} total] ……\n"
    return TruncResult(text=head + marker + tail, original_bytes=n, truncated=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_execlog.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/execlog.py cairn/tests/test_execlog.py
git commit -m "feat: shared secret-redaction + head/tail truncation utilities"
```

---

### Task 2: DB schema — executions table, settings columns, executions root

**Files:**
- Modify: `cairn/src/cairn/server/db.py`
- Test: `cairn/tests/test_db_executions.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_db_executions.py
from __future__ import annotations

from cairn.server import db


def _fresh(tmp_path):
    db._db_path = None  # reset module singleton for the test
    db.configure(tmp_path / "cairn.db")


def test_executions_table_and_settings_columns_exist(tmp_path):
    _fresh(tmp_path)
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(executions)")}
    assert {"id", "project_id", "phase", "intent_id", "worker_name", "command",
            "prompt", "stdout_inline", "outcome", "log_path",
            "produced_fact_id", "produced_intent_ids"} <= cols
    with db.get_conn() as conn:
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
    assert {"execution_record_enabled", "execution_file_logging"} <= scols


def test_executions_root_is_under_db_dir(tmp_path):
    _fresh(tmp_path)
    assert db.executions_root() == tmp_path / "executions"


def test_executions_cascade_delete_with_project(tmp_path):
    _fresh(tmp_path)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
        conn.execute(
            "INSERT INTO executions (id, project_id, phase, worker_name, command, prompt, "
            "outcome, started_at, ended_at) VALUES "
            "('exec_001','p1','explore','w','[]','hi','success','now','now')"
        )
    with db.get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = 'p1'")
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM executions WHERE project_id = 'p1'").fetchall()
    assert rows == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_db_executions.py -v`
Expected: FAIL (no `executions` table / no `executions_root`).

- [ ] **Step 3: Implement schema + migration + root helper**

In `cairn/src/cairn/server/db.py`, append this table to the `SCHEMA` string (after `scoped_counters`):

```sql
CREATE TABLE IF NOT EXISTS executions (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    intent_id TEXT,
    worker_name TEXT NOT NULL,
    model TEXT,
    command TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response_text TEXT,
    stdout_inline TEXT,
    stderr_inline TEXT,
    stdout_bytes INTEGER NOT NULL DEFAULT 0,
    stderr_bytes INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER,
    outcome TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    produced_fact_id TEXT,
    produced_intent_ids TEXT,
    log_path TEXT,
    PRIMARY KEY (id, project_id)
);
```

> Note: only `project_id` has a foreign key with `ON DELETE CASCADE`. `intent_id` / `produced_*` are plain columns by design (reopen deletes intents; execution history must survive).

In `configure()`, after `_ensure_project_columns(conn)`, add `_ensure_settings_columns(conn)`. Then add the helpers:

```python
def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    if "execution_record_enabled" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN execution_record_enabled INTEGER NOT NULL DEFAULT 1")
    if "execution_file_logging" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN execution_file_logging INTEGER NOT NULL DEFAULT 1")


def executions_root() -> Path:
    assert _db_path is not None
    return _db_path.parent / "executions"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_db_executions.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/db.py cairn/tests/test_db_executions.py
git commit -m "feat: executions table, settings toggle columns, executions root"
```

---

### Task 3: Server Pydantic models

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Test: `cairn/tests/test_execution_models.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_execution_models.py
from cairn.server.models import ExecutionReport, ExecutionSummary, ExecutionDetail, Settings


def test_settings_has_toggles_with_defaults():
    s = Settings(intent_timeout=15, reason_timeout=15)
    assert s.execution_record_enabled is True
    assert s.execution_file_logging is True


def test_execution_report_minimal_valid():
    r = ExecutionReport(
        phase="explore", worker_name="w", command=["opencode", "run"],
        prompt="do it", outcome="success", started_at="t0", ended_at="t1",
        duration_ms=10, stdout="out", stderr="",
    )
    assert r.intent_id is None
    assert r.produced_intent_ids == []


def test_execution_summary_roundtrip():
    s = ExecutionSummary(
        id="exec_001", phase="reason", intent_id=None, worker_name="w", model="m",
        outcome="success", exit_code=0, started_at="t0", ended_at="t1",
        duration_ms=5, produced_fact_id=None, produced_intent_ids=["i001"], has_log=True,
    )
    assert s.has_log is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_execution_models.py -v`
Expected: FAIL (ImportError for new models).

- [ ] **Step 3: Implement models**

In `cairn/src/cairn/server/models.py`, extend `Settings` and add the new models:

```python
class Settings(BaseModel):
    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    execution_record_enabled: bool = True
    execution_file_logging: bool = True


class ExecutionReport(BaseModel):
    phase: str
    intent_id: str | None = None
    worker_name: str
    model: str | None = None
    command: list[str]
    prompt: str
    response_text: str | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    outcome: str
    started_at: str
    ended_at: str
    duration_ms: int = 0
    produced_fact_id: str | None = None
    produced_intent_ids: list[str] = Field(default_factory=list)


class ExecutionSummary(BaseModel):
    id: str
    phase: str
    intent_id: str | None = None
    worker_name: str
    model: str | None = None
    outcome: str
    exit_code: int | None = None
    started_at: str
    ended_at: str
    duration_ms: int = 0
    produced_fact_id: str | None = None
    produced_intent_ids: list[str] = Field(default_factory=list)
    has_log: bool = False


class ExecutionDetail(ExecutionSummary):
    command: list[str]
    prompt: str
    response_text: str | None = None
    stdout_inline: str | None = None
    stderr_inline: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    truncated: bool = False
    log_path: str | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_execution_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/models.py cairn/tests/test_execution_models.py
git commit -m "feat: execution + settings pydantic models"
```

---

### Task 4: File store — atomic write, read, zip, delete

Owns all filesystem concerns for execution logs. Server-only.

**Files:**
- Create: `cairn/src/cairn/server/execstore.py`
- Test: `cairn/tests/test_execstore.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_execstore.py
from __future__ import annotations

import io
import zipfile

from cairn.server import db, execstore


def _fresh(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")


def test_write_and_read_log_atomic(tmp_path):
    _fresh(tmp_path)
    path = execstore.write_log(
        project_id="p1", exec_id="exec_001", phase="explore", intent_id="i001",
        started_at="2026-06-19-01-32-26", body="hello world",
    )
    assert path.exists()
    assert "p1" in str(path) and path.name.endswith("exec_001.log")
    assert execstore.read_log(path) == "hello world"
    # no leftover temp files
    assert not any(p.name.endswith(".tmp") for p in path.parent.iterdir())


def test_zip_project_logs_contains_all(tmp_path):
    _fresh(tmp_path)
    execstore.write_log("p1", "exec_001", "explore", "i001", "2026-06-19-01-00-00", "a")
    execstore.write_log("p1", "exec_002", "reason", None, "2026-06-19-01-01-00", "b")
    data = execstore.zip_project_logs("p1")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert len(names) == 2


def test_zip_returns_none_when_no_logs(tmp_path):
    _fresh(tmp_path)
    assert execstore.zip_project_logs("ghost") is None


def test_delete_project_logs_removes_dir(tmp_path):
    _fresh(tmp_path)
    p = execstore.write_log("p1", "exec_001", "explore", "i001", "2026-06-19-01-00-00", "a")
    execstore.delete_project_logs("p1")
    assert not p.exists()
    assert not p.parent.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_execstore.py -v`
Expected: FAIL (no `execstore`).

- [ ] **Step 3: Implement execstore**

```python
# cairn/src/cairn/server/execstore.py
from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path

from cairn.server import db

FILE_CAP_BYTES = 1_000_000  # 1MB hard cap per .log


def _project_dir(project_id: str) -> Path:
    return db.executions_root() / project_id


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in part)


def log_filename(exec_id: str, phase: str, intent_id: str | None, started_at: str) -> str:
    intent = intent_id or "no_intent"
    return f"{_safe(started_at)}-{_safe(phase)}-{_safe(intent)}-{_safe(exec_id)}.log"


def write_log(project_id: str, exec_id: str, phase: str, intent_id: str | None,
              started_at: str, body: str) -> Path:
    directory = _project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / log_filename(exec_id, phase, intent_id, started_at)
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, final)  # atomic on same filesystem
    return final


def read_log(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def zip_project_logs(project_id: str) -> bytes | None:
    directory = _project_dir(project_id)
    if not directory.exists():
        return None
    logs = sorted(directory.glob("*.log"))
    if not logs:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for log in logs:
            zf.write(log, arcname=log.name)
    return buf.getvalue()


def delete_project_logs(project_id: str) -> None:
    shutil.rmtree(_project_dir(project_id), ignore_errors=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_execstore.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/execstore.py cairn/tests/test_execstore.py
git commit -m "feat: execution log file store (atomic write, zip, delete)"
```

---

### Task 5: Persistence service — insert + query executions

Bridges the report payload → DB row (+ inline truncation) and reads rows back.

**Files:**
- Modify: `cairn/src/cairn/server/services.py`
- Test: `cairn/tests/test_execution_service.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_execution_service.py
from __future__ import annotations

from cairn.server import db, services
from cairn.server.models import ExecutionReport


def _fresh(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")


def _report(**over):
    base = dict(
        phase="explore", worker_name="w", model="m", command=["opencode", "run"],
        prompt="do", outcome="success", started_at="2026-06-19-01-00-00",
        ended_at="2026-06-19-01-00-05", duration_ms=5, stdout="x" * 200000, stderr="",
        intent_id="i001", produced_fact_id="f001",
    )
    base.update(over)
    return ExecutionReport(**base)


def test_insert_truncates_inline_and_records_bytes(tmp_path):
    _fresh(tmp_path)
    with db.get_conn() as conn:
        rec = services.insert_execution(conn, "p1", _report(), inline_limit=64 * 1024)
    assert rec.id == "exec_001"
    assert rec.stdout_bytes == 200000
    assert rec.truncated is True
    assert len(rec.stdout_inline.encode("utf-8")) <= 64 * 1024 + 200  # +marker


def test_exec_ids_are_unique_and_scoped(tmp_path):
    _fresh(tmp_path)
    with db.get_conn() as conn:
        a = services.insert_execution(conn, "p1", _report(), inline_limit=64 * 1024)
        b = services.insert_execution(conn, "p1", _report(), inline_limit=64 * 1024)
    assert (a.id, b.id) == ("exec_001", "exec_002")


def test_list_and_get_executions(tmp_path):
    _fresh(tmp_path)
    with db.get_conn() as conn:
        services.insert_execution(conn, "p1", _report(), inline_limit=64 * 1024)
    with db.get_conn() as conn:
        summaries = services.list_executions(conn, "p1")
        detail = services.get_execution(conn, "p1", "exec_001")
    assert len(summaries) == 1 and summaries[0].id == "exec_001"
    assert detail.prompt == "do" and detail.command == ["opencode", "run"]
    assert detail.produced_fact_id == "f001"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_execution_service.py -v`
Expected: FAIL (no `insert_execution`/`list_executions`/`get_execution`).

- [ ] **Step 3: Implement service functions**

Add to `cairn/src/cairn/server/services.py` (add `import json` at top and import the models + execlog + ExecutionSummary/Detail):

```python
import json

from cairn.server.models import ExecutionDetail, ExecutionReport, ExecutionSummary
from cairn.execlog import redact_command, redact_text, truncate_head_tail


def next_execution_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "execution", "exec_", project_id)


def insert_execution(
    conn: sqlite3.Connection, project_id: str, report: ExecutionReport, *,
    inline_limit: int, log_path: str | None = None,
) -> ExecutionDetail:
    exec_id = next_execution_id(conn, project_id)
    command = redact_command(report.command)
    out = truncate_head_tail(redact_text(report.stdout or ""), inline_limit)
    err = truncate_head_tail(redact_text(report.stderr or ""), inline_limit)
    produced_intents = json.dumps(report.produced_intent_ids) if report.produced_intent_ids else None
    conn.execute(
        "INSERT INTO executions (id, project_id, phase, intent_id, worker_name, model, "
        "command, prompt, response_text, stdout_inline, stderr_inline, stdout_bytes, "
        "stderr_bytes, truncated, exit_code, outcome, started_at, ended_at, duration_ms, "
        "produced_fact_id, produced_intent_ids, log_path) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            exec_id, project_id, report.phase, report.intent_id, report.worker_name, report.model,
            json.dumps(command), redact_text(report.prompt),
            redact_text(report.response_text) if report.response_text else None,
            out.text, err.text, out.original_bytes, err.original_bytes,
            1 if (out.truncated or err.truncated) else 0,
            report.exit_code, report.outcome, report.started_at, report.ended_at,
            report.duration_ms, report.produced_fact_id, produced_intents, log_path,
        ),
    )
    return _execution_detail_from_row(
        conn.execute(
            "SELECT * FROM executions WHERE id = ? AND project_id = ?", (exec_id, project_id)
        ).fetchone()
    )


def set_execution_log_path(conn: sqlite3.Connection, project_id: str, exec_id: str, log_path: str) -> None:
    conn.execute(
        "UPDATE executions SET log_path = ? WHERE id = ? AND project_id = ?",
        (log_path, exec_id, project_id),
    )


def list_executions(conn: sqlite3.Connection, project_id: str) -> list[ExecutionSummary]:
    rows = conn.execute(
        "SELECT * FROM executions WHERE project_id = ? ORDER BY started_at, id", (project_id,)
    ).fetchall()
    return [_execution_summary_from_row(r) for r in rows]


def get_execution(conn: sqlite3.Connection, project_id: str, exec_id: str) -> ExecutionDetail:
    row = conn.execute(
        "SELECT * FROM executions WHERE id = ? AND project_id = ?", (exec_id, project_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Execution not found")
    return _execution_detail_from_row(row)


def _produced_intents(row: sqlite3.Row) -> list[str]:
    raw = row["produced_intent_ids"]
    return json.loads(raw) if raw else []


def _execution_summary_from_row(row: sqlite3.Row) -> ExecutionSummary:
    return ExecutionSummary(
        id=row["id"], phase=row["phase"], intent_id=row["intent_id"],
        worker_name=row["worker_name"], model=row["model"], outcome=row["outcome"],
        exit_code=row["exit_code"], started_at=row["started_at"], ended_at=row["ended_at"],
        duration_ms=row["duration_ms"], produced_fact_id=row["produced_fact_id"],
        produced_intent_ids=_produced_intents(row), has_log=bool(row["log_path"]),
    )


def _execution_detail_from_row(row: sqlite3.Row) -> ExecutionDetail:
    return ExecutionDetail(
        id=row["id"], phase=row["phase"], intent_id=row["intent_id"],
        worker_name=row["worker_name"], model=row["model"], outcome=row["outcome"],
        exit_code=row["exit_code"], started_at=row["started_at"], ended_at=row["ended_at"],
        duration_ms=row["duration_ms"], produced_fact_id=row["produced_fact_id"],
        produced_intent_ids=_produced_intents(row), has_log=bool(row["log_path"]),
        command=json.loads(row["command"]), prompt=row["prompt"],
        response_text=row["response_text"], stdout_inline=row["stdout_inline"],
        stderr_inline=row["stderr_inline"], stdout_bytes=row["stdout_bytes"],
        stderr_bytes=row["stderr_bytes"], truncated=bool(row["truncated"]),
        log_path=row["log_path"],
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_execution_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/services.py cairn/tests/test_execution_service.py
git commit -m "feat: execution persistence service (insert/list/get + inline truncation)"
```

---

### Task 6: Executions router + app wiring

POST report (authoritative toggle enforcement + file write), list, detail, single-log download, project zip.

**Files:**
- Create: `cairn/src/cairn/server/routers/executions.py`
- Modify: `cairn/src/cairn/server/app.py:10` (import) and `:32` (include_router)
- Test: `cairn/tests/test_executions_router.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_executions_router.py
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    return TestClient(app)


def _payload(**over):
    base = dict(
        phase="explore", worker_name="w", model="m", command=["opencode", "run", "sk-secret123"],
        prompt="do", outcome="success", started_at="2026-06-19-01-00-00",
        ended_at="2026-06-19-01-00-05", duration_ms=5, stdout="hello", stderr="",
        intent_id="i001", produced_fact_id="f001",
    )
    base.update(over)
    return base


def test_post_records_redacts_and_writes_file(tmp_path):
    c = _client(tmp_path)
    r = c.post("/projects/p1/executions", json=_payload())
    assert r.status_code == 201
    exec_id = r.json()["id"]
    detail = c.get(f"/projects/p1/executions/{exec_id}").json()
    assert "sk-secret123" not in "".join(detail["command"])  # redacted
    assert detail["has_log"] is True
    log = c.get(f"/projects/p1/executions/{exec_id}/log")
    assert log.status_code == 200 and "hello" in log.text


def test_list_and_zip(tmp_path):
    c = _client(tmp_path)
    c.post("/projects/p1/executions", json=_payload())
    c.post("/projects/p1/executions", json=_payload(phase="reason", intent_id=None, produced_fact_id=None))
    assert len(c.get("/projects/p1/executions").json()) == 2
    z = c.get("/projects/p1/executions/logs.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_file_logging_off_skips_file_no_download(tmp_path):
    c = _client(tmp_path)
    c.put("/settings", json={"intent_timeout": 15, "reason_timeout": 15,
                             "execution_record_enabled": True, "execution_file_logging": False})
    exec_id = c.post("/projects/p1/executions", json=_payload()).json()["id"]
    assert c.get(f"/projects/p1/executions/{exec_id}").json()["has_log"] is False
    assert c.get(f"/projects/p1/executions/{exec_id}/log").status_code == 404
    assert c.get("/projects/p1/executions/logs.zip").status_code == 404


def test_record_disabled_returns_204_and_stores_nothing(tmp_path):
    c = _client(tmp_path)
    c.put("/settings", json={"intent_timeout": 15, "reason_timeout": 15,
                             "execution_record_enabled": False, "execution_file_logging": True})
    assert c.post("/projects/p1/executions", json=_payload()).status_code == 204
    assert c.get("/projects/p1/executions").json() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_executions_router.py -v`
Expected: FAIL (404s — router not mounted).

- [ ] **Step 3: Implement router + wiring**

```python
# cairn/src/cairn/server/routers/executions.py
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from cairn.server import execstore, services
from cairn.server.db import get_conn
from cairn.server.models import ExecutionDetail, ExecutionReport, ExecutionSummary

router = APIRouter(tags=["executions"])

INLINE_LIMIT = 64 * 1024  # 64KB head+tail in DB


def _read_toggles(conn) -> tuple[bool, bool]:
    row = conn.execute(
        "SELECT execution_record_enabled, execution_file_logging FROM settings WHERE rowid = 1"
    ).fetchone()
    return bool(row["execution_record_enabled"]), bool(row["execution_file_logging"])


@router.post("/projects/{project_id}/executions", status_code=201, response_model=ExecutionDetail)
def report_execution(project_id: str, report: ExecutionReport):
    with get_conn() as conn:
        services.get_project_or_404(conn, project_id)
        record_enabled, file_logging = _read_toggles(conn)
        if not record_enabled:
            return Response(status_code=204)
        rec = services.insert_execution(conn, project_id, report, inline_limit=INLINE_LIMIT)
        if file_logging:
            body = _render_log_body(rec, report)
            path = execstore.write_log(
                project_id, rec.id, rec.phase, rec.intent_id, rec.started_at, body
            )
            services.set_execution_log_path(conn, project_id, rec.id, str(path))
            rec.log_path = str(path)
            rec.has_log = True
    return rec


def _render_log_body(rec: ExecutionDetail, report: ExecutionReport) -> str:
    from cairn.execlog import redact_command, redact_text, truncate_head_tail
    capped_out = truncate_head_tail(redact_text(report.stdout or ""), execstore.FILE_CAP_BYTES)
    capped_err = truncate_head_tail(redact_text(report.stderr or ""), execstore.FILE_CAP_BYTES)
    lines = [
        "=== META ===",
        f"exec_id: {rec.id}", f"phase: {rec.phase}", f"worker: {rec.worker_name}",
        f"model: {rec.model}", f"outcome: {rec.outcome}", f"duration_ms: {rec.duration_ms}",
        f"started_at: {rec.started_at}", f"ended_at: {rec.ended_at}",
        f"produced_fact_id: {rec.produced_fact_id}",
        f"produced_intent_ids: {rec.produced_intent_ids}",
        "=== COMMAND ===", " ".join(redact_command(report.command)),
        "=== PROMPT ===", redact_text(report.prompt),
        "=== STDOUT ===", capped_out.text,
        "=== STDERR ===", capped_err.text,
    ]
    return "\n".join(lines)


@router.get("/projects/{project_id}/executions", response_model=list[ExecutionSummary])
def list_executions(project_id: str):
    with get_conn() as conn:
        services.get_project_or_404(conn, project_id)
        return services.list_executions(conn, project_id)


@router.get("/projects/{project_id}/executions/logs.zip")
def download_project_logs(project_id: str):
    data = execstore.zip_project_logs(project_id)
    if data is None:
        raise HTTPException(404, "No logs for this project")
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_id}-logs.zip"'},
    )


@router.get("/projects/{project_id}/executions/{exec_id}", response_model=ExecutionDetail)
def get_execution(project_id: str, exec_id: str):
    with get_conn() as conn:
        services.get_project_or_404(conn, project_id)
        return services.get_execution(conn, project_id, exec_id)


@router.get("/projects/{project_id}/executions/{exec_id}/log")
def download_execution_log(project_id: str, exec_id: str):
    with get_conn() as conn:
        detail = services.get_execution(conn, project_id, exec_id)
    if not detail.log_path or not Path(detail.log_path).exists():
        raise HTTPException(404, "No log file for this execution")
    return FileResponse(detail.log_path, media_type="text/plain",
                        filename=Path(detail.log_path).name)
```

> Route order matters: `logs.zip` is declared before `/{exec_id}` so it is not captured as an exec_id.

In `cairn/src/cairn/server/app.py`, update the import on line 10 and add the include after line 32:

```python
from cairn.server.routers import executions, export, hints, intents, projects, settings
```
```python
app.include_router(executions.router)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_executions_router.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/routers/executions.py cairn/src/cairn/server/app.py cairn/tests/test_executions_router.py
git commit -m "feat: executions REST endpoints (report/list/detail/log/zip)"
```

---

### Task 7: Settings endpoint toggles + delete-project cascade

**Files:**
- Modify: `cairn/src/cairn/server/routers/settings.py`
- Modify: `cairn/src/cairn/server/routers/projects.py:147-151` (delete_project)
- Test: `cairn/tests/test_settings_and_delete_executions.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_settings_and_delete_executions.py
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cairn.server import db, execstore
from cairn.server.app import app


def _client(tmp_path):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    return TestClient(app)


def test_settings_get_put_roundtrip_toggles(tmp_path):
    c = _client(tmp_path)
    assert c.get("/settings").json()["execution_file_logging"] is True
    c.put("/settings", json={"intent_timeout": 15, "reason_timeout": 15,
                             "execution_record_enabled": True, "execution_file_logging": False})
    assert c.get("/settings").json()["execution_file_logging"] is False


def test_delete_project_removes_log_dir_and_rows(tmp_path):
    c = _client(tmp_path)
    payload = dict(phase="explore", worker_name="w", model="m", command=["x"], prompt="p",
                   outcome="success", started_at="2026-06-19-01-00-00",
                   ended_at="2026-06-19-01-00-01", duration_ms=1, stdout="o", stderr="")
    exec_id = c.post("/projects/p1/executions", json=payload).json()["id"]
    log_path = Path(c.get(f"/projects/p1/executions/{exec_id}").json()["log_path"])
    assert log_path.exists()
    c.delete("/projects/p1")
    assert not log_path.exists()
    assert not log_path.parent.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_settings_and_delete_executions.py -v`
Expected: FAIL (PUT settings rejects extra fields / delete leaves files).

- [ ] **Step 3: Implement**

Replace the body of `cairn/src/cairn/server/routers/settings.py` get/put with toggle-aware SQL:

```python
@router.get("/settings", response_model=Settings)
def get_settings():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT intent_timeout, reason_timeout, execution_record_enabled, "
            "execution_file_logging FROM settings WHERE rowid = 1"
        ).fetchone()
        return Settings(
            intent_timeout=row["intent_timeout"], reason_timeout=row["reason_timeout"],
            execution_record_enabled=bool(row["execution_record_enabled"]),
            execution_file_logging=bool(row["execution_file_logging"]),
        )


@router.put("/settings", response_model=Settings)
def update_settings(body: Settings):
    with get_conn() as conn:
        conn.execute(
            "UPDATE settings SET intent_timeout = ?, reason_timeout = ?, "
            "execution_record_enabled = ?, execution_file_logging = ? WHERE rowid = 1",
            (body.intent_timeout, body.reason_timeout,
             1 if body.execution_record_enabled else 0,
             1 if body.execution_file_logging else 0),
        )
        return body
```

In `cairn/src/cairn/server/routers/projects.py`, add `from cairn.server import execstore` to the imports, and change `delete_project`:

```python
@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    execstore.delete_project_logs(project_id)  # best-effort; ignore_errors internally
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_settings_and_delete_executions.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/server/routers/settings.py cairn/src/cairn/server/routers/projects.py cairn/tests/test_settings_and_delete_executions.py
git commit -m "feat: settings toggles endpoint + cascade-delete log dir with project"
```

---

### Task 8: Dispatcher client — report_execution

**Files:**
- Modify: `cairn/src/cairn/dispatcher/protocol/client.py`
- Test: `cairn/tests/test_client_report_execution.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_client_report_execution.py
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.app import app
from cairn.dispatcher.protocol.client import CairnClient


class _Adapter:
    """Route CairnClient requests into the in-process FastAPI TestClient."""
    def __init__(self, tc): self.tc = tc

    def request(self, method, url, json=None, params=None, timeout=None):
        path = url.replace("http://test", "")
        return self.tc.request(method, path, json=json, params=params)


def test_report_execution_posts_and_returns_ok(tmp_path, monkeypatch):
    db._db_path = None
    db.configure(tmp_path / "cairn.db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (id, title, created_at) VALUES ('p1','t','now')")
    tc = TestClient(app)
    client = CairnClient("http://test")
    monkeypatch.setattr(client, "_session", lambda: _Adapter(tc))
    result = client.report_execution("p1", {
        "phase": "explore", "worker_name": "w", "command": ["x"], "prompt": "p",
        "outcome": "success", "started_at": "t0", "ended_at": "t1", "duration_ms": 1,
        "stdout": "o", "stderr": "",
    })
    assert result.ok
    assert result.status_code in (201, 204)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_client_report_execution.py -v`
Expected: FAIL (`AttributeError: 'CairnClient' object has no attribute 'report_execution'`).

- [ ] **Step 3: Implement**

Add to `CairnClient` in `cairn/src/cairn/dispatcher/protocol/client.py` (after `create_intent`):

```python
    def report_execution(self, project_id: str, payload: dict[str, Any]) -> ApiResult:
        return self._request_json("POST", f"/projects/{project_id}/executions", json=payload)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_client_report_execution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/dispatcher/protocol/client.py cairn/tests/test_client_report_execution.py
git commit -m "feat: CairnClient.report_execution"
```

---

### Task 9: ExecutionRecorder — capture per task and report on finish

Single place that records the *decisive* worker process per task, then reports once with the final outcome. Reading the settings toggles decides whether to gather/ship output and how much.

**Files:**
- Modify: `cairn/src/cairn/dispatcher/tasks/common.py`
- Test: `cairn/tests/test_execution_recorder.py`

- [ ] **Step 1: Write the failing test**

```python
# cairn/tests/test_execution_recorder.py
from __future__ import annotations

from cairn.dispatcher.tasks.common import ExecutionRecorder
from cairn.dispatcher.runtime.process import ProcessResult


class _FakeSettings:
    def __init__(self, rec=True, files=True):
        self.execution_record_enabled = rec
        self.execution_file_logging = files


class _FakeClient:
    def __init__(self, settings):
        self._settings = settings
        self.calls = []

    def get_settings(self):
        return self._settings

    def report_execution(self, project_id, payload):
        self.calls.append((project_id, payload))
        class R: ok = True; status_code = 201
        return R()


def _result(stdout="out", rc=0):
    return ProcessResult(returncode=rc, stdout=stdout, stderr="", timed_out=False, cancelled=False)


def test_recorder_reports_decisive_process_with_outcome():
    client = _FakeClient(_FakeSettings())
    rec = ExecutionRecorder(client, project_id="p1", intent_id="i001",
                            worker_name="w", model="m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result("hello"))
    rec.set_produced_fact("f001")
    rec.finish("success")
    assert len(client.calls) == 1
    _, payload = client.calls[0]
    assert payload["phase"] == "explore"
    assert payload["outcome"] == "success"
    assert payload["produced_fact_id"] == "f001"
    assert payload["stdout"] == "hello"


def test_recorder_noop_when_record_disabled():
    client = _FakeClient(_FakeSettings(rec=False))
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result())
    rec.finish("success")
    assert client.calls == []


def test_recorder_caps_stdout_to_64k_when_file_logging_off():
    client = _FakeClient(_FakeSettings(files=False))
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result("x" * 500_000))
    rec.finish("success")
    payload = client.calls[0][1]
    assert len(payload["stdout"].encode()) <= 64 * 1024 + 200


def test_recorder_skips_when_no_process_recorded():
    client = _FakeClient(_FakeSettings())
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.finish("failed")  # healthcheck failed before any process
    assert client.calls == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_execution_recorder.py -v`
Expected: FAIL (no `ExecutionRecorder`).

- [ ] **Step 3: Implement recorder in common.py**

Add near the top of `cairn/src/cairn/dispatcher/tasks/common.py` (after existing imports):

```python
from cairn.execlog import redact_command, redact_text, truncate_head_tail

FILE_SHIP_CAP = 1_000_000   # ship at most ~1MB when files are on (server caps file too)
NOFILE_SHIP_CAP = 64 * 1024  # ship only inline-sized output when files are off
```

Add the class to the same file:

```python
class ExecutionRecorder:
    """Accumulates the decisive worker process for one task and reports it once."""

    def __init__(self, client, project_id: str, intent_id: str | None,
                 worker_name: str, model: str | None):
        self._client = client
        self._project_id = project_id
        self._intent_id = intent_id
        self._worker_name = worker_name
        self._model = model
        self._pending: dict | None = None
        self._started_at = utcnow_compact()
        self._produced_fact_id: str | None = None
        self._produced_intent_ids: list[str] = []

    def record(self, *, phase: str, command: list[str], prompt: str, result) -> None:
        self._pending = {"phase": phase, "command": list(command), "prompt": prompt,
                         "stdout": result.stdout or "", "stderr": result.stderr or "",
                         "exit_code": result.returncode}

    def set_produced_fact(self, fact_id: str | None) -> None:
        if fact_id:
            self._produced_fact_id = fact_id

    def add_produced_intent(self, intent_id: str | None) -> None:
        if intent_id:
            self._produced_intent_ids.append(intent_id)

    def finish(self, outcome: str) -> None:
        if self._pending is None:
            return
        try:
            settings = self._client.get_settings()
        except Exception:  # never let logging break the task
            return
        if not settings.execution_record_enabled:
            return
        cap = FILE_SHIP_CAP if settings.execution_file_logging else NOFILE_SHIP_CAP
        out = truncate_head_tail(redact_text(self._pending["stdout"]), cap)
        err = truncate_head_tail(redact_text(self._pending["stderr"]), cap)
        payload = {
            "phase": self._pending["phase"], "intent_id": self._intent_id,
            "worker_name": self._worker_name, "model": self._model,
            "command": redact_command(self._pending["command"]),
            "prompt": redact_text(self._pending["prompt"]),
            "stdout": out.text, "stderr": err.text,
            "exit_code": self._pending["exit_code"], "outcome": outcome,
            "started_at": self._started_at, "ended_at": utcnow_compact(),
            "duration_ms": 0,
            "produced_fact_id": self._produced_fact_id,
            "produced_intent_ids": self._produced_intent_ids,
        }
        try:
            self._client.report_execution(self._project_id, payload)
        except Exception:
            LOG.warning("execution report failed project=%s intent=%s",
                        self._project_id, self._intent_id)
```

Add the timestamp helper to the same file:

```python
def utcnow_compact() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd cairn && uv run pytest tests/test_execution_recorder.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cairn/src/cairn/dispatcher/tasks/common.py cairn/tests/test_execution_recorder.py
git commit -m "feat: ExecutionRecorder for per-task execution reporting"
```

---

### Task 10: Wire the recorder into explore / bootstrap / reason

Each task: create a recorder, record the decisive process, capture produced ids, and `finish()` in a `finally`. The recorder keeps only the last `record()` call, so success reports the execute process and a conclude-fallback reports the conclude process automatically.

**Files:**
- Modify: `cairn/src/cairn/dispatcher/tasks/explore.py`
- Modify: `cairn/src/cairn/dispatcher/tasks/bootstrap.py`
- Modify: `cairn/src/cairn/dispatcher/tasks/reason.py`
- Test: `cairn/tests/test_mock_end_to_end.py` (existing — assert records appear)

- [ ] **Step 1: Add a failing assertion to the mock end-to-end test**

Open `cairn/tests/test_mock_end_to_end.py`, find `test_mock_scheduler_runs_reason_explore_reason_complete_chain`, and after the chain completes add:

```python
    # execution records were captured for the run
    with db.get_conn() as conn:
        execs = conn.execute("SELECT phase, outcome FROM executions WHERE project_id = ?",
                             (project_id,)).fetchall()
    assert len(execs) >= 1
    assert any(e["outcome"] == "success" for e in execs)
```

(If `db` / `project_id` are named differently in that test, match the local names already in scope.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd cairn && uv run pytest tests/test_mock_end_to_end.py -k reason_explore -v`
Expected: FAIL (0 execution rows).

- [ ] **Step 3: Edit explore.py**

In `run_explore_task`, after `driver = get_driver(worker.type)` (line ~41) create the recorder:

```python
    recorder = ExecutionRecorder(client, project.project.id, intent.id, worker.name,
                                 worker.env.get(_model_env_key(worker)))
    outcome = "failed"
```

Wrap the existing body so every `return X` becomes `outcome = X; return outcome` is unnecessary — instead, rename the current function body into the try and convert the final `return`/`except` to set `outcome`. The minimal reliable pattern: change the outer `try/finally` (currently `finally: lease.stop()`) to:

```python
    try:
        ... existing body, but replace every `return VALUE` with:
            outcome = VALUE
            return outcome
    except Exception:
        LOG.exception(...)  # keep existing
        best_effort_release(...)
        outcome = "failed"
        return outcome
    finally:
        recorder.finish(outcome)
        lease.stop()
```

Record the decisive process right after each `_run_process(...)` call. After the execute call (line ~117-126) add:

```python
        recorder.record(phase="explore", command=execute.argv, prompt=prompt, result=first)
```

Capture the produced fact at the success path: replace the `return write_conclude_result(...)` (line ~195) with:

```python
            conclude = write_conclude_result_with_fact_id(
                client, project.project.id, intent.id, worker.name, description,
                source="explore_execute", phase_ms=execute_ms,
                total_ms=int((time.perf_counter() - task_started) * 1000),
            )
            recorder.set_produced_fact(conclude.fact_id)
            outcome = conclude.status
            return outcome
```

Add `write_conclude_result_with_fact_id` to the imports from `tasks.common`, and `from cairn.dispatcher.tasks.common import ExecutionRecorder`. Add a small helper at module bottom to read the model env key per worker type:

```python
def _model_env_key(worker) -> str:
    return {"opencode": "OPENCODE_MODEL", "pi": "PI_MODEL",
            "claudecode": "ANTHROPIC_MODEL", "codex": "CODEX_MODEL"}.get(worker.type, "MODEL")
```

In `_try_conclude_fallback`, add a `recorder` parameter (pass it from the caller), record after the conclude `_run_process` (line ~318-327):

```python
    recorder.record(phase="conclude", command=conclude_argv, prompt=prompt, result=result)
```

and at its success path (line ~386) capture the fact via `write_conclude_result_with_fact_id` + `recorder.set_produced_fact(...)` the same way as above (set `outcome` and return it). Update both `_try_conclude_fallback(...)` call sites to pass `recorder`.

- [ ] **Step 4: Edit bootstrap.py (same pattern)**

`run_bootstrap_task`: create `recorder = ExecutionRecorder(client, project.project.id, intent.id, worker.name, worker.env.get(_model_env_key(worker)))`, `outcome = "failed"`, wrap body in try/except/finally calling `recorder.finish(outcome)`, and:
- after the execute `_run_process` (phase `"bootstrap"`, line ~116) → `recorder.record(phase="bootstrap", command=<execute argv var>, prompt=<prompt var>, result=<result var>)`.
- the success path already uses `write_conclude_result_with_fact_id` (line 424) returning `conclude.fact_id` → add `recorder.set_produced_fact(conclude.fact_id)`.
- thread `recorder` into bootstrap's `_try_conclude_fallback` and record with `phase="conclude"`.
- replace bare `return VALUE` with `outcome = VALUE; return outcome`.

Reuse the same `_model_env_key` helper (define once in bootstrap.py too, or import from explore.py).

- [ ] **Step 5: Edit reason.py (same pattern, intent_id=None)**

`run_reason_task`: `recorder = ExecutionRecorder(client, project.project.id, None, worker.name, worker.env.get(_model_env_key(worker)))`. Record after the reason `_run_process` (phase `"reason"`, line ~136): `recorder.record(phase="reason", command=<execute argv>, prompt=<prompt>, result=<result>)`. At the `create_intent` success site (line ~240): after a successful response, extract the new intent id from `response.data` and call `recorder.add_produced_intent(new_id)`. At `complete` (line ~215) leave produced ids empty (goal). Wrap body in try/except/finally → `recorder.finish(outcome)`.

- [ ] **Step 6: Run the full suite**

Run: `cd cairn && uv run pytest -v`
Expected: PASS, including the new assertion in `test_mock_end_to_end.py`.

- [ ] **Step 7: Commit**

```bash
git add cairn/src/cairn/dispatcher/tasks/explore.py cairn/src/cairn/dispatcher/tasks/bootstrap.py cairn/src/cairn/dispatcher/tasks/reason.py cairn/tests/test_mock_end_to_end.py
git commit -m "feat: report executions from explore/bootstrap/reason tasks"
```

---

### Task 11: Frontend — settings toggles, Runtime tab, copy/detail (manual test)

No JS test runner exists; verify in a browser. Work in `cairn/src/cairn/server/static/index.html`.

- [ ] **Step 1: Settings panel toggles**

In the gear (⚙) settings panel markup, add two checkboxes bound to new state and persisted via PUT `/settings`. Locate the existing settings form (search `intent_timeout`), and add:

```html
<label class="flex items-center justify-between gap-3 text-xs">
  <span class="text-slate-500">Execution records (Runtime tab)</span>
  <input type="checkbox" x-model="settings.execution_record_enabled" @change="saveSettings()">
</label>
<label class="flex items-center justify-between gap-3 text-xs">
  <span class="text-slate-500">Full .log file logging (disk)</span>
  <input type="checkbox" x-model="settings.execution_file_logging" @change="saveSettings()">
</label>
```

Ensure `saveSettings()` PUTs the full Settings object (intent_timeout, reason_timeout, execution_record_enabled, execution_file_logging) and that the loader reads the two new fields.

- [ ] **Step 2: Runtime tab button + panel**

Next to the existing `Log` tab button (search `sideTab = 'log'`), add:

```html
<button @click="sideTab = 'runtime'" class="flex-1 px-3 py-2.5 text-xs font-medium transition"
  :class="sideTab === 'runtime' ? 'text-brand-600 border-b-2 border-brand-500' : 'text-slate-400 hover:text-slate-600'">Runtime</button>
```

Add the panel (after the Log panel `</div>`):

```html
<div x-show="sideTab === 'runtime'" class="flex-1 overflow-y-auto p-4 space-y-2">
  <div class="flex items-center justify-between mb-2">
    <span class="text-[11px] text-slate-400 uppercase tracking-wider">Executions</span>
    <a :href="`/projects/${project.project.id}/executions/logs.zip`"
       class="text-[11px] text-brand-600 hover:underline" x-show="executions.some(e => e.has_log)">Download all (zip)</a>
  </div>
  <template x-if="executions.length === 0">
    <p class="text-sm text-slate-300">No execution records yet</p>
  </template>
  <template x-for="e in executions" :key="e.id">
    <div class="rounded-lg border border-slate-200 p-2.5 text-xs">
      <div class="flex items-center gap-2">
        <span class="font-mono text-slate-600" x-text="e.phase"></span>
        <span class="px-1.5 rounded text-[10px]"
              :class="e.outcome === 'success' ? 'bg-teal-50 text-teal-600' : 'bg-rose-50 text-rose-500'"
              x-text="e.outcome"></span>
        <span class="text-slate-400" x-text="e.worker_name + ' · ' + (e.model || '')"></span>
        <span class="ml-auto text-slate-300" x-text="formatTime(e.started_at)"></span>
      </div>
      <button class="mt-1 text-brand-600 hover:underline" @click="openExecutionDetail(e.id)">detail</button>
      <a x-show="e.has_log" class="ml-2 text-brand-600 hover:underline"
         :href="`/projects/${project.project.id}/executions/${e.id}/log`">download log</a>
    </div>
  </template>
</div>
```

- [ ] **Step 3: State + polling + detail fetch**

In the Alpine component data add `executions: []` and `executionDetail: null`. Add methods:

```javascript
async loadExecutions() {
  if (!this.project) return;
  try {
    const r = await fetch(`/projects/${this.project.project.id}/executions`);
    if (r.ok) this.executions = await r.json();
  } catch (e) {}
},
async openExecutionDetail(id) {
  const r = await fetch(`/projects/${this.project.project.id}/executions/${id}`);
  if (r.ok) { this.executionDetail = await r.json(); this.showExecutionModal = true; }
},
```

Call `this.loadExecutions()` wherever the project graph is (re)loaded/polled (search for the existing project refresh/poll function and add the call alongside it).

- [ ] **Step 4: Detail modal**

Add a modal that renders `executionDetail` (command joined, prompt, stdout_inline, stderr_inline, a "truncated" note when `executionDetail.truncated`, and a download link when `executionDetail.has_log`).

- [ ] **Step 5: copy/detail on result boxes**

On the timeline result boxes (search `timelineEventBadge`), add two icon buttons. `copy` writes the entry text via `navigator.clipboard.writeText(...)`. `detail` finds the matching execution and opens it:

```javascript
executionForResult(entry) {
  // FACT/CONCLUDE: the successful explore/bootstrap whose intent_id concluded into this fact
  // INTENT: the reason execution whose produced_intent_ids contains this intent id
  if (entry.type === 'intent') {
    return this.executions.find(e => (e.produced_intent_ids || []).includes(entry.id)) || null;
  }
  const intentId = entry.intentId || (this.factProducingIntent(entry.id) || {}).id;
  return this.executions.find(e => e.intent_id === intentId && e.outcome === 'success') || null;
},
```

Hide both icons while `replay.active` (wrap with `x-show="!replay.active"`). When `executionForResult(entry)` is null, render the `detail` icon disabled/greyed with title "No execution record".

- [ ] **Step 6: Manual verification**

Start services (`uv run cairn serve` + a dispatcher against a real/mock worker), open a project, and verify:
1. Runtime tab lists executions in near-real-time as phases complete.
2. `detail` on a fact opens the explore/bootstrap execution; on an intent opens the reason execution.
3. `copy` copies the result text.
4. "download log" and "Download all (zip)" work; turning off **file logging** hides downloads for new runs; turning off **records** stops new Runtime entries.
5. During replay, copy/detail icons are hidden.
6. reopen-created facts/intents show `detail` greyed ("No execution record").

- [ ] **Step 7: Commit**

```bash
git add cairn/src/cairn/server/static/index.html
git commit -m "feat: Runtime tab, settings toggles, result copy/detail binding"
```

---

### Task 12: Documentation

**Files:**
- Modify: `docs/specs/server-protocol.md`
- Modify: `docs/specs/dispatcher-design.md`

- [ ] **Step 1: Document the endpoints + settings**

In `docs/specs/server-protocol.md`, add an `Executions` section documenting: `POST /projects/{id}/executions` (body = ExecutionReport; 201 with ExecutionDetail, or 204 when records disabled), `GET /projects/{id}/executions`, `GET /projects/{id}/executions/{exec_id}`, `GET /projects/{id}/executions/{exec_id}/log`, `GET /projects/{id}/executions/logs.zip`, and the two new `settings` fields (`execution_record_enabled`, `execution_file_logging`, both default true).

- [ ] **Step 2: Document the dispatcher side**

In `docs/specs/dispatcher-design.md`, add: the ExecutionRecorder reporting path (one decisive process per task), secret redaction before upload, head/tail truncation (64KB ship when files off, ≤1MB when on), and that records are gated by the server settings toggles read via `get_settings()`.

- [ ] **Step 3: Commit**

```bash
git add docs/specs/server-protocol.md docs/specs/dispatcher-design.md
git commit -m "docs: execution logging endpoints + dispatcher reporting"
```

---

## Self-Review Notes (author)

- **Spec coverage:** data model (T2/T3), redaction (T1, applied T5/T9/T6), truncation 64KB/1MB (T1/T5/T6/T9), server-owned files + per-project dir (T4), atomic write (T4), exec_id atomic + filename uniqueness (T2/T4/T5), endpoints incl. single-log + zip (T6), settings two-level toggles default-on (T2/T3/T7), dispatcher report path (T8/T9/T10), cascade delete dir+rows (T7/T2), multi-project isolation via per-project dir + scoped counter (T4/T5), reopen no-CASCADE-on-intent (T2) + greyed detail for human products (T11), replay icons hidden (T11), Runtime tab (T11), docs (T12). All spec sections map to a task.
- **No CASCADE on intent_id** is enforced by the schema in T2 and tested implicitly (only project delete removes rows, T7).
- **Type consistency:** `ExecutionReport`/`ExecutionSummary`/`ExecutionDetail`/`Settings` field names are identical across T3 (definition), T5 (rows), T6 (router), T9 (payload keys). `report_execution(project_id, payload)` signature matches between T8 and T9. `insert_execution(..., inline_limit=...)` matches T5 and T6 (`INLINE_LIMIT`). `executions_root()` defined T2, used T4.
- **Known simplification (documented):** one record per task (decisive process), not one per sub-process; phase is `explore`/`bootstrap`/`reason`/`conclude`. Matches spec's phase enum.
