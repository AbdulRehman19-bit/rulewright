"""Load .rulesmith/config.toml. Every key is optional; defaults match the spec."""

import tomllib
from pathlib import Path

from pydantic import BaseModel


class Config(BaseModel):
    commits: int = 500
    targets: list[str] = ["agents_md", "claude_md", "cursor"]
    min_cluster_size: int = 3


def load_config(repo: Path) -> Config:
    path = repo / ".rulesmith" / "config.toml"
    if not path.is_file():
        return Config()
    return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
