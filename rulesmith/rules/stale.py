"""Stale rule detection: dead paths, dead commands, dead scopes and silent rules."""

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from rulesmith.mine.signals import Signal
from rulesmith.rules.model import Rule

SILENT_AFTER = timedelta(days=90)
_PATH_EXT = r"(?:py|js|ts|tsx|jsx|md|toml|json|yaml|yml|cfg|ini|txt|sh|go|rs|java|rb|sql)"
_PATH = re.compile(rf"[\w@.-]+(?:/[\w@.*-]+)+/?|[\w-]+\.{_PATH_EXT}\b")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_STOPWORDS = {"never", "always", "that", "this", "with", "from", "when", "should", "instead", "use"}
_WORD = re.compile(r"[a-z][a-z0-9_.]{3,}")
MIN_SHARED_WORDS = 2


class StaleFinding(BaseModel):
    rule_id: str
    reason: str  # dead-path | dead-command | dead-scope | silent
    detail: str


def glob_match(path: str, pattern: str) -> bool:
    """`**` crosses directories, `*` and `?` do not."""
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.fullmatch(regex, path) is not None


def tracked_files(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return {line for line in out.splitlines() if line}


def _path_exists(token: str, files: set[str]) -> bool:
    token = token.rstrip("/")
    return token in files or any(f.startswith(token + "/") for f in files)


def _make_targets(repo: Path) -> set[str]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return set()
    return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:", makefile.read_text("utf-8"), re.M))


def _npm_scripts(repo: Path) -> set[str]:
    pkg = repo / "package.json"
    if not pkg.is_file():
        return set()
    try:
        return set(json.loads(pkg.read_text("utf-8")).get("scripts", {}))
    except (ValueError, AttributeError):
        return set()


def _dead_commands(text: str, repo: Path) -> list[str]:
    dead = []
    for span in _BACKTICKED.findall(text):
        parts = span.split()
        if len(parts) >= 2 and parts[0] == "make" and parts[1] not in _make_targets(repo):
            dead.append(span)
        elif len(parts) >= 3 and parts[:2] == ["npm", "run"] and parts[2] not in _npm_scripts(repo):
            dead.append(span)
    return dead


def _words(text: str) -> set[str]:
    """Lowercase content words, trailing dots and plural s removed so "imports" meets "import"."""
    words = {w.rstrip(".").removesuffix("s") for w in _WORD.findall(text.lower())}
    return words - _STOPWORDS


def _signal_matches(rule: Rule, signal: Signal) -> bool:
    if rule.scope != ["**"] and any(
        glob_match(f, pat) for f in signal.files for pat in rule.scope
    ):
        return True
    return len(_words(rule.text) & _words(signal.text)) >= MIN_SHARED_WORDS


def stale_findings(
    rules: list[Rule],
    repo: Path,
    signals: list[Signal] | None = None,
    now: datetime | None = None,
) -> list[StaleFinding]:
    """Findings for accepted rules. Silent-rule detection runs only when `signals` is given."""
    files = tracked_files(repo)
    findings = []
    for rule in rules:
        if rule.status != "accepted":
            continue
        text = _URL.sub(" ", rule.text)
        for raw in dict.fromkeys(_PATH.findall(text)):
            token = raw.rstrip(".-")  # sentence punctuation is not part of a path
            if "*" in token:
                continue
            if not _path_exists(token, files):
                findings.append(StaleFinding(rule_id=rule.id, reason="dead-path", detail=token))
        for cmd in _dead_commands(rule.text, repo):
            findings.append(StaleFinding(rule_id=rule.id, reason="dead-command", detail=cmd))
        if rule.scope != ["**"] and not any(glob_match(f, p) for f in files for p in rule.scope):
            findings.append(
                StaleFinding(rule_id=rule.id, reason="dead-scope", detail=", ".join(rule.scope))
            )
        if signals is not None and now is not None:
            recent = [s for s in signals if now - s.ts <= SILENT_AFTER]
            if not any(_signal_matches(rule, s) for s in recent):
                findings.append(
                    StaleFinding(
                        rule_id=rule.id,
                        reason="silent",
                        detail=f"no matching signal in the last {SILENT_AFTER.days} days",
                    )
                )
    return findings
