from cairn.server.models import ExecutionReport, ExecutionSummary, ExecutionDetail, Settings


def test_settings_has_toggles_with_defaults():
    s = Settings(intent_timeout=15, reason_timeout=15)
    assert s.execution_record_enabled is True
    assert s.execution_file_logging is True


def test_execution_report_minimal_valid():
    r = ExecutionReport(
        phase="explore", worker_name="w", command=["opencode", "run"],
        prompt="do it", outcome="success", started_at="t0", ended_at="t1",
        duration_ms=10, stdout="out", stderr="",
    )
    assert r.intent_id is None
    assert r.produced_intent_ids == []


def test_execution_summary_roundtrip():
    s = ExecutionSummary(
        id="exec_001", phase="reason", intent_id=None, worker_name="w", model="m",
        outcome="success", exit_code=0, started_at="t0", ended_at="t1",
        duration_ms=5, produced_fact_id=None, produced_intent_ids=["i001"], has_log=True,
    )
    assert s.has_log is True
