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
    d = repo / ".rulewright"
    d.mkdir(exist_ok=True)
    return connect(d / "cache.db")


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
