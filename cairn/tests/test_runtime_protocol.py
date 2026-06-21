from __future__ import annotations

from cairn.dispatcher.runtime.base import Runtime  # noqa: F401
from cairn.dispatcher.tasks import common


class _FakeRuntime:
    def __init__(self):
        self.written = []
    def snapshot_root(self) -> str:
        return "/fake/snap"
    def write_text_file(self, key, path, content):
        self.written.append((key, path, content))
    def install_skills(self, key, skill_dirs):
        pass


def test_write_graph_snapshot_reference_uses_runtime_snapshot_root():
    rt = _FakeRuntime()
    ref = common.write_graph_snapshot_reference(rt, "proj_1", "graph: yaml", phase="explore_execute")
    assert rt.written, "graph file must be written via runtime"
    written_path = rt.written[0][1]
    assert written_path.startswith("/fake/snap/")
    assert written_path in ref


def test_runtime_protocol_and_container_manager_has_snapshot_root():
    from cairn.dispatcher.runtime.containers import ContainerManager
    assert hasattr(ContainerManager, "ensure_running")
    assert hasattr(ContainerManager, "snapshot_root")
