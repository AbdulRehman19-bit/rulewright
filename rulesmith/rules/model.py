"""Rule and Evidence: the proposal and the links back to the signals that justify it."""

from typing import Literal

from pydantic import BaseModel

RuleStatus = Literal["proposed", "accepted", "rejected", "stale"]


class Evidence(BaseModel):
    rule_id: str
    signal_id: str
    commit_sha: str
    url: str = ""


class Rule(BaseModel):
    id: str
    text: str
    scope: list[str]
    status: RuleStatus = "proposed"
    risk_flags: list[str] = []
    created_from_cluster: str = ""
    evidence: list[Evidence] = []
