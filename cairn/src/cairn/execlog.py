from __future__ import annotations

import re
from dataclasses import dataclass

# Mask "key: value" / "key=value" / JSON "key":"value" where key looks secret.
# The value capture excludes values that start with "Bearer" (handled separately).
_KV_SECRET = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password|authorization)["\']?\s*[:=]\s*["\']?)'
    r'(?!Bearer\b)([^"\'\s,}\]]+)'
)
_SK_TOKEN = re.compile(r'sk-[A-Za-z0-9_\-]{6,}')
_BEARER = re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]+')

# env var names whose VALUE is always a secret regardless of content
_SECRET_ENV = re.compile(r'(?i)(api[_-]?key|apikey|token|secret|password|auth)')


def redact_text(text: str) -> str:
    if not text:
        return text
    out = _BEARER.sub("Bearer ***", text)
    out = _KV_SECRET.sub(lambda m: m.group(1) + "***", out)
    out = _SK_TOKEN.sub("sk-***", out)
    return out


def redact_command(argv: list[str]) -> list[str]:
    return [redact_text(arg) for arg in argv]


def redact_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if _SECRET_ENV.search(k) else v) for k, v in env.items()}


@dataclass(slots=True)
class TruncResult:
    text: str
    original_bytes: int
    truncated: bool


def truncate_head_tail(text: str, limit_bytes: int) -> TruncResult:
    raw = text.encode("utf-8")
    n = len(raw)
    if n <= limit_bytes:
        return TruncResult(text=text, original_bytes=n, truncated=False)
    half = max(1, limit_bytes // 2)
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[n - half:].decode("utf-8", errors="ignore")
    dropped = n - (2 * half)
    marker = f"\n…… [truncated {dropped} bytes of {n} total] ……\n"
    return TruncResult(text=head + marker + tail, original_bytes=n, truncated=True)
