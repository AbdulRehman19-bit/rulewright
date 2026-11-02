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
