"""Local embeddings of signal summaries, cached in SQLite so a rescan only embeds new text."""

import hashlib
import sqlite3
from typing import Protocol

import numpy as np

from rulesmith.mine.signals import Signal
from rulesmith.store import Vector, get_embeddings, put_embeddings

SUMMARY_CHANGE_CHARS = 200
DEFAULT_MODEL ="sentence-transformers/all-MiniLM-L6-v2"


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[Vector]: ...


class SentenceTransformerEmbedder:
    """Small offline model; downloaded on first use, no API key."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.name = model
        self._model: object | None = None

    def embed(self, texts: list[str]) -> list[Vector]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # slow import, load lazily

            self._model = SentenceTransformer(self.name)
        vecs = self._model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [np.asarray(v, dtype=np.float32) for v in vecs]


def signal_summary(signal: Signal) -> str:
    """Short text describing what went wrong; this is what gets embedded.

    The human's fix (removed and added lines) comes first: it carries the mistake itself,
    where the commit subjects of a busy repository are all different.
    """
    change = " ".join(signal.diff_excerpt.split())[:SUMMARY_CHANGE_CHARS]
    files = ", ".join(signal.files[:5])
    return (
        (f"change: {change} | " if change else "")
        + f"{signal.kind}: {signal.text}"
        + (f" (files: {files})" if files else "")
    )


def embed_signals(
    signals: list[Signal], embedder: Embedder, conn: sqlite3.Connection | None = None
) -> list[Vector]:
    """One vector per signal, in order. Cached vectors are reused when `conn` is given."""
    texts = [signal_summary(s) for s in signals]
    keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    cached = get_embeddings(conn, embedder.name, keys) if conn is not None else {}
    missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in cached}
    if missing:
        fresh = dict(zip(missing, embedder.embed(list(missing.values())), strict=True))
        if conn is not None:
            put_embeddings(conn, embedder.name, fresh)
        cached.update(fresh)
    return [cached[k] for k in keys]
