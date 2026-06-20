from __future__ import annotations

import io
import zipfile

import pytest

from cairn import skills_store


def _point(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    return tmp_path / "skills"


def _skill_md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\nbody\n"


def test_root_follows_cairn_home(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    assert skills_store.skills_root() == tmp_path / "skills"


def test_seed_if_empty_copies_repo_skills(monkeypatch, tmp_path):
    root = _point(monkeypatch, tmp_path)
    repo = tmp_path / "repo_skills"
    (repo / "decompile").mkdir(parents=True)
    (repo / "decompile" / "SKILL.md").write_text(_skill_md("decompile", "reverse"), encoding="utf-8")
    skills_store.seed_if_empty(repo)
    assert (root / "decompile" / "SKILL.md").exists()
    (repo / "decompile" / "SKILL.md").write_text("changed", encoding="utf-8")
    skills_store.seed_if_empty(repo)
    assert "changed" not in (root / "decompile" / "SKILL.md").read_text(encoding="utf-8")


def test_create_list_read_enabled_delete(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    skills_store.create_skill("graphify", _skill_md("graphify", "knowledge graph"))
    metas = skills_store.list_skills()
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "graphify" and m.description == "knowledge graph" and m.enabled is True
    assert "knowledge graph" in skills_store.read_skill_md("graphify")
    skills_store.set_enabled("graphify", False)
    assert skills_store.list_skills()[0].enabled is False
    assert skills_store.enabled_skill_dirs() == []
    skills_store.set_enabled("graphify", True)
    assert [p.name for p in skills_store.enabled_skill_dirs()] == ["graphify"]
    skills_store.delete_skill("graphify")
    assert skills_store.list_skills() == []


def test_corrupt_registry_tolerated(monkeypatch, tmp_path):
    root = _point(monkeypatch, tmp_path)
    skills_store.create_skill("a", _skill_md("a", "x"))
    (root / ".registry.json").write_text("{ broken", encoding="utf-8")
    assert skills_store.list_skills()[0].enabled is True


def test_import_zip(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mytool/SKILL.md", _skill_md("mytool", "does things"))
        zf.writestr("mytool/scripts/run.sh", "echo hi\n")
    name = skills_store.import_zip(buf.getvalue())
    assert name == "mytool"
    assert "does things" in skills_store.read_skill_md("mytool")
    assert (skills_store.skills_root() / "mytool" / "scripts" / "run.sh").exists()


def test_rejects_bad_names(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        skills_store.create_skill("../evil", "x")
