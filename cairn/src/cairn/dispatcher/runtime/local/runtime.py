from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from cairn.dispatcher.runtime.local import resolve
from cairn.dispatcher.runtime.local.process import LocalManagedProcess


class LocalRuntime:
    """Run worker argv on the host. Mirrors the ContainerManager surface."""

    def __init__(self, *, workspaces_root: str, completed_action: str, agents_source: str | None):
        self._root = Path(workspaces_root).expanduser()
        self._completed_action = completed_action  # "keep" | "remove"
        self._agents_source = Path(agents_source).expanduser() if agents_source else None
        self._snapshot_root = Path(tempfile.gettempdir()) / "cairn-prompts"

    # --- identity / lifecycle ---
    def container_name(self, project_id: str) -> str:
        return project_id

    def _workspace(self, project_id: str) -> Path:
        return self._root / project_id.replace("/", "-")

    def ensure_running(self, project_id: str) -> str:
        ws = self._workspace(project_id)
        first = not ws.exists()
        ws.mkdir(parents=True, exist_ok=True)
        if first:
            self._seed_agent_config(ws)
        return str(ws)

    def _seed_agent_config(self, ws: Path) -> None:
        src = self._agents_source
        if src is None or not src.exists():
            return
        agents = src / ".agents"
        if agents.is_dir():
            shutil.copytree(agents, ws / ".claude", dirs_exist_ok=True)
            shutil.copytree(agents, ws / ".agents", dirs_exist_ok=True)
        agents_md = src / "AGENTS.md"
        if agents_md.is_file():
            content = agents_md.read_text(encoding="utf-8")
            (ws / "AGENTS.md").write_text(content, encoding="utf-8")
            (ws / "CLAUDE.md").write_text(content, encoding="utf-8")

    def create_startup_container(self) -> str:
        tmp = self._root / f"_startup-{uuid.uuid4().hex[:12]}"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp.name

    # --- exec ---
    def build_exec_process(self, name: str, env: dict[str, str], command: list[str],
                           timeout_seconds: int | None = None, kill_after_seconds: int = 5
                           ) -> LocalManagedProcess:
        # timeout_seconds is enforced by communicate() (no container `timeout` coreutil),
        # so it is intentionally not turned into an argv prefix here.
        ws = self._workspace_for_key(name)
        child_env = dict(env)
        child_env["PATH"] = resolve.augmented_path(env.get("PATH") or os.environ.get("PATH", ""))
        argv = self._rewrite_argv(command)
        return LocalManagedProcess(argv, child_env, cwd=str(ws))

    def _workspace_for_key(self, name: str) -> Path:
        candidate = self._root / name
        return candidate if candidate.exists() else self._workspace(name)

    @staticmethod
    def _rewrite_argv(command: list[str]) -> list[str]:
        if not command:
            return command
        head = command[0]
        # Only rewrite bare known agent binaries (claude/codex direct-argv drivers).
        # /bin/sh wrappers (pi/opencode) are left as-is; their inner binary resolves via PATH.
        if os.path.basename(head) == head and head in resolve.DIRECT_BINARIES:
            wtype = next((t for t, b in resolve.BINARY.items() if b == head), head)
            resolved = resolve.resolve_engine(wtype)
            if resolved is not None:
                return resolve.launch_argv(resolved, command[1:])
        return command

    def write_text_file(self, name: str, path: str, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def snapshot_root(self) -> str:
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        return str(self._snapshot_root)

    # --- cleanup ---
    def needs_completed_cleanup(self, project_id: str) -> bool:
        return self._completed_action == "remove" and self._workspace(project_id).exists()

    def needs_stopped_cleanup(self, project_id: str) -> bool:
        return False

    def cleanup_completed(self, project_id: str) -> bool:
        if self._completed_action == "remove":
            shutil.rmtree(self._workspace(project_id), ignore_errors=True)
        return True

    def cleanup_stopped(self, project_id: str) -> bool:
        return True

    def close(self) -> None:
        return None
