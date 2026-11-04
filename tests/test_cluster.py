import sqlite3
from datetime import UTC, datetime

import numpy as np

from rulewright.cluster.embed import embed_signals, signal_summary
from rulewright.cluster.group import cluster_signals, cold_start_message
from rulewright.mine.signals import Signal
from rulewright.store import connect
from tests.helpers import FakeEmbedder

TS = datetime(2026, 1, 5, tzinfo=UTC)


def _sig(i: int, text: str) -> Signal:
    return Signal(
        id=f"s{i}", kind="refix", commit_sha=f"{i:07x}", files=["app/x.py"], text=text,
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


def test_one_commit_with_many_fixes_is_not_a_repeated_mistake() -> None:
    sigs = [
        _sig(i, text).model_copy(update={"commit_sha": "a" * 40, "id": f"s{i}"})
        for i, text in enumerate(IMPORTS)
    ]
    assert cluster_signals(sigs, embed_signals(sigs, FakeEmbedder())) == []
    two_commits = [
        s.model_copy(update={"commit_sha": ("b" if i % 4 == 0 else "a") * 40})
        for i, s in enumerate(sigs)
    ]
    assert cluster_signals(two_commits, embed_signals(two_commits, FakeEmbedder())) == []


# --- coherence ----------------------------------------------------------------------------


def _csig(i: int, excerpt: str) -> Signal:
    from datetime import UTC, datetime

    from rulewright.mine.signals import Signal

    return Signal(
        id=f"s{i}", kind="refix", commit_sha=f"{i:040d}", files=["a.py"], diff_excerpt=excerpt,
        text="t", author_is_agent=True, score=0.8, ts=datetime(2026, 1, 1, tzinfo=UTC),
    )  # fmt: skip


def test_coherence_is_high_when_the_same_names_keep_changing() -> None:
    from rulewright.cluster.group import coherence

    same = [_csig(i, "- import settings_loader\n+ import config_loader") for i in range(3)]
    unrelated = [
        _csig(0, "- alpha_value = compute_total(items)"),
        _csig(1, "- render_banner(user_name)"),
        _csig(2, "- retry_budget = parse_header(raw)"),
    ]
    assert coherence(same) == 1.0
    assert coherence(unrelated) < 0.1
    assert coherence([_csig(0, "x")]) == 0.0  # one signal has nothing to compare


def test_select_clusters_skips_incoherent_ones_and_ranks_by_coherence() -> None:
    from rulewright.cluster.group import Cluster
    from rulewright.pipeline import select_clusters

    def cluster(cid: str, sigs: list[Signal]) -> Cluster:
        return Cluster(id=cid, signal_ids=[s.id for s in sigs], centroid=[0.0], summary="x",
                       size=len(sigs))  # fmt: skip

    tight = [_csig(i, "- import settings_loader") for i in range(3)]
    loose = [_csig(10 + i, f"- unique_name_{i} = other_thing_{i}()") for i in range(5)]
    mid = [_csig(20, "- shared_name one_a"), _csig(21, "- shared_name two_b"),
           _csig(22, "- shared_name three_c")]  # fmt: skip
    clusters = [cluster("c-loose", loose), cluster("c-mid", mid), cluster("c-tight", tight)]
    chosen, skipped = select_clusters(clusters, tight + loose + mid, set())
    assert [c.id for c in chosen] == ["c-tight", "c-mid"] and skipped == 1
    only, _ = select_clusters(clusters, tight + loose + mid, {"r-tight"})
    assert [c.id for c in only] == ["c-mid"]  # a rule that already exists is not asked for again


def test_a_damaged_cache_file_is_replaced_and_an_unwritable_folder_falls_back(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from rulewright.store import get_embeddings, open_store, put_embeddings

    repo = tmp_path / "r"
    (repo / ".rulewright").mkdir(parents=True)
    (repo / ".rulewright" / "cache.db").write_bytes(b"this is not a sqlite database at all" * 50)
    conn = open_store(repo)
    put_embeddings(conn, "m", {"k": np.ones(3, dtype=np.float32)})
    assert list(get_embeddings(conn, "m", ["k"])) == ["k"]
    conn.close()

    blocked = tmp_path / "b"
    blocked.mkdir()
    (blocked / ".rulewright").write_text("a file where the folder should be", encoding="utf-8")
    conn = open_store(blocked)  # cannot create the folder: held in memory instead
    put_embeddings(conn, "m", {"k": np.ones(3, dtype=np.float32)})
    assert list(get_embeddings(conn, "m", ["k"])) == ["k"]
