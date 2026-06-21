"""CairnAgentEngine: run a Cairn host agent (claude/codex/opencode/pi) as a
one-shot, structured-output Flock engine. Mirrors OpenClawEngine but executes
in-process on the host instead of calling an HTTP gateway."""

from __future__ import annotations

from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.runtime.local.process import LocalManagedProcess
from cairn.dispatcher.workers.registry import get_driver
from flock.components.agent.base import EngineComponent
from flock.utils.runtime import EvalResult


class CairnAgentEngine(EngineComponent):
    """Flock engine that delegates evaluation to a Cairn host agent CLI."""

    worker: WorkerConfig
    timeout: int = 600
    retries: int = 1
    cwd: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    def _build_argv(self, prompt: str) -> tuple[list[str], str | None]:
        driver = get_driver(self.worker.type)
        session = driver.prepare_session()
        result = driver.build_execute(self.worker, prompt, session)
        return result.argv, result.session

    def _run(
        self, argv: list[str], extra_env: dict[str, str], cwd: str | None, timeout: int
    ) -> tuple[str, str, int]:
        env = {**self.worker.env, **extra_env}
        proc = LocalManagedProcess(argv, env, cwd)
        proc.start()
        res = proc.communicate(timeout=timeout)
        return res.stdout, res.stderr, res.returncode
