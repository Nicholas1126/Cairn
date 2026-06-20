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
