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


def test_unrelated_deletions_in_a_busy_file_are_not_signals(tmp_path: Path) -> None:
    from rulesmith.mine.detectors import detect_reverts

    fx.build_busy_file(tmp_path / "r")
    history = read_commits(tmp_path / "r")
    assert detect_refixes(history) == []
    # 25 lines were deleted against 10 added, but none of the deleted lines were the agent's
    assert detect_reverts(history) == []


def test_the_signal_excerpt_shows_the_humans_fix(tmp_path: Path) -> None:
    fx.build_refix(tmp_path / "r")
    (signal,) = detect_refixes(read_commits(tmp_path / "r"))
    assert signal.diff_excerpt == "- from app.settings import X\n+ from app.config import X"
