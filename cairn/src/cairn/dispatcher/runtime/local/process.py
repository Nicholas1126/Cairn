from __future__ import annotations

import os
import signal
import subprocess

from cairn.dispatcher.runtime.process import ProcessResult


class LocalManagedProcess:
    """Host-subprocess analog of the container ManagedProcess.

    Surface used by the task layer + HeartbeatLease/TaskCancellation:
    start() / communicate(timeout) / kill() / cancel(reason).
    Timeout is enforced here (no container `timeout` coreutil). On expiry or
    cancel the whole process tree is killed (unix process group / Windows /T).
    """

    def __init__(self, command: list[str], env: dict[str, str], cwd: str | None):
        self.command = command
        self.env = env
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._timed_out = False
        self._cancel_reason: str | None = None

    def start(self) -> None:
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        full_env = {**os.environ, **self.env}
        self._proc = subprocess.Popen(
            self.command,
            cwd=self._cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )

    def communicate(self, timeout: float | None) -> ProcessResult:
        assert self._proc is not None
        stdout, stderr = "", ""
        try:
            stdout, stderr = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._timed_out = True
            self._kill_tree()
            try:
                stdout, stderr = self._proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        rc = self._proc.returncode
        if rc is None:
            rc = 137 if self._timed_out else 1
        return ProcessResult(
            returncode=rc,
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=self._timed_out,
            cancelled=self._cancel_reason is not None,
            cancel_reason=self._cancel_reason,
        )

    def kill(self) -> None:
        self._kill_tree()

    def cancel(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason
        self._kill_tree()

    def _kill_tree(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
