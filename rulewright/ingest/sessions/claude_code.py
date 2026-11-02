"""Claude Code transcript reader.

Transcripts live in ~/.claude/projects/<project-slug>/<session-id>.jsonl, one JSON object per
line. The slug is the project path with every non-alphanumeric character replaced by "-".
Records of type "user" and "assistant" carry `message.content` as a string or a list of blocks;
only "text" blocks are kept (tool calls and tool results are skipped). Unknown or malformed
lines are skipped so a format change degrades to fewer messages, not a crash.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rulewright.ingest.sessions.base import SessionMessage, ToolCall


def project_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))


DENIED_MARKERS = ("doesn't want to proceed", "was rejected", "user rejected")


def _result_text(content: Any) -> str:
    return content if isinstance(content, str) else _text_of(content)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


class ClaudeCodeSource:
    name = "claude_code"

    def __init__(self, projects_dir: Path | None = None) -> None:
        self.projects_dir = projects_dir or Path.home() / ".claude" / "projects"

    def find_dir(self, repo: Path) -> Path | None:
        slug = project_slug(repo).lower()  # drive letter case differs between tools on Windows
        if not self.projects_dir.is_dir():
            return None
        for d in self.projects_dir.iterdir():
            if d.is_dir() and d.name.lower() == slug:
                return d
        return None

    def read(self, repo: Path) -> list[SessionMessage]:
        d = self.find_dir(repo)
        if d is None:
            return []
        messages: list[SessionMessage] = []
        for path in sorted(d.glob("*.jsonl")):
            messages.extend(self._read_file(path))
        messages.sort(key=lambda m: m.ts)
        return messages

    def _read_file(self, path: Path) -> list[SessionMessage]:
        out: list[SessionMessage] = []
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                msg = self._parse(line, path.stem)
                if msg is not None:
                    out.append(msg)
        return out

    @staticmethod
    def _parse(line: str, default_session: str) -> SessionMessage | None:
        try:
            rec = json.loads(line)
            if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
                return None
            if rec.get("isMeta"):
                return None
            text = _text_of(rec["message"].get("content")).strip()
            if not text:
                return None
            return SessionMessage(
                session_id=rec.get("sessionId", default_session),
                uuid=rec["uuid"],
                role=rec["type"],
                text=text,
                ts=datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def read_calls(self, repo: Path) -> list[ToolCall]:
        """Bash commands with whether each failed or was refused (matched by tool_use_id)."""
        d = self.find_dir(repo)
        if d is None:
            return []
        calls: list[ToolCall] = []
        for path in sorted(d.glob("*.jsonl")):
            calls.extend(self._calls_in(path))
        calls.sort(key=lambda c: c.ts)
        return calls

    @staticmethod
    def _calls_in(path: Path) -> list[ToolCall]:
        pending: dict[str, ToolCall] = {}
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    content = rec["message"]["content"]
                    if rec.get("isSidechain") or not isinstance(content, list):
                        continue
                    when = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
                    session = rec.get("sessionId", path.stem)
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind == "tool_use" and block.get("name") == "Bash":
                        command = (block.get("input") or {}).get("command")
                        if isinstance(command, str) and isinstance(block.get("id"), str):
                            pending[block["id"]] = ToolCall(
                                session_id=session, agent="claude_code", tool="Bash",
                                command=command, ts=when,
                            )  # fmt: skip
                    elif kind == "tool_result" and block.get("tool_use_id") in pending:
                        call = pending[block["tool_use_id"]]
                        if block.get("is_error"):
                            text = _result_text(block.get("content")).lower()
                            call.denied = any(m in text for m in DENIED_MARKERS)
                            call.failed = not call.denied
        return list(pending.values())
