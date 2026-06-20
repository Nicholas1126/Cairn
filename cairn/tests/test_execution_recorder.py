from __future__ import annotations

from cairn.dispatcher.tasks.common import ExecutionRecorder
from cairn.dispatcher.runtime.process import ProcessResult


class _FakeSettings:
    def __init__(self, rec=True, files=True):
        self.execution_record_enabled = rec
        self.execution_file_logging = files


class _FakeClient:
    def __init__(self, settings):
        self._settings = settings
        self.calls = []

    def get_settings(self):
        return self._settings

    def report_execution(self, project_id, payload):
        self.calls.append((project_id, payload))
        class R: ok = True; status_code = 201
        return R()


def _result(stdout="out", rc=0):
    return ProcessResult(returncode=rc, stdout=stdout, stderr="", timed_out=False, cancelled=False)


def test_recorder_reports_decisive_process_with_outcome():
    client = _FakeClient(_FakeSettings())
    rec = ExecutionRecorder(client, project_id="p1", intent_id="i001",
                            worker_name="w", model="m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result("hello"))
    rec.set_produced_fact("f001")
    rec.finish("success")
    assert len(client.calls) == 1
    _, payload = client.calls[0]
    assert payload["phase"] == "explore"
    assert payload["outcome"] == "success"
    assert payload["produced_fact_id"] == "f001"
    assert payload["stdout"] == "hello"


def test_recorder_noop_when_record_disabled():
    client = _FakeClient(_FakeSettings(rec=False))
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result())
    rec.finish("success")
    assert client.calls == []


def test_recorder_caps_stdout_to_64k_when_file_logging_off():
    client = _FakeClient(_FakeSettings(files=False))
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.record(phase="explore", command=["c"], prompt="P", result=_result("x" * 500_000))
    rec.finish("success")
    payload = client.calls[0][1]
    assert len(payload["stdout"].encode()) <= 64 * 1024 + 200


def test_recorder_skips_when_no_process_recorded():
    client = _FakeClient(_FakeSettings())
    rec = ExecutionRecorder(client, "p1", "i001", "w", "m")
    rec.finish("failed")  # healthcheck failed before any process
    assert client.calls == []
