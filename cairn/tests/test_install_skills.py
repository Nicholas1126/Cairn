from __future__ import annotations

from pathlib import Path

from cairn.dispatcher.runtime.local.runtime import LocalRuntime


def test_local_install_skills_copies_into_dot_claude(tmp_path):
    src = tmp_path / "src" / "decompile"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("hi", encoding="utf-8")
    (src / "scripts" / "run.sh").write_text("echo", encoding="utf-8")
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = Path(rt.ensure_running("p1"))
    rt.install_skills("p1", [src])
    assert (ws / ".claude" / "skills" / "decompile" / "SKILL.md").read_text() == "hi"
    assert (ws / ".claude" / "skills" / "decompile" / "scripts" / "run.sh").exists()


def test_local_install_skills_empty_noop(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    rt.ensure_running("p1")
    rt.install_skills("p1", [])  # must not raise
