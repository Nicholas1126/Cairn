from cairn.server.models import CreateProjectRequest, ProjectMeta, ToolInfo


def test_create_request_project_root_optional_default_none():
    req = CreateProjectRequest(title="t", origin="o", goal="g")
    assert req.project_root is None
    req2 = CreateProjectRequest(title="t", origin="o", goal="g", project_root="/data/A")
    assert req2.project_root == "/data/A"


def test_project_meta_carries_project_root():
    meta = ProjectMeta(id="p1", title="t", status="active",
                       bootstrap_enabled=True, backend="docker",
                       created_at="2026-06-21T00:00:00Z", project_root="/data/A")
    assert meta.project_root == "/data/A"


def test_tool_info_shape():
    t = ToolInfo(name="graphify", launchable=True, version="graphify 0.8.41", path="/usr/bin/graphify")
    assert t.name == "graphify" and t.launchable is True
    t2 = ToolInfo(name="codegraph", launchable=False)
    assert t2.version is None and t2.path is None
