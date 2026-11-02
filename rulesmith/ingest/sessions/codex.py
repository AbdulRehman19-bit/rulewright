"""Codex rollout reader (commands only).

Rollouts live in ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. Each line is
`{"timestamp", "type", "payload"}`. A `session_meta` record carries the working directory
(`payload.cwd`); `response_item` records with `payload.type == "function_call"` carry the command
as a JSON string in `payload.arguments`, and `function_call_output` answers it by `call_id`.
The command is a string, or an argv list such as ["bash", "-lc", "git status"].
Checked against a real rollout (Codex 0.137); unknown or malformed lines are skipped.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rulesmith.ingest.sessions.base import ToolCall

SHELL_TOOLS = {"shell", "shell_command", "exec_command", "local_shell"}
DENIED_MARKERS = ("rejected by user", "denied by user", "user declined", "aborted by user")
_EXIT_CODE = re.compile(r"exit code:?\s*(-?\d+)", re.I)


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def command_text(command: Any) -> str | None:
    if isinstance(command, str):
        return command
    if isinstance(command, list) and command and all(isinstance(p, str) for p in command):
        parts: list[str] = command
        if len(parts) >= 3 and parts[1] in ("-c", "-lc", "/c", "-Command"):
            return parts[-1]  # the script the shell was asked to run
        return " ".join(parts)
    return None


def _outcome(output: Any) -> tuple[bool, bool]:
    """(failed, denied) from a function_call_output payload."""
    text = output if isinstance(output, str) else json.dumps(output)
    low = text.lower()
    if any(m in low for m in DENIED_MARKERS):
        return False, True
    if low.startswith("execution error"):
        return True, False
    m = _EXIT_CODE.search(text) or re.search(r'"exit_code":\s*(-?\d+)', text)
    return (m is not None and int(m.group(1)) != 0), False


class CodexSource:
    name = "codex"

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = sessions_dir or Path.home() / ".codex" / "sessions"

    def read_calls(self, repo: Path) -> list[ToolCall]:
        if not self.sessions_dir.is_dir():
            return []
        want = _norm(str(repo.resolve()))
        calls: list[ToolCall] = []
        for path in sorted(self.sessions_dir.glob("**/rollout-*.jsonl")):
            calls.extend(self._calls_in(path, want))
        calls.sort(key=lambda c: c.ts)
        return calls

    @staticmethod
    def _calls_in(path: Path, want_cwd: str) -> list[ToolCall]:
        session = path.stem
        pending: dict[str, ToolCall] = {}
        inside = False
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    payload = rec["payload"]
                    kind = rec["type"]
                except (ValueError, KeyError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if kind == "session_meta":
                    inside = _norm(str(payload.get("cwd", ""))) == want_cwd
                    session = str(payload.get("id") or session)
                    continue
                if not inside or kind != "response_item":
                    continue
                call = CodexSource._parse(payload, rec.get("timestamp"), session)
                if call is not None:
                    pending[str(payload["call_id"])] = call
                elif payload.get("type") == "function_call_output":
                    done = pending.get(str(payload.get("call_id")))
                    if done is not None:
                        done.failed, done.denied = _outcome(payload.get("output"))
        return list(pending.values())

    @staticmethod
    def _parse(payload: dict[str, Any], stamp: Any, session: str) -> ToolCall | None:
        if payload.get("type") != "function_call" or payload.get("name") not in SHELL_TOOLS:
            return None
        try:
            args = json.loads(payload["arguments"])
            command = command_text(args.get("command") or args.get("cmd"))
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            call_id = payload["call_id"]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        if command is None or not call_id:
            return None
        return ToolCall(
            session_id=session, agent="codex", tool=str(payload["name"]), command=command, ts=when
        )
