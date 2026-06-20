from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path

from cairn.server import db

FILE_CAP_BYTES = 10_000_000  # 10MB hard cap per .log


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
