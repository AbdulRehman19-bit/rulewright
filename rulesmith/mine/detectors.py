"""Detectors turn commit events into Signals. Each has a known right answer in the fixtures."""

import re
from datetime import timedelta

from rulesmith.ingest.git_history import Commit, FileChange
from rulesmith.ingest.sessions.base import SessionMessage
from rulesmith.mine.signals import Signal

REVERT_WINDOW = timedelta(days=7)
REFIX_WINDOW = timedelta(hours=48)
EXCERPT_CHARS = 800
# A later commit "deletes most of" an agent commit when it removes at least this share of its adds.
MOSTLY_DELETED = 0.5
_REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)


def detect_reverts(commits: list[Commit]) -> list[Signal]:
    """Commits that revert, or delete most of, an agent commit within 7 days."""
    agent = [c for c in commits if c.is_agent]
    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()
    for later in commits:
        for target in agent:
            if target.sha == later.sha or later.ts <= target.ts:
                continue
            if later.ts - target.ts > REVERT_WINDOW:
                continue
            explicit = _explicitly_reverts(later, target)
            if not explicit and not _deletes_most_of(later, target):
                continue
            key = (later.sha, target.sha)
            if key in seen:
                continue
            seen.add(key)
            signals.append(
                Signal(
                    id=f"revert-{target.sha[:7]}-{later.sha[:7]}",
                    kind="revert",
                    commit_sha=target.sha,
                    files=[f.path for f in target.files],
                    diff_excerpt=target.patch[:EXCERPT_CHARS],
                    text=f"{target.subject} -> {later.subject}",
                    author_is_agent=True,
                    score=1.0 if explicit else 0.6,
                    ts=later.ts,
                )
            )
    return signals


def detect_refixes(commits: list[Commit]) -> list[Signal]:
    """Human commits touching lines an agent commit changed, within 48 hours."""
    agent = [c for c in commits if c.is_agent]
    signals: list[Signal] = []
    for later in commits:
        if later.is_agent:
            continue
        for target in agent:
            if later.ts <= target.ts or later.ts - target.ts > REFIX_WINDOW:
                continue
            if _explicitly_reverts(later, target):
                continue  # already a revert signal
            shared = _overlapping_files(later, target)
            if not shared:
                continue
            signals.append(
                Signal(
                    id=f"refix-{target.sha[:7]}-{later.sha[:7]}",
                    kind="refix",
                    commit_sha=target.sha,
                    files=shared,
                    diff_excerpt=target.patch[:EXCERPT_CHARS],
                    text=f"{target.subject} -> {later.subject}",
                    author_is_agent=True,
                    score=0.8,
                    ts=later.ts,
                )
            )
    return signals


def _pair_key(signal: Signal) -> str:
    """Revert and re-fix ids end in `<agent7>-<later7>`; anything else is unique by id."""
    return signal.id.split("-", 1)[1] if signal.kind in ("revert", "refix") else signal.id


def dedupe_signals(signals: list[Signal]) -> list[Signal]:
    """One signal per (agent commit, later commit) pair, keeping the most confident.

    A human fixing the line an agent just wrote also "deletes most of" that commit, so the
    revert and re-fix detectors can both fire on one event.
    """
    best: dict[str, Signal] = {}
    for s in signals:
        key = _pair_key(s)
        if key not in best or s.score > best[key].score:
            best[key] = s
    return [s for s in signals if best[_pair_key(s)] is s]


def _explicitly_reverts(later: Commit, target: Commit) -> bool:
    return any(target.sha.startswith(m) for m in _REVERTS_SHA.findall(later.body))


def _deletes_most_of(later: Commit, target: Commit) -> bool:
    if target.added == 0:
        return False
    touched = {f.path for f in target.files}
    deleted = sum(f.deleted for f in later.files if f.path in touched)
    return deleted >= MOSTLY_DELETED * target.added


def _overlapping_files(later: Commit, target: Commit) -> list[str]:
    """Files where `later` edits lines that `target` wrote.

    Line numbers are compared directly, so commits between the two that shift lines can cause
    misses or false matches. Good enough for fix-ups made soon after the agent commit.
    """
    theirs = {f.path: f for f in target.files}
    return [f.path for f in later.files if f.path in theirs and _ranges_overlap(f, theirs[f.path])]


def _ranges_overlap(later: FileChange, target: FileChange) -> bool:
    return any(
        a_lo <= b_hi and b_lo <= a_hi
        for a_lo, a_hi in later.old_ranges
        for b_lo, b_hi in target.new_ranges
    )


# Opening words of a message that rejects or corrects the agent ("no, use X", "don't touch Y").
_CORRECTION = re.compile(
    r"^\s*(no\b[,.!:]?|nope\b|don'?t\b|do not\b|stop\b|wrong\b|that'?s (not|wrong)\b"
    r"|not what i\b|undo\b|revert\b|why did you\b|instead\b"
    r"|you (should|shouldn'?t|didn'?t|forgot)\b)",
    re.I,
)
MAX_CORRECTION_CHARS = 400  # long messages are new tasks, not corrections
# Tags the harness injects into user turns; these are not the human talking.
_HARNESS_TEXT = re.compile(r"^\s*<[a-z-]+>|^\s*\[Request interrupted", re.I)


def detect_corrections(messages: list[SessionMessage]) -> list[Signal]:
    """User messages that reject or correct the agent's previous turn."""
    signals: list[Signal] = []
    seen_assistant: set[str] = set()
    for m in sorted(messages, key=lambda m: m.ts):
        if m.role == "assistant":
            seen_assistant.add(m.session_id)
            continue
        if m.session_id not in seen_assistant:
            continue  # the opening message of a session is the task itself
        if len(m.text) > MAX_CORRECTION_CHARS or _HARNESS_TEXT.match(m.text):
            continue
        if not _CORRECTION.match(m.text):
            continue
        signals.append(
            Signal(
                id=f"correction-{m.uuid[:8]}",
                kind="correction",
                commit_sha="",  # evidence is the session message, not a commit
                files=[],
                text=m.text,
                author_is_agent=True,
                score=0.5,
                ts=m.ts,
            )
        )
    return signals
