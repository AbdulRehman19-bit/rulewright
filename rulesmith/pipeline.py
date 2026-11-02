"""The scan pipeline: history in, evidence-backed candidate rules out. Writes nothing."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from rulesmith.cluster.embed import Embedder, embed_signals
from rulesmith.cluster.group import Cluster, cluster_signals, cold_start_message
from rulesmith.ingest.git_history import read_commits
from rulesmith.ingest.sessions.base import SessionSource
from rulesmith.mine.detectors import (
    dedupe_signals,
    detect_corrections,
    detect_refixes,
    detect_reverts,
)
from rulesmith.mine.score import score_signals
from rulesmith.mine.signals import Signal
from rulesmith.pr import commit_url
from rulesmith.propose.llm import CostEstimate, LLMClient, estimate_cost
from rulesmith.propose.writer import Proposals, build_prompt, propose_rules
from rulesmith.rules.source import RulesFile
from rulesmith.rules.stale import StaleFinding, stale_findings
from rulesmith.store import open_store

MAX_RULES = 10  # spec: print 5-10 proposed rules


class ScanResult(BaseModel):
    commits_read: int
    agent_commits: int
    signals: list[Signal]
    clusters: list[Cluster]
    proposals: Proposals | None  # None when no rules were requested from an LLM
    stale: list[StaleFinding]
    cold_start: str | None
    notes: list[str]


def run_scan(
    repo: Path,
    commits: int,
    embedder: Embedder,
    client: LLMClient | None,
    existing: RulesFile,
    now: datetime,
    confirm: Callable[[CostEstimate], bool] = lambda _estimate: True,
    sessions: list[SessionSource] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ScanResult:
    history = read_commits(repo, commits)
    agent_commits = sum(1 for c in history if c.is_agent)
    notes: list[str] = []

    signals = detect_reverts(history) + detect_refixes(history)
    for source in sessions or []:
        signals += detect_corrections(source.read(repo))
    signals = score_signals(dedupe_signals(signals), now)

    clusters: list[Cluster] = []
    if signals:
        conn = open_store(repo)
        try:
            clusters = cluster_signals(signals, embed_signals(signals, embedder, conn))
        finally:
            conn.close()
    known = {r.id for r in existing.rules}
    fresh = [c for c in clusters if "r-" + c.id.removeprefix("c-") not in known][:MAX_RULES]

    cold_start = cold_start_message(len(clusters), commits)
    if agent_commits == 0:
        notes.append(
            "No agent-authored commits found (looked for Co-Authored-By trailers and bot authors)."
        )

    proposals: Proposals | None = None
    if cold_start is None and fresh and client is None:
        notes.append(
            "No LLM available: start Ollama or set ANTHROPIC_API_KEY / OPENAI_API_KEY to "
            "turn these clusters into rules."
        )
    elif cold_start is None and fresh and client is not None:
        by_id = {s.id: s for s in signals}
        prompts = [
            build_prompt(c, [by_id[i] for i in c.signal_ids if i in by_id]) for c in fresh
        ]
        if confirm(estimate_cost(client.model, prompts)):
            proposals = propose_rules(fresh, signals, repo, client, progress)
            for rule in proposals.rules:
                for e in rule.evidence:
                    e.url = commit_url(repo, e.commit_sha) if e.commit_sha else ""
        else:
            notes.append("Cancelled before any LLM call; no rules were proposed.")

    return ScanResult(
        commits_read=len(history),
        agent_commits=agent_commits,
        signals=signals,
        clusters=clusters,
        proposals=proposals,
        stale=stale_findings(existing.rules, repo, signals, now),
        cold_start=cold_start,
        notes=notes,
    )
