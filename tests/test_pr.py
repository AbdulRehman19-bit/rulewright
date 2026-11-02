import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rulewright.pr import PRError, commit_url, create_pr, github_slug
from rulewright.rules.emit import MarkerError
from rulewright.rules.emit.base import START
from rulewright.rules.model import Evidence, Rule
from rulewright.rules.source import RulesFile
from tests.fixtures import build_repos as fx

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
SHAS = ["a" * 40, "b" * 40, "c" * 40]


def _rule() -> Rule:
    evidence = [
        Evidence(rule_id="r-1", signal_id=f"s{i}", commit_sha=sha) for i, sha in enumerate(SHAS)
    ]
    return Rule(id="r-1", text="Keep it simple.", scope=["app/**"], evidence=evidence)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class FakeGh:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.body = ""
        self.fail = fail

    def __call__(self, args: list[str], cwd: Path) -> str:
        self.calls.append(args)
        if self.fail:
            raise PRError("gh is not logged in")
        self.body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
        return "https://github.com/o/r/pull/7"


def _repo_with_remote(tmp_path: Path) -> Path:
    repo = fx.init_repo(tmp_path / "work")
    fx.commit(repo, {"README.md": "hi\n"}, "initial", fx.T0)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    return repo


def test_branch_is_pushed_and_pull_request_opened(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    gh = FakeGh()
    result = create_pr(repo, [_rule()], RulesFile(), NOW, gh)
    assert result.branch == "rulewright/rules-20260301-120000"
    assert result.pushed and result.url == "https://github.com/o/r/pull/7" and result.notes == []
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # branch restored
    assert not (repo / "rulewright.yaml").exists()  # main is untouched
    assert result.branch in _git(repo, "ls-remote", "--heads", "origin")
    files = _git(repo, "show", "--name-only", "--format=", result.branch).split()
    assert set(files) == {"rulewright.yaml", "AGENTS.md", "CLAUDE.md", ".cursorrules"}
    assert "Keep it simple." in _git(repo, "show", f"{result.branch}:AGENTS.md")
    assert gh.calls[0][:2] == ["pr", "create"] and "r-1" in gh.body and "aaaaaaa" in gh.body


def test_commit_message_has_no_attribution_trailer(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    result = create_pr(repo, [_rule()], RulesFile(), NOW, FakeGh())
    message = _git(repo, "log", "-1", "--format=%B", result.branch)
    assert message == "Add 1 rule(s) learned by rulewright"


def test_no_remote_keeps_the_branch_local(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "work")
    fx.commit(repo, {"README.md": "hi\n"}, "initial", fx.T0)
    gh = FakeGh()
    result = create_pr(repo, [_rule()], RulesFile(), NOW, gh)
    assert not result.pushed and result.url is None and gh.calls == []
    assert any("No git remote" in n for n in result.notes)
    assert result.branch in _git(repo, "branch", "--list")


def test_gh_failure_is_reported_but_the_branch_is_pushed(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    result = create_pr(repo, [_rule()], RulesFile(), NOW, FakeGh(fail=True))
    assert result.pushed and result.url is None
    assert any("no pull request opened" in n for n in result.notes)


def test_broken_markers_abort_before_a_branch_exists(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    fx.commit(repo, {"AGENTS.md": f"{START}\nnever closed\n"}, "bad agents file", fx.T0)
    with pytest.raises(MarkerError):
        create_pr(repo, [_rule()], RulesFile(), NOW, FakeGh())
    assert "rulewright/" not in _git(repo, "branch", "--list")


def test_uncommitted_edits_to_target_files_are_refused(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    fx.commit(repo, {"AGENTS.md": "# mine\n"}, "agents", fx.T0)
    (repo / "AGENTS.md").write_text("# mine, edited\n", encoding="utf-8")
    with pytest.raises(PRError, match="uncommitted"):
        create_pr(repo, [_rule()], RulesFile(), NOW, FakeGh())
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "# mine, edited\n"


def test_github_urls_for_evidence(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "w")
    assert github_slug(repo) is None and commit_url(repo, "abc") == ""
    for url in ["https://github.com/o/r.git", "git@github.com:o/r.git", "https://github.com/o/r"]:
        _git(repo, "remote", "add", "origin", url)
        assert github_slug(repo) == "o/r"
        _git(repo, "remote", "remove", "origin")
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    assert commit_url(repo, "abc") == "https://github.com/o/r/commit/abc"
