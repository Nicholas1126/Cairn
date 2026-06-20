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
