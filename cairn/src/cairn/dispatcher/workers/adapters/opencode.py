from __future__ import annotations

import json
from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.base import DriverResult, WorkerDriver


# opencode `--format json` event-stream field names. VERIFIED against opencode
# 1.15.0 (dashscope openai-compatible provider). Each stdout line is one JSON
# event; the session id is a top-level "sessionID" on every event, and assistant
# text is carried as `{"type":"text", "part":{"type":"text","text":...}}`, e.g.:
#   {"type":"step_start","sessionID":"ses_...","part":{...,"type":"step-start"}}
#   {"type":"text","sessionID":"ses_...","part":{"type":"text","text":"pong",...}}
# `_SESSION_ID_FIELDS` is intentionally just "sessionID" (no bare "id") so a
# message/part id can never be mistaken for the session id.
_SESSION_ID_FIELDS = ("sessionID",)
_ENVELOPE_KEYS = ("part",)
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
        # stdin is redirected from /dev/null: opencode's `run` blocks waiting on a
        # TTY/stdin otherwise. The dispatcher already execs workers with stdin=False,
        # but this keeps the command correct if invoked any other way.
        script = (
            'cfg="$1"\n'
            "shift 1\n"
            'exec env OPENCODE_CONFIG_CONTENT="$cfg" '
            "OPENCODE_DISABLE_AUTOUPDATE=1 "
            "OPENCODE_DISABLE_MODELS_FETCH=1 "
            'opencode "$@" </dev/null\n'
        )
        return [
            "/bin/sh",
            "-lc",
            script,
            "--",
            self._config_json(worker),
            *opencode_argv,
        ]

    def extract_session(self, session: str | None, stdout: str, stderr: str) -> str | None:
        if session:
            return session
        for event in self._iter_events(stdout):
            found = self._find_session_id(event)
            if found:
                return found
        return None

    def extract_response_text(self, stdout: str, stderr: str) -> str:
        parts: list[str] = []
        for event in self._iter_events(stdout):
            parts.extend(self._collect_text(event))
        return "\n".join(parts).strip() or stdout

    @staticmethod
    def _iter_events(stdout: str) -> list[dict[str, Any]]:
        text = stdout.strip()
        if not text:
            return []
        # opencode may emit a single JSON array or newline-delimited objects.
        try:
            whole = json.loads(text)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, list):
            return [e for e in whole if isinstance(e, dict)]
        if isinstance(whole, dict):
            return [whole]
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    @classmethod
    def _find_session_id(cls, event: dict[str, Any]) -> str | None:
        for key in _SESSION_ID_FIELDS:
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        for key in _ENVELOPE_KEYS:
            nested = event.get(key)
            if isinstance(nested, dict):
                found = cls._find_session_id(nested)
                if found:
                    return found
        return None

    @classmethod
    def _collect_text(cls, event: dict[str, Any]) -> list[str]:
        out: list[str] = []
        if event.get("type") == _TEXT_PART_TYPE:
            text = event.get("text")
            if isinstance(text, str) and text:
                out.append(text)
        for key in _ENVELOPE_KEYS:
            nested = event.get(key)
            if isinstance(nested, dict):
                out.extend(cls._collect_text(nested))
            elif isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        out.extend(cls._collect_text(item))
        return out

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
