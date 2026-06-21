from __future__ import annotations

from pathlib import Path

from cairn.dispatcher.tasks import common
from cairn import skills_store


class _Runtime:
    def __init__(self):
        self.installed = None

    def install_skills(self, key, dirs):
        self.installed = (key, dirs)


def test_prepare_skills_installs_and_describes(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    skills_store.create_skill("decompile", "---\nname: decompile\ndescription: reverse\n---\n")
    rt = _Runtime()
    text = common.prepare_skills(rt, "p1")
    assert rt.installed[0] == "p1"
    assert [Path(d).name for d in rt.installed[1]] == ["decompile"]
    assert "decompile" in text and "reverse" in text


def test_prepare_skills_none(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    rt = _Runtime()
    assert common.prepare_skills(rt, "p1") == ""
    assert rt.installed == ("p1", [])
