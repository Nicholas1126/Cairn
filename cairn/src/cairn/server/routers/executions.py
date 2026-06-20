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


# IMPORTANT: logs.zip route MUST be declared before /{exec_id} to avoid capture
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
