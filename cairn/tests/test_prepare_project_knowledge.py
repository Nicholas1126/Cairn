from cairn.dispatcher.tasks.common import prepare_project_knowledge


def test_none_root_returns_empty():
    assert prepare_project_knowledge(None) == ""


def test_probes_existing_subdirs(tmp_path):
    a = tmp_path / "A"
    (a / "src-repo").mkdir(parents=True)
    (a / "graphify-out").mkdir()
    # scan-out / docs-out / codegraph-out absent
    out = prepare_project_knowledge(str(a))
    assert "./project/src-repo" in out
    assert "./project/graphify-out" in out
    assert "scan-out" not in out
    assert "codegraph-out" not in out


def test_empty_when_no_known_subdirs(tmp_path):
    a = tmp_path / "A"
    (a / "unrelated").mkdir(parents=True)
    assert prepare_project_knowledge(str(a)) == ""
