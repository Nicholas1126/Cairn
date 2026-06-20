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
