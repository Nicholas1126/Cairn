from __future__ import annotations

import os

import docker
import pytest

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.local.runtime import LocalRuntime


# ---- docker: read-only bind mount at container creation ----
class _FakeContainers:
    def __init__(self):
        self.run_calls = []
    def run(self, image, command, **kwargs):
        self.run_calls.append({"image": image, "command": command, **kwargs})
        return object()


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()
    def close(self):
        pass


def _cm(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(docker, "from_env", lambda: fake)
    cfg = ContainerConfig(image="img:test", network_mode="none", completed_action="stop")
    cm = ContainerManager(cfg)
    monkeypatch.setattr(cm, "inspect_state", lambda name: None)  # force "create" path
    return cm, fake


def test_ensure_running_adds_readonly_volume_when_project_root(monkeypatch, tmp_path):
    a = tmp_path / "A"; a.mkdir()
    cm, fake = _cm(monkeypatch)
    cm.ensure_running("p1", str(a))
    kwargs = fake.containers.run_calls[0]
    assert kwargs["volumes"] == {str(a): {"bind": "/home/kali/workspace/project", "mode": "ro"}}


def test_ensure_running_no_volume_when_no_project_root(monkeypatch):
    cm, fake = _cm(monkeypatch)
    cm.ensure_running("p1")
    assert "volumes" not in fake.containers.run_calls[0] or fake.containers.run_calls[0]["volumes"] is None


# ---- local: symlink project -> A ----
def test_local_ensure_running_symlinks_project_root(tmp_path):
    a = tmp_path / "A"; a.mkdir()
    (a / "src-repo").mkdir()
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = rt.ensure_running("p1", str(a))
    link = os.path.join(ws, "project")
    assert os.path.islink(link)
    assert os.path.realpath(link) == os.path.realpath(str(a))
    assert os.path.isdir(os.path.join(link, "src-repo"))


def test_local_ensure_running_no_symlink_without_project_root(tmp_path):
    rt = LocalRuntime(workspaces_root=str(tmp_path / "ws"), completed_action="stop", agents_source=None)
    ws = rt.ensure_running("p1")
    assert not os.path.exists(os.path.join(ws, "project"))
