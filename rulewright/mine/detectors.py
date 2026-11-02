"""Detectors turn commit events into Signals. Each has a known right answer in the fixtures.

Revert and re-fix matching is by line content: a later commit counts only if it removes lines
the agent commit added. Counting deletions anywhere in a touched file flagged most commits to
busy files in real repositories.
"""

import re
from collections import Counter
from datetime import timedelta

from rulesmith.ingest.git_history import Commit, FileChange
from rulesmith.ingest.sessions.base import SessionMessage
from rulesmith.mine.signals import Signal

REVERT_WINDOW = timedelta(days=7)
REFIX_WINDOW = timedelta(hours=48)
EXCERPT_CHARS = 400
MAX_EXCERPT_LINES = 3
# A later commit "deletes most of" an agent commit when it removes this share of its added lines.
MOSTLY_DELETED = 0.5
_REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
# Lockfiles, bundles and the like: edited by tools and everyone, never evidence of a mistake.
_GENERATED = re.compile(
    r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.lock|uv\.lock|poetry\.lock"
    r"|Gemfile\.lock|composer\.lock|go\.sum)$|\.lock$|\.min\.(js|css)$|\.snap$|\.svg$|\.map$",
    re.I,
)


def _significant(line: str) -> bool:
    """Skip braces, blank lines and other lines too common to identify anything."""
    s = line.strip()
    return len(s) >= 4 and sum(c.isalnum() for c in s) >= 3


class _Added:
    """The significant lines an agent commit added, per file, indexed once.

    Pair checks run for every (later commit, agent commit) inside the time window, so building
    this per pair made scanning 500 commits of a busy repository take minutes.
    """

    def __init__(self, commit: Commit) -> None:
        self.pools: dict[str, Counter[str]] = {}
        self.total = 0
        for f in commit.files:
            if _GENERATED.search(f.path):
                continue
            pool = Counter(line for line in f.added_lines if _significant(line))
            if pool:
                self.pools[f.path] = pool
                self.total += sum(pool.values())


def _removed_agent_lines(later: Commit, added: _Added) -> dict[str, list[str]]:
    """Per file, the lines `later` removes that the agent commit added (each matches once)."""
    out: dict[str, list[str]] = {}
    for f in later.files:
        pool = added.pools.get(f.path)
        if pool is None:
            continue
        candidates = [line for line in f.removed_lines if line in pool]
        if not candidates:
            continue
        left = pool.copy()
        hits = []
        for line in candidates:
            if left[line] > 0:
                left[line] -= 1
                hits.append(line)
        if hits:
            out[f.path] = hits
    return out


def _fix_excerpt(later: Commit, removed: dict[str, list[str]]) -> str:
    """What the human changed: the agent's lines that were removed, and what replaced them."""
    lines: list[str] = []
    for path, hits in list(removed.items())[:2]:
        lines += [f"- {line}" for line in hits[:MAX_EXCERPT_LINES]]
        file = next(f for f in later.files if f.path == path)
        lines += [f"+ {line}" for line in file.added_lines[:MAX_EXCERPT_LINES]]
    return "\n".join(lines)[:EXCERPT_CHARS]


def _added_excerpt(target: Commit) -> str:
    lines = [
        f"+ {line}"
        for f in target.files
        if not _GENERATED.search(f.path)
        for line in f.added_lines
        if _significant(line)
    ]
    return "\n".join(lines[:MAX_EXCERPT_LINES])[:EXCERPT_CHARS]


def detect_reverts(commits: list[Commit]) -> list[Signal]:
    """Commits that revert, or delete most of, an agent commit within 7 days."""
    agent = [c for c in commits if c.is_agent]
    added = {c.sha: _Added(c) for c in agent}
    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()
    for later in commits:
        for target in agent:
            if target.sha == later.sha or later.ts <= target.ts:
                continue
            if later.ts - target.ts > REVERT_WINDOW:
                continue
            explicit = _explicitly_reverts(later, target)
            removed = {} if explicit else _deletes_most_of(later, added[target.sha])
            if not explicit and not removed:
                continue
            key = (later.sha, target.sha)
            if key in seen:
                continue
            seen.add(key)
            excerpt = _added_excerpt(target) if explicit else _fix_excerpt(later, removed)
            signals.append(
                Signal(
                    id=f"revert-{target.sha[:7]}-{later.sha[:7]}",
                    kind="revert",
                    commit_sha=target.sha,
                    files=[f.path for f in target.files] if explicit else list(removed),
                    diff_excerpt=excerpt,
                    text=f"{target.subject} -> {later.subject}",
                    author_is_agent=True,
                    score=1.0 if explicit else 0.6,
                    ts=later.ts,
                )
            )
    return signals


def detect_refixes(commits: list[Commit]) -> list[Signal]:
    """Human commits that remove lines an agent commit added, in the same place, within 48h."""
    agent = [c for c in commits if c.is_agent]
    added = {c.sha: _Added(c) for c in agent}
    signals: list[Signal] = []
    for later in commits:
        if later.is_agent:
            continue
        for target in agent:
            if later.ts <= target.ts or later.ts - target.ts > REFIX_WINDOW:
                continue
            if _explicitly_reverts(later, target):
                continue  # already a revert signal
            removed = _removed_agent_lines(later, added[target.sha])
            shared = {p: removed[p] for p in _overlapping_files(later, target) if p in removed}
            if not shared:
                continue
            signals.append(
                Signal(
                    id=f"refix-{target.sha[:7]}-{later.sha[:7]}",
                    kind="refix",
                    commit_sha=target.sha,
                    files=list(shared),
                    diff_excerpt=_fix_excerpt(later, shared),
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

    A human replacing the line an agent just wrote also "deletes most of" that commit, so the
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


def _deletes_most_of(later: Commit, added: _Added) -> dict[str, list[str]]:
    """The removed agent lines per file when `later` removes at least half of them, else {}."""
    if added.total == 0:
        return {}
    removed = _removed_agent_lines(later, added)
    matched = sum(len(v) for v in removed.values())
    return removed if matched >= MOSTLY_DELETED * added.total else {}


def _overlapping_files(later: Commit, target: Commit) -> list[str]:
    """Files where `later` edits the place `target` wrote.

    Line numbers are compared directly, so commits between the two that shift lines can cause
    misses. Content matching (above) is what keeps false matches out.
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
