"""Strip likely secrets from mined text before it can reach any LLM API."""

import math
import re
from collections import Counter

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),  # GitHub tokens
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # OpenAI / Anthropic style keys
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]
# key = value assignments whose key name suggests a secret; keep the key, drop the value
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
    r"[A-Za-z0-9_.-]*)(\s*[:=]\s*)(\"[^\"\n]+\"|'[^'\n]+'|[^\s,;]+)"
)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_ENTROPY_BITS = 4.0  # git SHAs (hex, about 3.8) stay readable, random keys do not


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _looks_random(token: str) -> bool:
    return (
        any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
        and _entropy(token) >= _ENTROPY_BITS
    )


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _CANDIDATE.sub(lambda m: REDACTED if _looks_random(m.group(0)) else m.group(0), text)
