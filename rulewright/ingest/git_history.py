"""Turn `git log` into uniform commit events. No interpretation beyond agent attribution."""

import re
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

_AGENTS = "claude|codex|copilot|cursor|devin|aider|openhands|jules|gemini|sweep"
_AGENT_TRAILER = re.compile(rf"^co-authored-by:.*\b({_AGENTS})\b", re.I | re.M)
# A bot counts only when it names a coding agent: release and dependency bots are not agents.
_AGENT_AUTHOR = re.compile(
    rf"(?:{_AGENTS})[\w.+-]*\[bot\]|noreply@anthropic\.com|\+(?:copilot|claude)@users\.noreply",
    re.I,
)
_REC = "\x1e"  # starts a commit record
_FLD = "\x1f"  # separates header fields
_END = "\x1d"  # ends the header; numstat and patch follow
MAX_PATCH_CHARS = 20_000
MAX_LINES_PER_FILE = 1000  # per direction; keeps huge generated files from dominating memory
MAX_LINE_CHARS = 200
# Never evidence of an agent mistake and often huge, so git does not even print them.
_EXCLUDE = [
    f":(exclude,glob)**/{pattern}"
    for pattern in (
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "uv.lock",
        "poetry.lock", "Gemfile.lock", "composer.lock", "go.sum", "*.lock", "*.min.js",
        "*.min.css", "*.snap", "*.svg", "*.map",
    )
]  # fmt: skip

Range = tuple[int, int]  # inclusive line range


class FileChange(BaseModel):
    path: str
    added: int
    deleted: int
    old_ranges: list[Range] = []  # lines removed or replaced, in the parent's numbering
    new_ranges: list[Range] = []  # lines added or replaced, in this commit's numbering
    added_lines: list[str] = []  # text of added lines (capped), untrusted
    removed_lines: list[str] = []  # text of removed lines (capped), untrusted


class Commit(BaseModel):
    sha: str
    parents: list[str]
    author_name: str
    author_email: str
    ts: datetime
    subject: str
    body: str
    files: list[FileChange]
    is_agent: bool
    patch: str = ""  # truncated unified diff, untrusted

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deleted for f in self.files)


def is_agent_commit(author_name: str, author_email: str, body: str) -> bool:
    author = f"{author_name} <{author_email}>"
    return bool(_AGENT_TRAILER.search(body) or _AGENT_AUTHOR.search(author))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


class HistoryError(Exception):
    """The history cannot be read; the message says why in plain words."""


def _check_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise HistoryError(f"{repo} is not a folder. Pass the repository with --repo.")
    try:
        inside = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, encoding="utf-8",
        )  # fmt: skip
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
        )  # fmt: skip
    except FileNotFoundError:
        raise HistoryError("git was not found. Install git and make sure it is on PATH.") from None
    if inside.returncode != 0:
        raise HistoryError(
            f"{repo} is not a git repository. Run rulewright inside one, or pass --repo."
        )
    if head.returncode != 0:
        raise HistoryError(f"{repo} has no commits yet, so there is no history to read.")


def read_commits(repo: Path, limit: int = 500) -> list[Commit]:
    """Return up to `limit` recent commits, oldest first. Raises HistoryError if unreadable."""
    if limit < 1:
        raise HistoryError("The number of commits to read must be at least 1.")
    _check_repo(repo)
    fmt = _FLD.join(["%H", "%P", "%an", "%ae", "%aI", "%s", "%b"]) + _END
    out = _git(
        repo, "log", f"-n{limit}", "--no-renames", "--numstat", "-p", "-U0",
        f"--format={_REC}{fmt}", "--", ".", *_EXCLUDE,
    )  # fmt: skip
    commits: list[Commit] = []
    for rec in out.split(_REC)[1:]:
        header, _, rest = rec.partition(_END)
        sha, parents, name, email, ts, subject, body = header.split(_FLD)
        body = body.strip()
        stat, patch = _split_stat_patch(rest)
        commits.append(
            Commit(
                sha=sha,
                parents=parents.split(),
                author_name=name,
                author_email=email,
                ts=datetime.fromisoformat(ts),
                subject=subject,
                body=body,
                files=_parse_files(stat, patch),
                is_agent=is_agent_commit(name, email, body),
                patch=patch[:MAX_PATCH_CHARS],
            )
        )
    commits.reverse()
    return commits


_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")
_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _split_stat_patch(text: str) -> tuple[str, str]:
    idx = text.find("diff --git ")
    if idx == -1:
        return text, ""
    return text[:idx], text[idx:]


def _span(start: str, length: str | None) -> Range | None:
    n = 1 if length is None else int(length)
    s = int(start)
    return (s, s + n - 1) if n > 0 else None


class _Hunks:
    def __init__(self) -> None:
        self.old: list[Range] = []
        self.new: list[Range] = []
        self.added: list[str] = []
        self.removed: list[str] = []


def _parse_hunks(patch: str) -> dict[str, _Hunks]:
    hunks: dict[str, _Hunks] = {}
    current: _Hunks | None = None
    for line in patch.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            current = hunks[m.group(1)] = _Hunks()
            continue
        if current is None:
            continue
        h = _HUNK.match(line)
        if h:
            old = _span(h.group(1), h.group(2))
            new = _span(h.group(3), h.group(4))
            if old:
                current.old.append(old)
            if new:
                current.new.append(new)
        elif line.startswith("+") and not line.startswith("+++"):
            if len(current.added) < MAX_LINES_PER_FILE:
                current.added.append(line[1:].strip()[:MAX_LINE_CHARS])
        elif line.startswith("-") and not line.startswith("---"):
            if len(current.removed) < MAX_LINES_PER_FILE:
                current.removed.append(line[1:].strip()[:MAX_LINE_CHARS])
    return hunks


def _parse_files(stat: str, patch: str) -> list[FileChange]:
    hunks = _parse_hunks(patch)
    changes = []
    for line in stat.splitlines():
        m = _NUMSTAT.match(line)
        if m:
            a, d, path = m.groups()
            h = hunks.get(path) or _Hunks()
            changes.append(
                FileChange(
                    path=path,
                    added=0 if a == "-" else int(a),
                    deleted=0 if d == "-" else int(d),
                    old_ranges=h.old,
                    new_ranges=h.new,
                    added_lines=h.added,
                    removed_lines=h.removed,
                )
            )
    return changes
