from cairn.server.models import SkillInfo, SkillContent, SkillCreate, SkillEnable


def test_skill_info():
    s = SkillInfo(name="decompile", description="reverse", enabled=True)
    assert s.enabled is True


def test_skill_content_and_create():
    assert SkillContent(name="a", content="x").content == "x"
    assert SkillCreate(name="a", content="x").name == "a"


def test_skill_enable():
    assert SkillEnable(enabled=False).enabled is False
