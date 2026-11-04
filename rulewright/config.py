"""Load .rulewright/config.toml. Every key is optional; defaults match the spec."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ValidationError


class Config(BaseModel):
    commits: int = 500
    targets: list[str] = ["agents_md", "claude_md", "cursor"]
    min_cluster_size: int = 3


class ConfigError(Exception):
    """The config file cannot be used; the message says where and why."""


def load_config(repo: Path) -> Config:
    path = repo / ".rulewright" / "config.toml"
    if not path.is_file():
        return Config()
    try:
        return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f".rulewright/config.toml is not valid TOML: {e}") from None
    except ValidationError as e:
        errors = e.errors()
        where = ".".join(str(p) for p in errors[0]["loc"])
        raise ConfigError(f".rulewright/config.toml: {where}: {errors[0]['msg']}") from None
    except (OSError, ValueError) as e:  # unreadable or not UTF-8
        raise ConfigError(f".rulewright/config.toml cannot be read: {e}") from None
