"""Build throwaway git repos with planted agent mistakes, so detectors have known answers."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
AGENT_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    config = {"user.name": "Dev", "user.email": "dev@example.com", "commit.gpgsign": "false"}
    for k, v in config.items():
        _git(path, "config", k, v)
    return path


def commit(
    repo: Path, files: dict[str, str], message: str, when: datetime, agent: bool = False
) -> str:
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    msg = f"{message}\n\n{AGENT_TRAILER}" if agent else message
    _git(repo, "commit", "-q", "-m", msg, when=when)
    return _git(repo, "rev-parse", "HEAD")


def build_reverted_agent_commit(path: Path) -> dict[str, str]:
    """An agent commit reverted by a human the next day, plus an unrelated human commit."""
    repo = init_repo(path)
    commit(repo, {"app/config.py": "DEBUG = False\n"}, "initial", T0)
    agent = commit(
        repo,
        {"app/settings.py": "".join(f"X{i} = {i}\n" for i in range(10))},
        "add settings module",
        T0 + timedelta(hours=1),
        agent=True,
    )
    _git(repo, "revert", "--no-edit", agent, when=T0 + timedelta(days=1))
    revert = _git(repo, "rev-parse", "HEAD")
    commit(repo, {"README.md": "hi\n"}, "docs", T0 + timedelta(days=2))
    return {"agent": agent, "revert": revert}


def build_late_revert(path: Path) -> dict[str, str]:
    """A revert of an agent commit after 8 days: outside the 7-day window."""
    repo = init_repo(path)
    commit(repo, {"a.txt": "a\n"}, "initial", T0)
    agent = commit(repo, {"b.txt": "b\n" * 5}, "agent change", T0 + timedelta(hours=1), agent=True)
    _git(repo, "revert", "--no-edit", agent, when=T0 + timedelta(days=8))
    return {"agent": agent}


def build_human_revert(path: Path) -> dict[str, str]:
    """A revert of a human commit: not an agent mistake."""
    repo = init_repo(path)
    commit(repo, {"a.txt": "a\n"}, "initial", T0)
    human = commit(repo, {"b.txt": "b\n" * 5}, "human change", T0 + timedelta(hours=1))
    _git(repo, "revert", "--no-edit", human, when=T0 + timedelta(days=1))
    return {"human": human}


def build_refix(path: Path) -> dict[str, str]:
    """An agent line rewritten by a human 5 hours later, then an unrelated human edit."""
    repo = init_repo(path)
    lines = [f"line{i}\n" for i in range(1, 21)]
    commit(repo, {"app/mod.py": "".join(lines)}, "initial", T0)
    lines[9] = "from app.settings import X\n"
    agent = commit(
        repo, {"app/mod.py": "".join(lines)}, "agent import", T0 + timedelta(hours=1), agent=True
    )
    lines[9] = "from app.config import X\n"
    fix = commit(repo, {"app/mod.py": "".join(lines)}, "fix import", T0 + timedelta(hours=6))
    lines[18] = "unrelated edit\n"
    commit(repo, {"app/mod.py": "".join(lines)}, "tweak elsewhere", T0 + timedelta(hours=7))
    return {"agent": agent, "fix": fix}


def build_late_refix(path: Path) -> dict[str, str]:
    """The same fix, but 3 days later: outside the 48 hour window."""
    repo = init_repo(path)
    commit(repo, {"m.py": "a\nb\nc\n"}, "initial", T0)
    agent = commit(repo, {"m.py": "a\nBAD\nc\n"}, "agent", T0 + timedelta(hours=1), agent=True)
    commit(repo, {"m.py": "a\ngood\nc\n"}, "fix", T0 + timedelta(days=3))
    return {"agent": agent}
