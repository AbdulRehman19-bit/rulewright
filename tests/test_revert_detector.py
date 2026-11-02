from pathlib import Path

from rulesmith.ingest.git_history import read_commits
from rulesmith.mine.detectors import detect_reverts
from tests.fixtures import build_repos as fx


def test_agent_commits_are_attributed(tmp_path: Path) -> None:
    shas = fx.build_reverted_agent_commit(tmp_path / "r")
    commits = {c.sha: c for c in read_commits(tmp_path / "r")}
    assert commits[shas["agent"]].is_agent
    assert not commits[shas["revert"]].is_agent
    assert commits[shas["agent"]].added == 10


def test_finds_exactly_the_planted_revert(tmp_path: Path) -> None:
    shas = fx.build_reverted_agent_commit(tmp_path / "r")
    signals = detect_reverts(read_commits(tmp_path / "r"))
    assert [(s.kind, s.commit_sha, s.score) for s in signals] == [("revert", shas["agent"], 1.0)]


def test_revert_outside_window_is_ignored(tmp_path: Path) -> None:
    fx.build_late_revert(tmp_path / "r")
    assert detect_reverts(read_commits(tmp_path / "r")) == []


def test_revert_of_human_commit_is_ignored(tmp_path: Path) -> None:
    fx.build_human_revert(tmp_path / "r")
    assert detect_reverts(read_commits(tmp_path / "r")) == []
