"""`scan --pr`: put accepted rules on a new branch and open a pull request for review.

The pull request is the approval step: the user reviews and merges it (or not). The working
branch is restored afterwards and nothing is changed if a target file has broken markers.
"""

import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from rulewright.rules.emit import plan_emit, write_changes
from rulewright.rules.model import Rule
from rulewright.rules.source import FILENAME, RulesFile, save_rules

_GITHUB = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
Gh = Callable[[list[str], Path], str]


class PRError(Exception):
    pass


class PRResult(BaseModel):
    branch: str
    pushed: bool
    url: str | None
    notes: list[str]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8"
    )
    if check and proc.returncode != 0:
        raise PRError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def github_slug(repo: Path) -> str | None:
    """`owner/name` of the origin remote when it is on GitHub."""
    m = _GITHUB.search(_git(repo, "remote", "get-url", "origin", check=False))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_url(repo: Path, sha: str) -> str:
    slug = github_slug(repo)
    return f"https://github.com/{slug}/commit/{sha}" if slug else ""


def pr_body(rules: list[Rule], repo: Path) -> str:
    lines = ["rulewright proposes these rules. Each cites the evidence behind it.", ""]
    for r in rules:
        lines.append(f"### {r.id}\n> {' '.join(r.text.split())}\n")
        lines.append("Scope: " + ", ".join(f"`{s}`" for s in r.scope) + "\n")
        lines.append("Evidence:")
        for e in r.evidence:
            ref = e.commit_sha or e.signal_id
            url = commit_url(repo, e.commit_sha) if e.commit_sha else ""
            lines.append(f"- [{ref[:7]}]({url})" if url else f"- {ref[:12]}")
        lines.append("")
    return "\n".join(lines)


def _default_gh(args: list[str], cwd: Path) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise PRError("the GitHub CLI (gh) is not installed")
    proc = subprocess.run([gh, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise PRError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def create_pr(
    repo: Path, rules: list[Rule], rules_file: RulesFile, now: datetime, gh: Gh = _default_gh
) -> PRResult:
    """Commit `rules` (as accepted) to a new branch, push it and open a pull request."""
    if not rules:
        raise PRError("no rules to propose")
    updated = rules_file.model_copy(deep=True)
    for rule in rules:
        updated.add(rule, "accepted")
    changes = plan_emit(repo, updated)  # raises before any branch exists if markers are broken
    touched = [FILENAME, *(str(c.path.relative_to(repo)) for c in changes)]
    dirty = _git(repo, "status", "--porcelain", "--", *touched)
    if dirty:
        raise PRError(f"uncommitted changes in files rulewright would edit:\n{dirty}")

    original = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = f"rulewright/rules-{now:%Y%m%d-%H%M%S}"
    _git(repo, "switch", "-q", "-c", branch)
    notes: list[str] = []
    pushed, url = False, None
    try:
        save_rules(repo, updated)
        write_changes(changes)
        _git(repo, "add", "--", *touched)
        _git(repo, "commit", "-q", "-m", f"Add {len(rules)} rule(s) learned by rulewright")
        if _git(repo, "remote", check=False).split():
            _git(repo, "push", "-q", "-u", "origin", branch)
            pushed = True
            try:
                body = pr_body(rules, repo)
                body_file = repo / ".rulewright" / "pr-body.md"
                body_file.parent.mkdir(exist_ok=True)
                body_file.write_text(body, encoding="utf-8")
                url = gh(
                    ["pr", "create", "--title", f"rulewright: {len(rules)} proposed rule(s)",
                     "--body-file", str(body_file)],
                    repo,
                )  # fmt: skip
            except PRError as e:
                notes.append(f"Branch pushed but no pull request opened: {e}")
        else:
            notes.append("No git remote; the branch is local only.")
    finally:
        _git(repo, "switch", "-q", original, check=False)
    return PRResult(branch=branch, pushed=pushed, url=url, notes=notes)
