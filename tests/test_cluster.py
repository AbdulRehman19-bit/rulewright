import sqlite3
from datetime import UTC, datetime

import numpy as np

from rulesmith.cluster.embed import embed_signals, signal_summary
from rulesmith.cluster.group import cluster_signals, cold_start_message
from rulesmith.mine.signals import Signal
from rulesmith.store import connect
from tests.helpers import FakeEmbedder

TS = datetime(2026, 1, 5, tzinfo=UTC)


def _sig(i: int, text: str) -> Signal:
    return Signal(
        id=f"s{i}", kind="refix", commit_sha="abc1234", files=["app/x.py"], text=text,
        author_is_agent=True, score=0.8, ts=TS,
    )  # fmt: skip


SUFFIXES = ["", "again", "module", "twice", "here", "now", "still", "once more"]
IMPORTS = [f"agent imports settings from app.settings instead of app.config {x}" for x in SUFFIXES]
TESTS = [f"agent deletes failing pytest tests rather than fixing them {x}" for x in SUFFIXES]
NOISE = ["rename variable in readme", "bump docker base image tag"]


def _cluster(texts: list[str]):  # type: ignore[no-untyped-def]
    sigs = [_sig(i, t) for i, t in enumerate(texts)]
    return sigs, cluster_signals(sigs, embed_signals(sigs, FakeEmbedder()))


def test_two_planted_patterns_become_two_clusters() -> None:
    sigs, clusters = _cluster(IMPORTS + TESTS + NOISE)
    assert len(clusters) == 2
    by_pattern = [{f"s{i}" for i in range(0, 8)}, {f"s{i}" for i in range(8, 16)}]
    noise = {"s16", "s17"}
    for c in clusters:
        members = set(c.signal_ids)
        assert not members & noise  # unrelated signals are not forced into a rule
        match = max(by_pattern, key=lambda p: len(p & members))
        assert members <= match and len(members) >= 7  # HDBSCAN may drop one border point
    imports = next(c for c in clusters if "s0" in c.signal_ids or "s1" in c.signal_ids)
    assert "app.settings" in imports.summary


def test_one_repeated_pattern_is_a_single_cluster() -> None:
    _, clusters = _cluster(IMPORTS)
    assert len(clusters) == 1 and clusters[0].size >= 7


def test_too_few_signals_give_no_clusters() -> None:
    assert _cluster(IMPORTS[:2])[1] == []


def test_cluster_ids_are_deterministic() -> None:
    assert [c.id for c in _cluster(IMPORTS + TESTS)[1]] == [
        c.id for c in _cluster(IMPORTS + TESTS)[1]
    ]


def test_embeddings_are_cached_in_sqlite() -> None:
    conn: sqlite3.Connection = connect(":memory:")
    sigs = [_sig(i, t) for i, t in enumerate(IMPORTS)]
    first = FakeEmbedder()
    v1 = embed_signals(sigs, first, conn)
    second = FakeEmbedder()
    v2 = embed_signals(sigs, second, conn)
    assert len(first.calls) == 1 and second.calls == []
    assert all(np.array_equal(a, b) for a, b in zip(v1, v2, strict=True))
    assert signal_summary(sigs[0]).startswith("refix: agent imports")


def test_cold_start_message() -> None:
    assert cold_start_message(3, 500) is None
    msg = cold_start_message(1, 500)
    assert msg and "1 repeated" in msg and "--commits 2000" in msg
