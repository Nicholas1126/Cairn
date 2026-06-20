from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.models import Settings

router = APIRouter(tags=["settings"])


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
