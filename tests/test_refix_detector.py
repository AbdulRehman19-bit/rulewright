from pathlib import Path

from rulesmith.ingest.git_history import read_commits
from rulesmith.mine.detectors import detect_refixes
from tests.fixtures import build_repos as fx


def test_finds_exactly_the_planted_refix(tmp_path: Path) -> None:
    shas = fx.build_refix(tmp_path / "r")
    signals = detect_refixes(read_commits(tmp_path / "r"))
    assert [(s.kind, s.commit_sha, s.files) for s in signals] == [
        ("refix", shas["agent"], ["app/mod.py"])
    ]
    assert signals[0].id.endswith(shas["fix"][:7])
    assert "app.settings" in signals[0].diff_excerpt


def test_refix_outside_window_is_ignored(tmp_path: Path) -> None:
    fx.build_late_refix(tmp_path / "r")
    assert detect_refixes(read_commits(tmp_path / "r")) == []


def test_revert_is_not_double_counted_as_refix(tmp_path: Path) -> None:
    fx.build_reverted_agent_commit(tmp_path / "r")
    assert detect_refixes(read_commits(tmp_path / "r")) == []
