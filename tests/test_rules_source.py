from pathlib import Path

import pytest

from rulesmith.rules.model import Evidence, Rule
from rulesmith.rules.source import (
    FILENAME,
    RulesFile,
    RulesFileError,
    load_pending,
    load_rules,
    save_pending,
    save_rules,
)


def _rule(rid: str = "r-1", status: str = "accepted") -> Rule:
    return Rule(
        id=rid,
        text='Use "app.config": never app.settings #1',
        scope=["app/**"],
        status=status,  # type: ignore[arg-type]
        risk_flags=["url"],
        evidence=[
            Evidence(rule_id=rid, signal_id="s1", commit_sha="a1b2c3d4e5f6"),
            Evidence(rule_id=rid, signal_id="correction-u0000002", commit_sha=""),
        ],
    )


def test_missing_file_gives_an_empty_default(tmp_path: Path) -> None:
    rf = load_rules(tmp_path)
    assert rf.rules == [] and rf.targets == ["agents_md", "claude_md", "cursor"]


def test_round_trip_keeps_what_matters(tmp_path: Path) -> None:
    save_rules(tmp_path, RulesFile(rules=[_rule()]))
    (rule,) = load_rules(tmp_path).rules
    assert rule.text == _rule().text  # quotes, colon and hash survive YAML
    assert (rule.scope, rule.status, rule.risk_flags) == (["app/**"], "accepted", ["url"])
    assert [(e.commit_sha, e.signal_id) for e in rule.evidence] == [
        ("a1b2c3d4e5f6", ""),
        ("", "correction-u0000002"),
    ]


def test_file_matches_the_spec_shape(tmp_path: Path) -> None:
    save_rules(tmp_path, RulesFile(rules=[_rule()]))
    text = (tmp_path / FILENAME).read_text(encoding="utf-8")
    assert text.startswith("version: 1\ntargets:")
    assert "  - id: r-1\n" in text
    assert "    evidence:\n      - a1b2c3d4e5f6\n      - correction-u0000002\n" in text


def test_add_replaces_by_id_and_sets_status() -> None:
    rf = RulesFile(rules=[_rule("r-1", "proposed")])
    rf.add(_rule("r-1"), "stale")
    assert [(r.id, r.status) for r in rf.rules] == [("r-1", "stale")]
    assert rf.accepted() == []


@pytest.mark.parametrize("content", ["version: 2\n", "rules: [{text: no id}]\n", "{{ not yaml"])
def test_bad_files_raise_a_clear_error(tmp_path: Path, content: str) -> None:
    (tmp_path / FILENAME).write_text(content, encoding="utf-8")
    with pytest.raises(RulesFileError):
        load_rules(tmp_path)


def test_pending_round_trip(tmp_path: Path) -> None:
    assert load_pending(tmp_path) == []
    save_pending(tmp_path, [_rule("r-9", "proposed")])
    assert [r.id for r in load_pending(tmp_path)] == ["r-9"]
