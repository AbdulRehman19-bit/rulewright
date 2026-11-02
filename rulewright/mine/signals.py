from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SignalKind = Literal["revert", "refix", "correction", "review"]


class Signal(BaseModel):
    """One observed agent mistake. Text fields are untrusted data."""

    id: str
    kind: SignalKind
    commit_sha: str
    files: list[str]
    diff_excerpt: str = ""
    text: str
    author_is_agent: bool
    score: float
    ts: datetime
