from __future__ import annotations

import json
from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.base import DriverResult, WorkerDriver


# opencode `--format json` event-stream field names. Confirm against a real
# captured stream in Task 3 and adjust here if the schema differs.
_SESSION_ID_FIELDS = ("sessionID", "sessionId", "session_id", "id")
_ENVELOPE_KEYS = ("properties", "info", "session", "message", "part", "data")
_TEXT_PART_TYPE = "text"

_DEFAULT_PROVIDER_NPM = "@ai-sdk/openai-compatible"


class OpenCodeDriver(WorkerDriver):
    type_name = "opencode"

    def build_healthcheck(self, worker: WorkerConfig) -> list[str]:
        model = worker.env["OPENCODE_MODEL"]
        return self._wrap(
            worker,
            ["run", "--pure", "-m", f"cairn/{model}", "--", "Reply with exactly pong."],
        )

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        argv = self._run_argv(worker, prompt, session)
        return DriverResult(argv=self._wrap(worker, argv), session=session)

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        return self._wrap(worker, self._run_argv(worker, prompt, session))

    @staticmethod
    def _run_argv(worker: WorkerConfig, prompt: str, session: str | None) -> list[str]:
        model = worker.env["OPENCODE_MODEL"]
        argv = [
            "run",
            "--pure",
            "--format",
            "json",
            "--dangerously-skip-permissions",
            "-m",
            f"cairn/{model}",
        ]
        if session:
            argv.extend(["-s", session])
        argv.extend(["--", prompt])
        return argv

    def _wrap(self, worker: WorkerConfig, opencode_argv: list[str]) -> list[str]:
        script = (
            'cfg="$1"\n'
            "shift 1\n"
            'exec env OPENCODE_CONFIG_CONTENT="$cfg" '
            "OPENCODE_DISABLE_AUTOUPDATE=1 "
            "OPENCODE_DISABLE_MODELS_FETCH=1 "
            'opencode "$@"\n'
        )
        return [
            "/bin/sh",
            "-lc",
            script,
            "--",
            self._config_json(worker),
            *opencode_argv,
        ]

    @staticmethod
    def _config_json(worker: WorkerConfig) -> str:
        env = worker.env
        model = env["OPENCODE_MODEL"]
        npm = env.get("OPENCODE_PROVIDER_NPM") or _DEFAULT_PROVIDER_NPM
        payload: dict[str, Any] = {
            "provider": {
                "cairn": {
                    "npm": npm,
                    "options": {
                        "baseURL": env["OPENCODE_BASE_URL"],
                        "apiKey": env["OPENCODE_API_KEY"],
                    },
                    "models": {model: {"name": model}},
                }
            }
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
