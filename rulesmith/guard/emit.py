"""Add allow rules to a Claude Code settings file without disturbing anything else in it."""

import difflib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from rulesmith.guard.profile import Pattern


class SettingsError(Exception):
    """The settings file is not something we can safely edit."""


def settings_path(repo: Path, local: bool = False) -> Path:
    name = "settings.local.json" if local else "settings.json"
    return repo / ".claude" / name


def rule_for(pattern: Pattern) -> str:
    return f"Bash({pattern.pattern}:*)"


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        msg = f"{path} is not valid JSON ({e}); fix it first, nothing was changed."
        raise SettingsError(msg) from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} does not hold a JSON object; nothing was changed.")
    perms = data.get("permissions", {})
    allow = perms.get("allow", []) if isinstance(perms, dict) else None
    if not isinstance(allow, list):
        raise SettingsError(f"{path} has an unexpected `permissions.allow`; nothing was changed.")
    return data


def with_rules(settings: dict[str, Any], rules: list[str]) -> tuple[dict[str, Any], list[str]]:
    """(new settings, the rules actually added). Existing rules and other keys are untouched."""
    new = json.loads(json.dumps(settings))
    perms = new.setdefault("permissions", {})
    allow: list[Any] = perms.setdefault("allow", [])
    added = [r for r in dict.fromkeys(rules) if r not in allow]
    allow.extend(added)
    return new, added


def render(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def unified_diff(old: str, new: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{name}", tofile=f"b/{name}",
        )
    )  # fmt: skip


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
