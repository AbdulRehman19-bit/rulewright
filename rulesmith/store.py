"""SQLite cache in .rulesmith/: signals, embeddings and rule history between runs.

Placeholder until phase 3 (embedding cache). Keep all SQL in this module.
"""

import sqlite3
from pathlib import Path


def open_store(repo: Path) -> sqlite3.Connection:
    d = repo / ".rulesmith"
    d.mkdir(exist_ok=True)
    return sqlite3.connect(d / "cache.db")
