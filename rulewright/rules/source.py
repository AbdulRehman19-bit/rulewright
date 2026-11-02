"""rulesmith.yaml: the single source of truth, committed to the repo.

Also the pending store (.rulesmith/pending.json) where `scan` leaves proposals until the user
approves them with `apply`.
"""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, TypeAdapter

from rulesmith.rules.model import Evidence, Rule, RuleStatus

FILENAME = "rulesmith.yaml"
PENDING = Path(".rulesmith") / "pending.json"
DEFAULT_TARGETS = ["agents_md", "claude_md", "cursor"]
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under their key, like the spec's example file."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


class RulesFileError(Exception):
    """rulesmith.yaml is unreadable or not in the expected shape."""


class RulesFile(BaseModel):
    version: int = 1
    targets: list[str] = list(DEFAULT_TARGETS)
    rules: list[Rule] = []

    def accepted(self) -> list[Rule]:
        return [r for r in self.rules if r.status == "accepted"]

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def add(self, rule: Rule, status: RuleStatus = "accepted") -> None:
        """Add or replace a rule by id."""
        rule = rule.model_copy(update={"status": status})
        self.rules = [r for r in self.rules if r.id != rule.id] + [rule]


def _evidence_ref(e: Evidence) -> str:
    return e.commit_sha or e.signal_id


def _rule_to_dict(rule: Rule) -> dict[str, object]:
    out: dict[str, object] = {
        "id": rule.id,
        "text": rule.text,
        "scope": rule.scope,
        "status": rule.status,
        "evidence": [_evidence_ref(e) for e in rule.evidence],
    }
    if rule.risk_flags:
        out["risk_flags"] = rule.risk_flags
    return out


def _rule_from_dict(raw: dict[str, object]) -> Rule:
    rule_id = str(raw["id"])
    evidence = []
    for ref in raw.get("evidence", []):  # type: ignore[attr-defined]
        ref = str(ref)
        is_sha = bool(_SHA.fullmatch(ref))
        evidence.append(
            Evidence(
                rule_id=rule_id,
                signal_id="" if is_sha else ref,
                commit_sha=ref if is_sha else "",
            )
        )
    return Rule.model_validate({**raw, "evidence": evidence})


def load_rules(repo: Path) -> RulesFile:
    """The rules file, or an empty default when the repo has none yet."""
    path = repo / FILENAME
    if not path.is_file():
        return RulesFile()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw.get("version", 1) != 1:
            raise RulesFileError(f"{FILENAME}: unsupported version {raw['version']}")
        return RulesFile(
            version=1,
            targets=raw.get("targets", list(DEFAULT_TARGETS)),
            rules=[_rule_from_dict(r) for r in raw.get("rules", [])],
        )
    except RulesFileError:
        raise
    except Exception as e:  # yaml, pydantic or key errors: all mean the file is unusable
        raise RulesFileError(f"{FILENAME}: {e}") from e


def save_rules(repo: Path, rules_file: RulesFile) -> Path:
    data = {
        "version": rules_file.version,
        "targets": rules_file.targets,
        "rules": [_rule_to_dict(r) for r in rules_file.rules],
    }
    path = repo / FILENAME
    text = yaml.dump(data, Dumper=_IndentedDumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(text, encoding="utf-8")
    return path


_PENDING_LIST = TypeAdapter(list[Rule])


def load_pending(repo: Path) -> list[Rule]:
    path = repo / PENDING
    if not path.is_file():
        return []
    return _PENDING_LIST.validate_json(path.read_text(encoding="utf-8"))


def save_pending(repo: Path, rules: list[Rule]) -> None:
    path = repo / PENDING
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(_PENDING_LIST.dump_json(rules, indent=2))
