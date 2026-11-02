"""Rebuild past agent tasks from a rule's evidence so they can be replayed.

A task is: the tree of the commit before the agent's, a prompt taken from that commit's message,
and the lines a human later removed from the agent's work (the mistake, for the judge).
Only the parent's tree is extracted, so the agent never sees the fix or anything newer.
"""

import io
import re
import subprocess
import tarfile
from pathlib import Path

from pydantic import BaseModel

from rulesmith.ingest.git_history import Commit
from rulesmith.mine.signals import Signal
from rulesmith.rules.model import Rule

MAX_TASKS = 10  # spec: up to 10 past tasks
MAX_PROMPT_CHARS = 1500
_TRAILER = re.compile(
    r"^(co-authored-by|signed-off-by|generated with|🤖).*$", re.I | re.M
)  # attribution lines are not part of the task


class Task(BaseModel):
    commit_sha: str  # the agent commit that made the mistake
    parent_sha: str
    prompt: str
    bad_lines: list[str]  # lines the human fix removed; empty when only an LLM can judge
    files: list[str]


def task_prompt(commit: Commit) -> str:
    """The agent commit's own message, as the closest thing to what was asked."""
    text = _TRAILER.sub("", f"{commit.subject}\n\n{commit.body}")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_PROMPT_CHARS]


def _bad_lines(signals: list[Signal], sha: str) -> list[str]:
    """Removed (`- `) lines from the human fix excerpts of every signal on this commit."""
    out: list[str] = []
    for s in signals:
        if s.commit_sha != sha:
            continue
        for line in s.diff_excerpt.splitlines():
            if line.startswith("- ") and line[2:].strip() not in out:
                out.append(line[2:].strip())
    return out


def build_tasks(
    rule: Rule, commits: list[Commit], signals: list[Signal], limit: int = MAX_TASKS
) -> list[Task]:
    """One task per distinct evidence commit still present in `commits`, newest first."""
    by_sha = {c.sha: c for c in commits}
    tasks: list[Task] = []
    for sha in dict.fromkeys(e.commit_sha for e in rule.evidence if e.commit_sha):
        commit = by_sha.get(sha)
        if commit is None or not commit.parents:
            continue  # outside the scanned history, or a root commit with nothing to rebuild
        tasks.append(
            Task(
                commit_sha=sha,
                parent_sha=commit.parents[0],
                prompt=task_prompt(commit),
                bad_lines=_bad_lines(signals, sha),
                files=[f.path for f in commit.files],
            )
        )
    tasks.sort(key=lambda t: by_sha[t.commit_sha].ts, reverse=True)
    return tasks[:limit]


def materialize(repo: Path, parent_sha: str, dest: Path) -> None:
    """Extract the tree of `parent_sha` into `dest`: files only, no git history."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", parent_sha],
        capture_output=True,
        check=True,
    )
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")  # refuses absolute paths, links outside, devices
