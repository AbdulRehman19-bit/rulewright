from datetime import UTC, datetime, timedelta

from rulesmith.mine.score import score_signals
from rulesmith.mine.signals import Signal

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _sig(sid: str, score: float, age_days: float) -> Signal:
    return Signal(
        id=sid, kind="revert", commit_sha="a" * 7, files=[], text="", author_is_agent=True,
        score=score, ts=NOW - timedelta(days=age_days),
    )  # fmt: skip


def test_recent_signal_keeps_its_score() -> None:
    (s,) = score_signals([_sig("a", 1.0, 0)], NOW)
    assert s.score == 1.0


def test_old_signal_is_discounted_and_weak_ones_dropped() -> None:
    kept = score_signals([_sig("a", 1.0, 180), _sig("b", 0.6, 540)], NOW)
    assert [(s.id, round(s.score, 2)) for s in kept] == [("a", 0.5)]  # b: 0.6 * 0.125 < 0.2
