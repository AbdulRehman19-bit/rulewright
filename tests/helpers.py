"""Test doubles shared by the suite. No network, no model downloads."""

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from rulewright.prove.sandbox import ExecResult, safe_relpath
from rulewright.store import Vector

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
    """Writes `rule`, citing every signal id in the prompt's data block; judges it `specific`."""

    def __init__(
        self, rule: str = GOOD_RULE, model: str = "ollama/test", specific: bool = True
    ) -> None:
        self.rule = rule
        self.model = model
        self.specific = specific
        self.prompts: list[tuple[str, str]] = []

    @property
    def drafts(self) -> list[tuple[str, str]]:
        return [p for p in self.prompts if "write rules" in p[0]]

    @property
    def judgements(self) -> list[tuple[str, str]]:
        return [p for p in self.prompts if "review one proposed rule" in p[0]]

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if "review one proposed rule" in system:
            return json.dumps({"specific": self.specific, "reason": "names app.config"})
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


class LocalSandbox:
    """Test double for DockerSandbox: runs in a plain temp directory. Never used by the product."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.closed = False
        self.commands: list[str] = []

    def exec(self, command: str, timeout: int) -> ExecResult:
        import subprocess

        self.commands.append(command)
        proc = subprocess.run(
            command, shell=True, cwd=self.root, capture_output=True, text=True, timeout=timeout
        )
        return ExecResult(exit_code=proc.returncode, output=proc.stdout + proc.stderr)

    def read_file(self, path: str) -> str:
        return (self.root / safe_relpath(path)).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self.root / safe_relpath(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")

    def close(self) -> None:
        self.closed = True


class ScriptedChat:
    """Plays a fixed list of replies (or callables of (system, messages)) as an agent's model."""

    model = "ollama/scripted"

    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.calls.append((system, list(messages)))
        reply = self.replies.pop(0) if self.replies else json.dumps({"action": "finish"})
        return reply(system, messages) if callable(reply) else str(reply)


def action(**fields: str) -> str:
    return json.dumps(fields)
