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
