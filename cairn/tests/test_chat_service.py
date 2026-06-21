from __future__ import annotations

from cairn.server import chat
from cairn.dispatcher.runtime.process import ProcessResult


class _FakeDriver:
    def prepare_session(self): return None
    def build_execute(self, worker, prompt, session):
        class R:
            pass
        R.argv = ["opencode", "run", "--", prompt] + (["-s", session] if session else [])
        R.session = session
        return R
    def extract_session(self, session, stdout, stderr): return session or "ses_new"
    def extract_response_text(self, stdout, stderr): return "the reply"


class _FakeProc:
    def __init__(self, argv): self.argv = argv
    def start(self): pass
    def communicate(self, timeout):
        return ProcessResult(returncode=0, stdout="raw out sk-SECRET123 done", stderr="")


class _FakeRuntime:
    def ensure_running(self, key, project_root=None): return "/tmp/ws"
    def build_exec_process(self, key, env, argv): return _FakeProc(argv)


class _Worker:
    def __init__(self):
        self.name = "opencode_x"; self.type = "opencode"; self.env = {"OPENCODE_API_KEY": "sk-SECRET123"}


def test_run_turn_new_session(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": True, "path": "/x", "version": "1", "source": "path"})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "ping", None, timeout=5)
    assert res.reply == "the reply"
    assert res.session == "ses_new"
    assert res.outcome == "success"
    assert "sk-SECRET123" not in "".join(res.command)
    assert "sk-SECRET123" not in res.stdout


def test_run_turn_resumes_session(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": True, "path": "/x", "version": "1", "source": "path"})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "again", "ses_keep", timeout=5)
    assert res.session == "ses_keep"
    assert "-s" in res.command and "ses_keep" in res.command


def test_run_turn_guard_when_not_launchable(monkeypatch):
    monkeypatch.setattr(chat.resolve, "probe_engine", lambda t: {"launchable": False, "path": None, "version": None, "source": None})
    res = chat._run_turn(_FakeDriver(), _FakeRuntime(), _Worker(), "ping", None, timeout=5)
    assert res.outcome == "failed"
    assert "opencode" in res.reply.lower() or "not" in res.reply.lower()


def test_list_workers_excludes_credentials(monkeypatch, tmp_path):
    cfg = tmp_path / "dispatch.yaml"
    cfg.write_text("""\
server: http://localhost:8000
runtime: {max_workers: 1, max_running_projects: 1, max_project_workers: 1, interval: 5, healthcheck_timeout: 30, prompt_group: default}
tasks:
  bootstrap: {timeout: 10, conclude_timeout: 10}
  reason: {timeout: 10, max_intents: 3}
  explore: {timeout: 10, conclude_timeout: 10}
container: {image: img, network_mode: bridge, completed_action: remove}
workers:
  - {name: oc, type: opencode, task_types: [explore], max_running: 1, priority: 0, env: {OPENCODE_MODEL: deepseek, OPENCODE_BASE_URL: http://x, OPENCODE_API_KEY: sk-SECRET}}
""", encoding="utf-8")
    monkeypatch.setenv("CAIRN_DISPATCH_CONFIG", str(cfg))
    workers = chat.list_workers()
    assert len(workers) == 1
    assert workers[0].name == "oc" and workers[0].type == "opencode" and workers[0].model == "deepseek"
    import json
    assert "sk-SECRET" not in json.dumps([w.model_dump() for w in workers])
