"""Turn `git log` into uniform commit events. No interpretation beyond agent attribution."""

import re
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

_AGENT_TRAILER = re.compile(r"^co-authored-by:.*\b(claude|codex|copilot|cursor)\b", re.I | re.M)
_BOT_AUTHOR = re.compile(r"(\[bot\]|-bot\b|\bbot$)", re.I)
_REC = "\x1e"  # starts a commit record
_FLD = "\x1f"  # separates header fields
_END = "\x1d"  # ends the header; numstat and patch follow
MAX_PATCH_CHARS = 20_000

Range = tuple[int, int]  # inclusive line range


class FileChange(BaseModel):
    path: str
    added: int
    deleted: int
    old_ranges: list[Range] = []  # lines removed or replaced, in the parent's numbering
    new_ranges: list[Range] = []  # lines added or replaced, in this commit's numbering


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
    return bool(
        _AGENT_TRAILER.search(body)
        or _BOT_AUTHOR.search(author_name)
        or _BOT_AUTHOR.search(author_email)
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def read_commits(repo: Path, limit: int = 500) -> list[Commit]:
    """Return up to `limit` recent commits, oldest first."""
    fmt = _FLD.join(["%H", "%P", "%an", "%ae", "%aI", "%s", "%b"]) + _END
    out = _git(
        repo, "log", f"-n{limit}", "--no-renames", "--numstat", "-p", "-U0",
        f"--format={_REC}{fmt}",
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


def _parse_hunks(patch: str) -> dict[str, tuple[list[Range], list[Range]]]:
    hunks: dict[str, tuple[list[Range], list[Range]]] = {}
    current: str | None = None
    for line in patch.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            current = m.group(1)
            hunks[current] = ([], [])
            continue
        h = _HUNK.match(line)
        if h and current is not None:
            old = _span(h.group(1), h.group(2))
            new = _span(h.group(3), h.group(4))
            if old:
                hunks[current][0].append(old)
            if new:
                hunks[current][1].append(new)
    return hunks


def _parse_files(stat: str, patch: str) -> list[FileChange]:
    hunks = _parse_hunks(patch)
    changes = []
    for line in stat.splitlines():
        m = _NUMSTAT.match(line)
        if m:
            a, d, path = m.groups()
            old, new = hunks.get(path, ([], []))
            changes.append(
                FileChange(
                    path=path,
                    added=0 if a == "-" else int(a),
                    deleted=0 if d == "-" else int(d),
                    old_ranges=old,
                    new_ranges=new,
                )
            )
    return changes
