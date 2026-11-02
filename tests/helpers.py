"""Test doubles shared by the suite. No network, no model downloads."""

import hashlib
import json
import re

import numpy as np

from rulesmith.store import Vector

GOOD_RULE = "Import settings from app.config, never from app.settings (removed in v2)."


class FakeEmbedder:
    """Deterministic bag-of-words hashing, so tests never download a model."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[Vector]:
        self.calls.append(texts)
        out = []
        for t in texts:
            v = np.zeros(512, dtype=np.float32)
            for w in re.findall(r"[a-z]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 512] += 1.0
            out.append(v / (np.linalg.norm(v) or 1.0))
        return out


class CitingLLM:
    """Replies with `rule`, citing every signal id that appears in the prompt's data block."""

    def __init__(self, rule: str = GOOD_RULE, model: str = "ollama/test") -> None:
        self.rule = rule
        self.model = model
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        ids = re.findall(r'"id": "([^"]+)"', user)
        return json.dumps({"rule": self.rule, "scope": ["app/**"], "cited_signals": ids})


class RawLLM:
    """Replies with a fixed string, whatever it is asked."""

    def __init__(self, reply: str, model: str = "ollama/test") -> None:
        self.reply = reply
        self.model = model
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.reply
