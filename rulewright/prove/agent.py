"""AgentRunner interface and the built-in agent used to replay tasks.

The built-in agent is a small loop: the model answers each turn with one JSON action (read a
file, write a file, run a command, finish), the action runs in the sandbox, and the result goes
back as the next turn. It is deliberately simple and model-agnostic (no native tool calling).
Other agents (for example Claude Code in headless mode) plug in behind `AgentRunner`.
"""

import json
import re
from typing import Protocol

from pydantic import BaseModel

from rulesmith.propose.llm import CHARS_PER_TOKEN, ChatClient
from rulesmith.prove.sandbox import Sandbox, SandboxError

MAX_STEPS = 12
COMMAND_TIMEOUT = 60  # seconds per command inside the sandbox
MAX_REPLY_TOKENS = 1500  # a file write can be long
OBSERVATION_CHARS = 4000

SYSTEM_PROMPT = """You are a coding agent working in the repository at /work.
Complete the task by editing files. Every turn, reply with exactly one JSON object and nothing
else, choosing one action:
{"action": "read_file", "path": "relative/path"}
{"action": "write_file", "path": "relative/path", "content": "the complete new file content"}
{"action": "run", "command": "shell command"}
{"action": "finish"}
Paths are relative to /work. There is no network. Make the smallest change that completes the
task, then reply with {"action": "finish"}."""


class RunResult(BaseModel):
    steps: int
    finished: bool
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


class AgentRunner(Protocol):
    name: str
    model: str

    def run(self, task: str, rules: list[str], sandbox: Sandbox) -> RunResult:
        """Attempt `task` in `sandbox`, following `rules` (instruction-file text) if any."""
        ...


def system_prompt(rules: list[str]) -> str:
    if not rules:
        return SYSTEM_PROMPT
    listed = "\n".join(f"- {r}" for r in rules)
    return f"{SYSTEM_PROMPT}\n\nProject rules you must follow (from AGENTS.md):\n{listed}"


def approx_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


_JSON = re.compile(r"\{.*\}", re.S)


def parse_action(reply: str) -> dict[str, str] | None:
    m = _JSON.search(reply)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str)}


class BuiltinAgent:
    name = "builtin"

    def __init__(self, client: ChatClient, max_steps: int = MAX_STEPS) -> None:
        self.client = client
        self.model = client.model
        self.max_steps = max_steps

    def run(self, task: str, rules: list[str], sandbox: Sandbox) -> RunResult:
        system = system_prompt(rules)
        messages = [{"role": "user", "content": f"Task:\n{task}"}]
        tokens_in = tokens_out = 0
        for step in range(1, self.max_steps + 1):
            tokens_in += approx_tokens(system) + sum(approx_tokens(m["content"]) for m in messages)
            try:
                reply = self.client.chat(system, messages, MAX_REPLY_TOKENS)
            except Exception as e:  # network, rate limit, auth: end this run, keep the others
                return RunResult(
                    steps=step, finished=False, error=f"model call failed: {e}"[:200],
                    tokens_in=tokens_in, tokens_out=tokens_out,
                )  # fmt: skip
            tokens_out += approx_tokens(reply)
            messages.append({"role": "assistant", "content": reply})
            action = parse_action(reply)
            if action is None:
                observation = 'Reply with one JSON object such as {"action": "finish"}.'
            elif action["action"] == "finish":
                return RunResult(
                    steps=step, finished=True, tokens_in=tokens_in, tokens_out=tokens_out
                )
            else:
                observation = self._act(action, sandbox)
            messages.append({"role": "user", "content": observation[:OBSERVATION_CHARS]})
        return RunResult(
            steps=self.max_steps, finished=False, error="step limit reached",
            tokens_in=tokens_in, tokens_out=tokens_out,
        )  # fmt: skip

    @staticmethod
    def _act(action: dict[str, str], sandbox: Sandbox) -> str:
        kind = action["action"]
        try:
            if kind == "read_file" and "path" in action:
                return sandbox.read_file(action["path"])
            if kind == "write_file" and "path" in action and "content" in action:
                sandbox.write_file(action["path"], action["content"])
                return f"wrote {action['path']}"
            if kind == "run" and "command" in action:
                result = sandbox.exec(action["command"], COMMAND_TIMEOUT)
                note = " (timed out)" if result.timed_out else ""
                return f"exit code {result.exit_code}{note}\n{result.output}"
        except SandboxError as e:
            return f"error: {e}"
        return f"error: unknown or incomplete action {kind!r}"
