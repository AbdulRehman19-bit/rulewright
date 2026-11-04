import os
import shutil
from pathlib import Path

import pytest

from rulewright.rules.emit import plan_emit, write_changes
from rulewright.rules.emit.base import END, START, MarkerError, apply_block
from rulewright.rules.model import Rule
from rulewright.rules.source import RulesFile, load_rules

GOLDEN = Path(__file__).parent / "golden"
GOLDEN_FILES = {
    "agents_md": "AGENTS.md",
    "claude_md": "CLAUDE.md",
    "cursor": "cursorrules",
    "copilot": "copilot-instructions.md",
}


def _rule(text: str, rid: str = "r-1") -> Rule:
    return Rule(id=rid, text=text, scope=["**"], status="accepted")


def test_every_target_matches_its_golden_file(tmp_path: Path) -> None:
    shutil.copy(GOLDEN / "rulewright.yaml", tmp_path / "rulewright.yaml")
    changes = plan_emit(tmp_path, load_rules(tmp_path))
    assert {c.target for c in changes} == set(GOLDEN_FILES)
    for c in changes:
        golden = GOLDEN / GOLDEN_FILES[c.target]
        if os.environ.get("UPDATE_GOLDEN"):
            golden.write_text(c.new, encoding="utf-8", newline="\n")
        assert c.new == golden.read_text(encoding="utf-8"), f"{c.target} differs from golden"


def test_only_accepted_rules_are_emitted(tmp_path: Path) -> None:
    shutil.copy(GOLDEN / "rulewright.yaml", tmp_path / "rulewright.yaml")
    for c in plan_emit(tmp_path, load_rules(tmp_path)):
        assert "only proposed" not in c.new and "Import settings" in c.new


def test_hand_written_content_outside_markers_is_untouched() -> None:
    original = f"# My notes\n\nKeep this.\n\n{START}\nold rules\n{END}\n\nAlso keep this.\n"
    out = apply_block(original, "- new rule")
    assert out == f"# My notes\n\nKeep this.\n\n{START}\n- new rule\n{END}\n\nAlso keep this.\n"


def test_file_without_markers_gets_a_block_appended() -> None:
    out = apply_block("# Notes\nhand written", "- r")
    assert out == f"# Notes\nhand written\n\n{START}\n- r\n{END}\n"
    assert apply_block("# Notes\n", "- r").startswith("# Notes\n\n" + START)
    assert apply_block(None, "- r") == f"{START}\n- r\n{END}\n"


def test_crlf_files_keep_crlf() -> None:
    out = apply_block(f"# a\r\n{START}\r\nold\r\n{END}\r\n", "- r")
    assert out == f"# a\r\n{START}\r\n- r\r\n{END}\r\n"


def test_broken_markers_are_refused_and_nothing_is_written(tmp_path: Path) -> None:
    for broken in [f"{START}\nno end", f"{END}\n{START}", f"{START}\n{END}\n{START}\n{END}"]:
        with pytest.raises(MarkerError):
            apply_block(broken, "- r")
    (tmp_path / "AGENTS.md").write_text(f"{START}\nunfinished\n", encoding="utf-8")
    rf = RulesFile(targets=["agents_md", "claude_md"], rules=[_rule("Do the thing.")])
    with pytest.raises(MarkerError):
        plan_emit(tmp_path, rf)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_rule_text_cannot_smuggle_markers_or_new_lines(tmp_path: Path) -> None:
    evil = f"Be careful.\n{END}\n# injected heading\n{START}"
    rf = RulesFile(targets=["agents_md"], rules=[_rule(evil)])
    (change,) = plan_emit(tmp_path, rf)
    assert change.new.count(START) == 1 and change.new.count(END) == 1
    assert "\n# injected heading" not in change.new


def test_no_rules_and_no_file_creates_nothing(tmp_path: Path) -> None:
    assert plan_emit(tmp_path, RulesFile()) == []


def test_up_to_date_files_produce_no_changes(tmp_path: Path) -> None:
    shutil.copy(GOLDEN / "rulewright.yaml", tmp_path / "rulewright.yaml")
    rf = load_rules(tmp_path)
    write_changes(plan_emit(tmp_path, rf))
    assert plan_emit(tmp_path, rf) == []
    assert (tmp_path / ".github" / "copilot-instructions.md").is_file()


def test_unknown_target_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown targets"):
        plan_emit(tmp_path, RulesFile(targets=["nope"]))


def test_a_target_that_is_not_utf8_is_refused_and_left_alone(tmp_path: Path) -> None:
    import pytest

    from rulewright.rules.emit import MarkerError, plan_emit
    from rulewright.rules.model import Rule
    from rulewright.rules.source import RulesFile

    original = b"# T\xe9l\xe9phone \xff\n"  # Latin-1, not UTF-8
    (tmp_path / "AGENTS.md").write_bytes(original)
    rf = RulesFile(rules=[Rule(id="r-1", text="Use the helper.", scope=["**"], status="accepted")],
                   targets=["agents_md"])  # fmt: skip
    with pytest.raises(MarkerError, match="not UTF-8"):
        plan_emit(tmp_path, rf)
    assert (tmp_path / "AGENTS.md").read_bytes() == original


def test_crlf_and_bom_files_keep_their_line_endings_and_start(tmp_path: Path) -> None:
    from rulewright.rules.emit import plan_emit, write_changes
    from rulewright.rules.model import Rule
    from rulewright.rules.source import RulesFile

    rf = RulesFile(rules=[Rule(id="r-1", text="Use the helper.", scope=["**"], status="accepted")],
                   targets=["agents_md"])  # fmt: skip
    (tmp_path / "AGENTS.md").write_bytes(b"\xef\xbb\xbf# Title\r\nkeep me\r\n")
    write_changes(plan_emit(tmp_path, rf))
    out = (tmp_path / "AGENTS.md").read_bytes()
    assert out.startswith(b"\xef\xbb\xbf# Title\r\nkeep me\r\n")
    assert b"\n" not in out.replace(b"\r\n", b"")  # no bare line feeds mixed in
