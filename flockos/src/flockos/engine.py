"""CairnAgentEngine: run a Cairn host agent (claude/codex/opencode/pi) as a
one-shot, structured-output Flock engine. Mirrors OpenClawEngine but executes
in-process on the host instead of calling an HTTP gateway."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cairn.dispatcher.config import WorkerConfig
from pydantic import BaseModel
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

    def _build_prompt(self, agent: Any, inputs, output_group) -> str:
        decl = output_group.outputs[0]
        schema = decl.spec.model.model_json_schema()
        input_payloads = [dict(a.payload) for a in inputs.artifacts]
        description = (getattr(agent, "description", "") or "").strip()
        lines = [
            "Your ENTIRE response must be a single valid JSON object matching the schema below.",
            "Do not include any text, explanation, markdown fences, or commentary — only the raw JSON object.",
            "The response will be parsed directly by a JSON schema validator.",
        ]
        if description:
            lines.append(f"Task: {description}")
        lines.append(f"Schema: {json.dumps(schema, ensure_ascii=False)}")
        if len(input_payloads) == 1:
            lines.append(f"Input: {json.dumps(input_payloads[0], ensure_ascii=False)}")
        else:
            lines.append(f"Inputs: {json.dumps(input_payloads, ensure_ascii=False)}")
        return "\n".join(lines)

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found in agent output")
        return json.loads(text[start : end + 1])

    async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult:
        if not inputs.artifacts or not output_group.outputs:
            return EvalResult.empty(state=dict(inputs.state))

        model_cls = output_group.outputs[0].spec.model
        driver = get_driver(self.worker.type)
        prompt = self._build_prompt(agent, inputs, output_group)

        attempts = max(1, self.retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            run_prompt = prompt
            if attempt > 0:
                run_prompt = (
                    prompt
                    + "\n\nYour previous response was not valid JSON. "
                    "Respond with ONLY the raw JSON object, nothing else."
                )
            argv, session = self._build_argv(run_prompt)
            stdout, stderr, rc = self._run(argv, {}, self.cwd, self.timeout)
            text = driver.extract_response_text(stdout, stderr)
            try:
                data = self._extract_json(text)
                instance = model_cls(**data)
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                last_error = exc
                # A nonzero exit with no parseable output is a hard failure
                # (auth/config/timeout — LocalManagedProcess returns rc=137 on
                # timeout rather than raising). Retrying the same broken config
                # won't help, so stop now and surface stderr for diagnosis.
                if rc != 0:
                    return EvalResult(
                        artifacts=[],
                        state=dict(inputs.state),
                        logs=[
                            f"CairnAgentEngine: {self.worker.type} exited {rc} with no "
                            f"valid {model_cls.__name__}; stderr: {stderr.strip()[:500]}"
                        ],
                    )
                continue
            # Parse succeeded — return the artifact regardless of rc (some CLIs
            # emit valid output then exit nonzero on a non-fatal warning).
            return EvalResult.from_object(instance, agent=agent)

        return EvalResult(
            artifacts=[],
            state=dict(inputs.state),
            logs=[f"CairnAgentEngine failed to produce valid {model_cls.__name__}: {last_error}"],
        )


class CairnConfig(BaseModel):
    """Alias -> Cairn WorkerConfig mapping for cairn_agent()."""

    workers: dict[str, WorkerConfig] = {}
    default_timeout: int = 600
    default_retries: int = 1

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_dispatch(cls, path: str | Path, **kw) -> "CairnConfig":
        from cairn.dispatcher.config import DispatchConfig

        dispatch = DispatchConfig.load(Path(path).expanduser())
        return cls(workers={w.name: w for w in dispatch.workers}, **kw)

    def build_engine(
        self,
        alias: str,
        *,
        timeout: int | None = None,
        retries: int | None = None,
        cwd: str | None = None,
    ) -> CairnAgentEngine:
        if alias not in self.workers:
            raise ValueError(f"unknown cairn worker alias: {alias!r}")
        return CairnAgentEngine(
            worker=self.workers[alias],
            timeout=timeout if timeout is not None else self.default_timeout,
            retries=retries if retries is not None else self.default_retries,
            cwd=cwd,
        )


def cairn_agent(flock, config: CairnConfig, alias: str, name: str):
    """Mirror of Flock.openclaw_agent: build an AgentBuilder pre-wired with a
    CairnAgentEngine for the given worker alias."""
    builder = flock.agent(name)
    builder.with_engines(config.build_engine(alias))
    return builder
