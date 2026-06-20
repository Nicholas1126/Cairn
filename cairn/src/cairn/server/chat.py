from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import HTTPException

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.runtime.local import resolve
from cairn.dispatcher.runtime.local.runtime import LocalRuntime
from cairn.dispatcher.tasks.common import model_env_key
from cairn.dispatcher.workers.registry import get_driver
from cairn.execlog import redact_command, redact_text, truncate_head_tail
from cairn.server.db import cairn_home
from cairn.server.models import ChatTurnResult, ChatWorker

CHAT_TIMEOUT_SECONDS = 300
STDOUT_CAP = 64 * 1024


def dispatch_config_path() -> Path:
    return Path(os.environ.get("CAIRN_DISPATCH_CONFIG", "dispatch.yaml"))


def load_dispatch_config() -> DispatchConfig:
    path = dispatch_config_path()
    if not path.exists():
        raise HTTPException(400, f"dispatch config not found at {path}; set CAIRN_DISPATCH_CONFIG")
    return DispatchConfig.load(path)


def list_workers() -> list[ChatWorker]:
    config = load_dispatch_config()
    return [ChatWorker(name=w.name, type=w.type, model=w.env.get(model_env_key(w))) for w in config.workers]


def _chat_runtime() -> LocalRuntime:
    # parents[4] of cairn/src/cairn/server/chat.py is the repo root /Users/nicholas/project/ai/Cairn
    agents_source = Path(__file__).resolve().parents[4] / "container"
    return LocalRuntime(
        workspaces_root=str(cairn_home() / "chats"),
        completed_action="stop",
        agents_source=str(agents_source) if agents_source.exists() else None,
    )


def _find_worker(config: DispatchConfig, name: str) -> WorkerConfig:
    for w in config.workers:
        if w.name == name:
            return w
    raise HTTPException(404, f"worker not found: {name}")


def run_turn(worker_name: str, message: str, session: str | None) -> ChatTurnResult:
    config = load_dispatch_config()
    worker = _find_worker(config, worker_name)
    driver = get_driver(worker.type)
    runtime = _chat_runtime()
    return _run_turn(driver, runtime, worker, message, session, timeout=CHAT_TIMEOUT_SECONDS)


def _run_turn(driver, runtime, worker, message: str, session: str | None, *, timeout: int) -> ChatTurnResult:
    probe = resolve.probe_engine(worker.type)
    if not probe["launchable"]:
        binary = resolve.BINARY.get(worker.type, worker.type)
        return ChatTurnResult(
            reply=f"Engine '{binary}' is not launchable on this host (not installed / not on PATH). "
                  f"Configure it on the Engines page.",
            session=session, command=[], prompt=message, stdout="", exit_code=None,
            outcome="failed", duration_ms=0,
        )
    session_in = session or driver.prepare_session()
    result = driver.build_execute(worker, message, session_in)
    runtime.ensure_running(worker.name)
    proc = runtime.build_exec_process(worker.name, dict(worker.env), result.argv)
    proc.start()
    started = time.perf_counter()
    res = proc.communicate(timeout=timeout)
    duration_ms = int((time.perf_counter() - started) * 1000)
    session_out = driver.extract_session(result.session, res.stdout, res.stderr)
    reply = driver.extract_response_text(res.stdout, res.stderr)
    if res.timed_out:
        outcome = "timeout"
    elif res.returncode == 0:
        outcome = "success"
    else:
        outcome = "failed"
    out = truncate_head_tail(redact_text(res.stdout or ""), STDOUT_CAP)
    return ChatTurnResult(
        reply=reply, session=session_out, command=redact_command(result.argv),
        prompt=message, stdout=out.text, exit_code=res.returncode,
        outcome=outcome, duration_ms=duration_ms,
    )
