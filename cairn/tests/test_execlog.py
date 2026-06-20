from __future__ import annotations

from cairn.execlog import redact_text, redact_command, redact_env, truncate_head_tail


def test_redact_text_masks_api_keys_tokens_and_bearer():
    assert "sk-abcd1234efgh" not in redact_text("key=sk-abcd1234efgh done")
    assert "sk-***" in redact_text("key=sk-abcd1234efgh done")
    masked = redact_text('{"apiKey":"sk-secretsecret","baseURL":"https://x"}')
    assert "sk-secretsecret" not in masked
    assert "https://x" in masked  # non-secret preserved
    assert "Bearer ***" in redact_text("Authorization: Bearer eyJabc.def-ghi")


def test_redact_command_masks_each_arg_and_preserves_structure():
    argv = ["opencode", "run", '{"provider":{"options":{"apiKey":"sk-zzzzzzzz"}}}', "--", "hello"]
    out = redact_command(argv)
    assert len(out) == len(argv)
    assert "sk-zzzzzzzz" not in "".join(out)
    assert out[0] == "opencode" and out[-1] == "hello"


def test_redact_env_masks_secret_named_keys_only():
    env = {"OPENCODE_API_KEY": "sk-zzzzzzzz", "OPENCODE_MODEL": "glm-5.1", "PATH": "/bin"}
    out = redact_env(env)
    assert out["OPENCODE_API_KEY"] == "***"
    assert out["OPENCODE_MODEL"] == "glm-5.1"
    assert out["PATH"] == "/bin"


def test_truncate_head_tail_keeps_head_and_tail_and_marks():
    text = "A" * 100 + "B" * 100
    res = truncate_head_tail(text, limit_bytes=40)
    assert res.truncated is True
    assert res.original_bytes == 200
    assert res.text.startswith("A")
    assert res.text.rstrip().endswith("B")
    assert "truncated" in res.text


def test_truncate_head_tail_noop_when_within_limit():
    res = truncate_head_tail("short", limit_bytes=1000)
    assert res.truncated is False
    assert res.text == "short"
    assert res.original_bytes == 5


def test_truncate_head_tail_does_not_split_multibyte():
    text = "中" * 100  # 3 bytes each in utf-8
    res = truncate_head_tail(text, limit_bytes=50)
    assert "�" not in res.text
