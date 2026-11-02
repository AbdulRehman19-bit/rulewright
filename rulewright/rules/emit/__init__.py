"""Render rulewright.yaml into every target file. Planning never writes; writing is explicit."""

import difflib
from dataclasses import dataclass
from pathlib import Path

from rulewright.rules.emit import agents_md, claude_md, copilot, cursor
from rulewright.rules.emit.base import MarkerError, Target, apply_block
from rulewright.rules.source import RulesFile

TARGETS: dict[str, Target] = {
    t.name: t for t in (agents_md.TARGET, claude_md.TARGET, cursor.TARGET, copilot.TARGET)
}

__all__ = ["TARGETS", "Change", "MarkerError", "plan_emit", "write_changes"]


@dataclass(frozen=True)
class Change:
    target: str
    path: Path
    old: str | None  # None when the file does not exist yet
    new: str

    def diff(self) -> str:
        old = (self.old or "").splitlines(keepends=True)
        new = self.new.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(old, new, f"a/{self.path.name}", f"b/{self.path.name}")
        )


def plan_emit(repo: Path, rules_file: RulesFile) -> list[Change]:
    """Files that would change. Raises MarkerError if any target has broken markers."""
    unknown = [t for t in rules_file.targets if t not in TARGETS]
    if unknown:
        raise ValueError(f"unknown targets {unknown}; known: {sorted(TARGETS)}")
    accepted = rules_file.accepted()
    changes = []
    for name in rules_file.targets:
        target = TARGETS[name]
        path = repo / target.path
        old = path.read_bytes().decode("utf-8") if path.is_file() else None
        if old is None and not accepted:
            continue  # nothing to say and no file to update
        new = apply_block(old, target.render(accepted))
        if new != old:
            changes.append(Change(name, path, old, new))
    return changes


def write_changes(changes: list[Change]) -> None:
    for c in changes:
        c.path.parent.mkdir(parents=True, exist_ok=True)
        c.path.write_bytes(c.new.encode("utf-8"))  # bytes: keep the file's own line endings
