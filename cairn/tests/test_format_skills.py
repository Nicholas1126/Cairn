from cairn.dispatcher.prompting import format_skills


class _M:
    def __init__(self, name, desc):
        self.name = name
        self.description = desc


def test_format_skills_empty():
    assert format_skills([]) == ""


def test_format_skills_lists_name_desc_path():
    out = format_skills([_M("decompile", "reverse binaries"), _M("graphify", "kg")])
    assert "decompile" in out and "reverse binaries" in out
    assert ".claude/skills/decompile/SKILL.md" in out
    assert "prefer" in out.lower()
