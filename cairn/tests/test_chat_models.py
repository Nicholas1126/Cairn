from cairn.server.models import ChatWorker, ChatTurnRequest, ChatTurnResult


def test_chat_worker():
    w = ChatWorker(name="opencode_x", type="opencode", model="deepseek-v4-pro")
    assert w.model == "deepseek-v4-pro"


def test_chat_turn_request_defaults_session_none():
    r = ChatTurnRequest(worker="w", message="hi")
    assert r.session is None


def test_chat_turn_result_minimal():
    r = ChatTurnResult(reply="pong", command=["opencode", "run"], prompt="ping",
                       stdout="...", outcome="success")
    assert r.session is None and r.exit_code is None and r.duration_ms == 0
