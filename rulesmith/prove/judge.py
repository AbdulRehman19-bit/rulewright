"""Did the mistake recur? Compares the workspace before and after an agent run."""

import os
from collections import Counter
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MIN_LINE_CHARS = 4


def snapshot(root: Path) -> dict[str, str]:
    """Text of every regular file under `root`.

    Symlinks, which an agent in a sandbox can create, are never followed, so a link pointing at
    a host path cannot be read. Binary and very large files are skipped.
    """
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return files


def added_lines(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Stripped lines present after the run that were not there before (per file, by count)."""
    out: list[str] = []
    for path, text in after.items():
        old = Counter(line.strip() for line in before.get(path, "").splitlines())
        new = Counter(line.strip() for line in text.splitlines())
        out += list((new - old).elements())
    return out


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(p for p in after.keys() | before.keys() if before.get(p) != after.get(p))


def recurred(before: dict[str, str], after: dict[str, str], bad_lines: list[str]) -> bool | None:
    """True when the agent wrote one of the lines a human later removed.

    None when there are no such lines to look for; the caller then needs an LLM judge.
    """
    bad = {line.strip() for line in bad_lines if len(line.strip()) >= MIN_LINE_CHARS}
    if not bad:
        return None
    return any(line in bad for line in added_lines(before, after))


def diff_text(before: dict[str, str], after: dict[str, str], limit: int = 4000) -> str:
    """A compact view of what changed, for an LLM judge."""
    lines = [f"changed files: {', '.join(changed_files(before, after)) or 'none'}"]
    lines += [f"+ {line}" for line in added_lines(before, after)]
    return "\n".join(lines)[:limit]
