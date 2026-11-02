"""Plugin interface for agent session-log readers."""

from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel


class SessionMessage(BaseModel):
    """One human or agent text message. Text is untrusted data."""

    session_id: str
    uuid: str
    role: Literal["user", "assistant"]
    text: str
    ts: datetime


class SessionSource(Protocol):
    """Reads the session logs one agent keeps for a repo. Add a new agent by adding a reader."""

    name: str

    def read(self, repo: Path) -> list[SessionMessage]:
        """Messages for `repo`, ordered by time. Empty when the agent has no logs there."""
        ...


class ToolCall(BaseModel):
    """One shell command an agent ran. The command is untrusted data and is never executed."""

    session_id: str
    agent: str  # which reader produced it, e.g. "claude_code"
    tool: str  # "Bash" for Claude Code; the shell tool name for others
    command: str
    ts: datetime
    failed: bool = False  # the tool reported an error
    denied: bool = False  # the person refused it when asked


class ToolCallSource(Protocol):
    """Reads the commands one agent ran for a repo. Used by `guard`, not by `scan`."""

    name: str

    def read_calls(self, repo: Path) -> list[ToolCall]: ...
