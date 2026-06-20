from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.execlog import redact_command, redact_text, truncate_head_tail

HEALTHCHECK_COMMUNICATE_GRACE_SECONDS = 10
PROCESS_COMMUNICATE_GRACE_SECONDS = 15
LOG_PREVIEW_LIMIT = 1200
GRAPH_SNAPSHOT_ROOT = "/tmp/cairn-prompts"
FILE_SHIP_CAP = 10_000_000   # ship at most ~10MB when files are on (server caps file too)
NOFILE_SHIP_CAP = 64 * 1024  # ship only inline-sized output when files are off
LOG = logging.getLogger(__name__)


def utcnow_iso() -> str:
    # ISO 8601 (matches the server's utcnow) so the browser's `new Date()` can parse it.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def model_env_key(worker) -> str:
    return {"opencode": "OPENCODE_MODEL", "pi": "PI_MODEL",
            "claudecode": "ANTHROPIC_MODEL", "codex": "CODEX_MODEL"}.get(worker.type, "MODEL")


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
        self._started_at = utcnow_iso()
        self._started_perf = time.perf_counter()
        self._produced_fact_id: str | None = None
        self._produced_intent_ids: list[str] = []

    def record(self, *, phase: str, command: list[str], prompt: str, result) -> None:
        """Capture a worker process for this task. Called after each worker run;
        the LAST call wins — i.e., the decisive process is reported. In a
        conclude-fallback path the conclude process (phase="conclude")
        intentionally overwrites the earlier execute process."""
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
            "started_at": self._started_at, "ended_at": utcnow_iso(),
            "duration_ms": int((time.perf_counter() - self._started_perf) * 1000),
            "produced_fact_id": self._produced_fact_id,
            "produced_intent_ids": self._produced_intent_ids,
        }
        try:
            self._client.report_execution(self._project_id, payload)
        except Exception:
            LOG.warning("execution report failed project=%s intent=%s",
                        self._project_id, self._intent_id)


@dataclass(slots=True)
class HealthcheckRun:
    result: ProcessResult
    duration_ms: int


@dataclass(slots=True)
class ConcludeWriteResult:
    status: str
    fact_id: str | None = None


def preview(text: str, limit: int = LOG_PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def did_timeout(result: ProcessResult) -> bool:
    return not result.cancelled and (result.timed_out or result.returncode in (124, 137))


def cancel_reason(result: ProcessResult, cancellation: TaskCancellation | None = None) -> str | None:
    if result.cancelled:
        return result.cancel_reason or "cancelled"
    if cancellation is not None:
        return cancellation.reason
    return None


def communicate_timeout(timeout_seconds: int, grace_seconds: int = PROCESS_COMMUNICATE_GRACE_SECONDS) -> int:
    return timeout_seconds + grace_seconds


def task_healthcheck_enabled(config: DispatchConfig) -> bool:
    return config.runtime.worker_healthcheck == "startup_and_task"


def write_graph_snapshot_reference(
    container_manager: ContainerManager,
    container_name: str,
    graph_yaml: str,
    *,
    phase: str,
) -> str:
    path = f"{container_manager.snapshot_root()}/{phase}-{uuid.uuid4().hex[:12]}/graph.yaml"
    container_manager.write_text_file(container_name, path, graph_yaml)
    return (
        "The graph YAML snapshot is stored in this file inside the current container:\n\n"
        f"{path}\n\n"
        "Before using the graph, read the entire file and treat its contents as the YAML snapshot "
        "for this Graph section."
    )


def run_healthcheck(
    container_manager: ContainerManager,
    container_name: str,
    worker: WorkerConfig,
    command: list[str],
    *,
    timeout_seconds: int,
    lease: HeartbeatLease | None = None,
    cancellation: TaskCancellation | None = None,
) -> HealthcheckRun:
    process = container_manager.build_exec_process(
        container_name,
        dict(worker.env),
        command,
        timeout_seconds=timeout_seconds,
    )
    process.start()
    if lease is not None:
        lease.attach_process(process)
    if cancellation is not None:
        cancellation.attach_process(process)
    started = time.perf_counter()
    try:
        result = process.communicate(timeout=communicate_timeout(timeout_seconds, HEALTHCHECK_COMMUNICATE_GRACE_SECONDS))
    finally:
        if lease is not None:
            lease.attach_process(None)
        if cancellation is not None:
            cancellation.attach_process(None)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return HealthcheckRun(result=result, duration_ms=duration_ms)


def run_worker_process(
    container_manager: ContainerManager,
    container_name: str,
    worker: WorkerConfig,
    argv: list[str],
    *,
    phase: str,
    timeout_seconds: int,
    lease: HeartbeatLease | None = None,
    cancellation: TaskCancellation | None = None,
) -> ProcessResult:
    LOG.info(
        "starting container exec container=%s worker=%s phase=%s timeout=%ss",
        container_name,
        worker.name,
        phase,
        timeout_seconds,
    )
    process = container_manager.build_exec_process(
        container_name,
        dict(worker.env),
        argv,
        timeout_seconds=timeout_seconds,
    )
    process.start()
    if lease is not None:
        lease.attach_process(process)
    if cancellation is not None:
        cancellation.attach_process(process)
    try:
        return process.communicate(timeout=communicate_timeout(timeout_seconds))
    finally:
        if lease is not None:
            lease.attach_process(None)
        if cancellation is not None:
            cancellation.attach_process(None)


def project_allows_conclude_fallback(client: CairnClient, project_id: str, *, worker_name: str, intent_id: str) -> bool:
    project = client.get_project(project_id)
    if project.project.status == "active":
        return True
    LOG.info(
        "skip conclude fallback because project is no longer active project=%s intent=%s worker=%s status=%s",
        project_id,
        intent_id,
        worker_name,
        project.project.status,
    )
    return False


def best_effort_release_reason(client: CairnClient, project_id: str, worker_name: str) -> None:
    response = client.release_reason(project_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning(
            "reason release failed project=%s worker=%s status=%s",
            project_id,
            worker_name,
            response.status_code,
        )
    elif response.ok:
        LOG.info("released reason project=%s worker=%s", project_id, worker_name)
    else:
        LOG.info(
            "reason release skipped project=%s worker=%s status=%s",
            project_id,
            worker_name,
            response.status_code,
        )


def write_conclude_result(
    client: CairnClient,
    project_id: str,
    intent_id: str,
    worker_name: str,
    description: str,
    *,
    source: str,
    phase_ms: int,
    total_ms: int | None = None,
) -> str:
    return write_conclude_result_with_fact_id(
        client,
        project_id,
        intent_id,
        worker_name,
        description,
        source=source,
        phase_ms=phase_ms,
        total_ms=total_ms,
    ).status


def write_conclude_result_with_fact_id(
    client: CairnClient,
    project_id: str,
    intent_id: str,
    worker_name: str,
    description: str,
    *,
    source: str,
    phase_ms: int,
    total_ms: int | None = None,
) -> ConcludeWriteResult:
    response = client.conclude(project_id, intent_id, worker_name, description)
    if response.ok:
        fact_id: str | None = None
        if isinstance(response.data, dict):
            fact = response.data.get("fact")
            if isinstance(fact, dict):
                candidate = fact.get("id")
                if isinstance(candidate, str) and candidate:
                    fact_id = candidate
        if total_ms is None:
            LOG.info(
                "intent concluded project=%s intent=%s worker=%s source=%s phase_ms=%s",
                project_id,
                intent_id,
                worker_name,
                source,
                phase_ms,
            )
        else:
            LOG.info(
                "intent concluded project=%s intent=%s worker=%s source=%s phase_ms=%s total_ms=%s",
                project_id,
                intent_id,
                worker_name,
                source,
                phase_ms,
                total_ms,
            )
        return ConcludeWriteResult(status="success", fact_id=fact_id)
    if response.status_code == 403:
        LOG.info(
            "project became inactive during conclude project=%s intent=%s worker=%s",
            project_id,
            intent_id,
            worker_name,
        )
    else:
        LOG.warning(
            "conclude write failed project=%s intent=%s worker=%s status=%s body=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
            response.text,
        )
    best_effort_release(client, project_id, intent_id, worker_name)
    return ConcludeWriteResult(status="failed", fact_id=None)


def best_effort_release(client: CairnClient, project_id: str, intent_id: str, worker_name: str) -> None:
    response = client.release(project_id, intent_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning(
            "release failed project=%s intent=%s worker=%s status=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
        )
    elif response.ok:
        LOG.info("released intent project=%s intent=%s worker=%s", project_id, intent_id, worker_name)
    else:
        LOG.info(
            "release skipped project=%s intent=%s worker=%s status=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
        )
