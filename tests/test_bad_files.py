"""Hand-edited and damaged files must give one plain sentence, never a traceback."""

from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

import rulewright.cli as cli

runner = CliRunner()


def _run(repo: Path, *args: str) -> Result:
    return runner.invoke(cli.app, [*args, "--repo", str(repo)])


def _text(result: Result) -> str:
    return " ".join(result.output.split())


def test_invalid_yaml_says_where(tmp_path: Path) -> None:
    (tmp_path / "rulewright.yaml").write_text("rules: [unclosed\n", encoding="utf-8")
    for command in ("emit", "stale"):
        result = _run(tmp_path, command)
        assert result.exit_code == 1 and "is not valid YAML" in _text(result), command
        assert "line 2" in _text(result) and "Traceback" not in result.output


def test_a_rule_with_missing_fields_names_the_rule(tmp_path: Path) -> None:
    (tmp_path / "rulewright.yaml").write_text(
        "rules:\n  - id: r-1\n    bogus_field: 1\n", encoding="utf-8"
    )
    result = _run(tmp_path, "emit")
    assert result.exit_code == 1 and "rule #1" in _text(result)
    assert "pydantic" not in result.output and "errors.pydantic.dev" not in result.output


def test_a_yaml_error_with_brackets_is_printed_literally(tmp_path: Path) -> None:
    """Rich would read [x] in the message as formatting, and [/x] as a markup error."""
    (tmp_path / "rulewright.yaml").write_text("rules: [a, [/b\n", encoding="utf-8")
    result = _run(tmp_path, "emit")
    assert result.exit_code == 1 and "not valid YAML" in _text(result)
    assert "MarkupError" not in result.output


@pytest.mark.parametrize(
    ("content", "expected"),
    [('commits = "many"\n', "commits"), ("[[[ not toml", "not valid TOML")],
)
def test_a_bad_config_file_is_explained(tmp_path: Path, content: str, expected: str) -> None:
    (tmp_path / ".rulewright").mkdir()
    (tmp_path / ".rulewright" / "config.toml").write_text(content, encoding="utf-8")
    result = _run(tmp_path, "scan")
    assert result.exit_code == 1 and expected in _text(result)
    assert "config.toml" in _text(result) and "Traceback" not in result.output


def test_a_damaged_pending_file_says_how_to_rebuild_it(tmp_path: Path) -> None:
    (tmp_path / ".rulewright").mkdir()
    (tmp_path / ".rulewright" / "pending.json").write_text("not json", encoding="utf-8")
    result = _run(tmp_path, "apply", "r-1")
    assert result.exit_code == 1 and "rulewright scan" in _text(result)
    assert "errors.pydantic.dev" not in result.output
