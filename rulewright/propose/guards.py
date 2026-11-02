"""Safety checks on proposed rules: risk scanner, scope sanity, evidence verification.

Mined text is hostile input. A flagged rule is shown in red and never pre-selected; a rule
without verified evidence is dropped.
"""

import re
import subprocess
from pathlib import Path

from rulewright.mine.signals import Signal

MAX_WORDS = 30
MIN_EVIDENCE = 3
TOP_UP_MAX = 1  # evidence the cluster may add when the model cited all but this many

_NEGATION = re.compile(
    r"\b(never|don'?t|do not|doesn'?t|avoid|must not|should not|shouldn'?t|without|instead of"
    r"|rather than|stop)\b",
    re.I,
)
NEGATION_WINDOW = 40  # characters before a match in which a negation makes it a prohibition

# (flag, pattern, whether a nearby negation clears it)
_RISKS: list[tuple[str, re.Pattern[str], bool]] = [
    ("url", re.compile(r"https?://|\bwww\.|\b[\w-]+\.(com|net|org|io|sh|xyz)/", re.I), False),
    ("network-command", re.compile(r"\b(curl|wget|nc|netcat|ssh|scp|ftp)\b", re.I), True),
    (
        "credentials",
        re.compile(
            r"\b(password|passwd|secret|api[_ -]?key|access[_ -]?key|credentials?|private key"
            r"|auth(?:orization)? token|bearer)\b",
            re.I,
        ),
        False,
    ),
    ("env-file", re.compile(r"(?<![\w])\.env\b|\benv(?:ironment)? files?\b", re.I), False),
    (
        "new-shell-command",
        re.compile(
            r"\b(sudo|chmod|chown|eval|exec|bash -c|sh -c|rm -rf?)\b"
            r"|\b(pip3?|npm|yarn|pnpm|apt(?:-get)?|brew|cargo|gem|go)\s+(install|add|get)\b"
            r"|\|\s*(ba)?sh\b",
            re.I,
        ),
        True,
    ),
    (
        "disables-tests",
        re.compile(
            r"\b(skip|disable|ignore|delete|remove|comment out|bypass|xfail)\w*\s+"
            r"(?:\w+\s+){0,2}(tests?|checks?|ci|lint\w*|type ?check\w*)\b"
            r"|--no-verify|\bnoqa\b|\btype:\s*ignore\b",
            re.I,
        ),
        True,
    ),
    (
        "disables-permissions",
        re.compile(
            r"\b(disable|bypass|skip|turn off|override|ignore)\w*\s+(?:\w+\s+){0,3}"
            r"(permissions?|sandbox\w*|approvals?|auth\w*|security|hooks?|safeguards?|guard\w*)\b"
            r"|\bdangerously\b|--yes\b|\bchmod\s+777\b|\ballow all\b",
            re.I,
        ),
        True,
    ),
    (
        "injection-phrase",
        re.compile(
            r"ignore (all |any )?(previous|prior|above)"
            r"|disregard (the |all )?(previous|above|rules)"
            r"|system prompt|you are now|new instructions|act as\b",
            re.I,
        ),
        False,
    ),
]


def _negated(text: str, start: int) -> bool:
    return bool(_NEGATION.search(text[max(0, start - NEGATION_WINDOW) : start]))


def risk_flags(rule_text: str) -> list[str]:
    """Names of every risk category the rule text trips. Prohibitions ("never skip tests") pass."""
    flags = []
    for name, pattern, negation_clears in _RISKS:
        for m in pattern.finditer(rule_text):
            if negation_clears and _negated(rule_text, m.start()):
                continue
            flags.append(name)
            break
    if len(rule_text.split()) > MAX_WORDS:
        flags.append("too-long")
    return flags


def clean_scope(scope: list[str]) -> list[str]:
    """Keep repo-relative globs only; fall back to the whole repo."""
    ok = [s.strip() for s in scope if s.strip() and not s.startswith(("/", "~")) and ".." not in s]
    ok = [s for s in ok if ":" not in s]
    return ok[:5] or ["**"]


def sha_exists(repo: Path, sha: str) -> bool:
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        return False
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
    )
    return result.returncode == 0


def distinct_evidence(signals: list[Signal]) -> list[Signal]:
    """One signal per commit (or per session message). One agent commit with three follow-up
    fixes is one piece of evidence, not three."""
    seen: set[str] = set()
    out = []
    for s in signals:
        key = s.commit_sha or s.id
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def verified_evidence(repo: Path, signals: list[Signal]) -> list[Signal]:
    """Signals whose evidence is real: an existing commit, or a session message (no SHA)."""
    return [s for s in signals if s.commit_sha == "" or sha_exists(repo, s.commit_sha)]
