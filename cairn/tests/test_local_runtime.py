from __future__ import annotations

import sys
from pathlib import Path

from cairn.dispatcher.runtime.local.runtime import LocalRuntime


def _rt(tmp_path):
    return LocalRuntime(workspaces_root=str(tmp_path / "ws"),
                        completed_action="remove",
                        agents_source=None)


def test_ensure_running_creates_workspace(tmp_path):
    rt = _rt(tmp_path)
    path = rt.ensure_running("proj_1")
    assert Path(path).is_dir()
    assert rt.container_name("proj_1") == "proj_1"


def test_seeds_agent_config_when_source_present(tmp_path):
    src = tmp_path / "src"
    (src / ".agents").mkdir(parents=True)
    (src / ".agents" / "a.md").write_text("hi", encoding="utf-8")
    (src / "AGENTS.md").write_text("agents", encoding="utf-8")
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="remove",
                      agents_source=str(src))
    ws = Path(rt.ensure_running("proj_1"))
    assert (ws / ".claude" / "a.md").read_text() == "hi"
    assert (ws / "AGENTS.md").read_text() == "agents"
    assert (ws / "CLAUDE.md").read_text() == "agents"


def test_write_text_file_lands_in_snapshot_root(tmp_path):
    rt = _rt(tmp_path)
    rt.ensure_running("proj_1")
    p = f"{rt.snapshot_root()}/explore-abc/graph.yaml"
    rt.write_text_file("proj_1", p, "graph: 1")
    assert Path(p).read_text() == "graph: 1"


def test_build_exec_process_runs_in_workspace(tmp_path):
    rt = _rt(tmp_path)
    ws = rt.ensure_running("proj_1")
    proc = rt.build_exec_process("proj_1", {}, [sys.executable, "-c", "import os;print(os.getcwd())"])
    proc.start()
    res = proc.communicate(timeout=30)
    assert ws in res.stdout


def test_cleanup_completed_removes_workspace(tmp_path):
    rt = _rt(tmp_path)
    ws = Path(rt.ensure_running("proj_1"))
    assert rt.needs_completed_cleanup("proj_1") is True
    assert rt.cleanup_completed("proj_1") is True
    assert not ws.exists()


def test_cleanup_keep_preserves_workspace(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="keep", agents_source=None)
    ws = Path(rt.ensure_running("proj_1"))
    assert rt.needs_completed_cleanup("proj_1") is False
    rt.cleanup_completed("proj_1")
    assert ws.exists()
