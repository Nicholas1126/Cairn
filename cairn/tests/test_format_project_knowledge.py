from cairn.dispatcher.prompting import format_project_knowledge


def test_empty_when_no_root():
    assert format_project_knowledge(None, []) == ""
    assert format_project_knowledge("/data/A", []) == ""


def test_lists_only_present_subdirs_with_usage():
    out = format_project_knowledge("/data/A", ["src-repo", "codegraph-out", "graphify-out"])
    assert "./project/src-repo" in out
    assert "codegraph" in out and "./project/codegraph-out" in out
    assert "graphify query" in out and "./project/graphify-out" in out
    # absent ones must not appear
    assert "scan-out" not in out
    assert "docs-out" not in out
    # reuse directive present
    assert "do NOT redo" in out or "Reuse" in out


def test_unknown_subdir_ignored():
    out = format_project_knowledge("/data/A", ["weird", "docs-out"])
    assert "./project/docs-out" in out
    assert "weird" not in out
