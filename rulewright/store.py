"""SQLite cache in .rulewright/: embeddings now, signals and rule history in later phases.

All SQL lives in this module.
"""

import sqlite3
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Vector = NDArray[np.float32]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    model TEXT NOT NULL,
    key   TEXT NOT NULL,
    dim   INTEGER NOT NULL,
    vec   BLOB NOT NULL,
    PRIMARY KEY (model, key)
)
"""


def open_store(repo: Path) -> sqlite3.Connection:
    """The embedding cache. It is only a speed-up, so a damaged file is replaced and an
    unwritable folder (a read-only checkout) falls back to a cache held in memory."""
    d = repo / ".rulewright"
    path = d / "cache.db"
    try:
        d.mkdir(exist_ok=True)
        try:
            return connect(path)
        except sqlite3.DatabaseError:  # "file is not a database" and similar
            for leftover in (path, d / "cache.db-wal", d / "cache.db-shm"):
                leftover.unlink(missing_ok=True)
            return connect(path)
    except (OSError, sqlite3.Error):
        return connect(":memory:")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    return conn


def get_embeddings(conn: sqlite3.Connection, model: str, keys: list[str]) -> dict[str, Vector]:
    found: dict[str, Vector] = {}
    for i in range(0, len(keys), 500):  # stay under SQLite's variable limit
        chunk = keys[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT key, vec FROM embeddings WHERE model = ? AND key IN ({marks})",  # noqa: S608
            [model, *chunk],
        )
        for key, blob in rows:
            found[key] = np.frombuffer(blob, dtype=np.float32)
    return found


def put_embeddings(conn: sqlite3.Connection, model: str, items: dict[str, Vector]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO embeddings (model, key, dim, vec) VALUES (?, ?, ?, ?)",
        [(model, k, len(v), v.astype(np.float32).tobytes()) for k, v in items.items()],
    )
    conn.commit()
