"""HDBSCAN clustering of signal embeddings: one cluster is one candidate rule."""

import hashlib
import itertools
import re

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel
from sklearn.cluster import HDBSCAN

from rulewright.mine.signals import Signal
from rulewright.store import Vector

MIN_CLUSTER_SIZE = 3  # spec: only clusters of 3+ signals become rules
MIN_CLUSTERS = 3  # spec: fewer than this triggers the cold-start message
# A cluster whose changes share fewer names than this is unrelated edits that merely look alike
# (import fixes after files move). On litellm it kept all 3 clusters judged good by hand and
# dropped 6 of 7 judged weak; 10 clusters is a small sample, so the floor is kept low.
COHERENCE_FLOOR = 0.10
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


class Cluster(BaseModel):
    id: str
    signal_ids: list[str]
    centroid: list[float]
    summary: str  # text of the signal closest to the centroid
    size: int


def coherence(signals: list[Signal]) -> float:
    """Mean pairwise overlap (Jaccard) of the identifiers in what each signal changed, 0 to 1.

    A repeated mistake keeps touching the same names; unrelated churn does not.
    """
    names = [set(_IDENTIFIER.findall(s.diff_excerpt or s.text)) for s in signals]
    pairs = [len(a & b) / len(a | b) for a, b in itertools.combinations(names, 2) if a | b]
    return sum(pairs) / len(pairs) if pairs else 0.0


def cluster_signals(
    signals: list[Signal], vectors: list[Vector], min_size: int = MIN_CLUSTER_SIZE
) -> list[Cluster]:
    """Group similar signals. Noise and groups smaller than `min_size` are dropped."""
    if len(signals) != len(vectors):
        raise ValueError("one vector per signal is required")
    if len(signals) < min_size:
        return []
    matrix = np.vstack(vectors)
    labels = _labels(matrix, min_size, allow_single=False)
    if not any(label != -1 for label in labels.tolist()):
        # HDBSCAN cannot return one cluster unless allowed to, but allowing it everywhere
        # merged unrelated mistakes into one blob on real repositories; use it only as a retry
        labels = _labels(matrix, min_size, allow_single=True)
    clusters = []
    for label in sorted(set(labels.tolist()) - {-1}):
        idx = [i for i, lab in enumerate(labels) if lab == label]
        if len(idx) < min_size:
            continue
        # a repeated mistake is repeated across commits: one commit followed by three fixes
        # is one occurrence
        if len({signals[i].commit_sha or signals[i].id for i in idx}) < min_size:
            continue
        centroid = matrix[idx].mean(axis=0)
        nearest = idx[int(np.argmin(np.linalg.norm(matrix[idx] - centroid, axis=1)))]
        ids = sorted(signals[i].id for i in idx)
        clusters.append(
            Cluster(
                id="c-" + hashlib.sha1("|".join(ids).encode()).hexdigest()[:8],
                signal_ids=ids,
                centroid=[float(x) for x in centroid],
                summary=signals[nearest].text,
                size=len(idx),
            )
        )
    return sorted(clusters, key=lambda c: (-c.size, c.id))


def _labels(matrix: NDArray[np.float32], min_size: int, allow_single: bool) -> NDArray[np.intp]:
    model = HDBSCAN(min_cluster_size=min_size, allow_single_cluster=allow_single, copy=True)
    return model.fit_predict(matrix)  # type: ignore[no-any-return]


def cold_start_message(n_clusters: int, commits: int) -> str | None:
    """Honest message when there is too little to propose, instead of inventing weak rules."""
    if n_clusters >= MIN_CLUSTERS:
        return None
    return (
        f"Found {n_clusters} repeated mistake pattern(s); at least {MIN_CLUSTERS} are needed "
        f"for useful rules. Try a deeper scan (--commits {max(commits * 4, 2000)}) or add "
        "agent session logs."
    )
