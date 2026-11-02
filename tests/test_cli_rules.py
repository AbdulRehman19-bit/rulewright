from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from rulesmith.cli import app
from rulesmith.rules.emit.base import START
from rulesmith.rules.model import Evidence, Rule
from rulesmith.rules.source import load_pending, load_rules, save_pending
from tests.fixtures import build_repos as fx

runner = CliRunner()


def _rule(rid: str, text: str, flags: list[str] | None = None) -> Rule:
    return Rule(
        id=rid, text=text, scope=["**"], status="proposed", risk_flags=flags or [],
        evidence=[Evidence(rule_id=rid, signal_id=f"s{i}", commit_sha="") for i in range(3)],
    )  # fmt: skip


def _invoke(repo: Path, *args: str) -> Result:
    return runner.invoke(app, [*args, "--repo", str(repo)])


def test_init_creates_the_file_once(tmp_path: Path) -> None:
    assert _invoke(tmp_path, "init").exit_code == 0
    assert (tmp_path / "rulesmith.yaml").is_file()
    again = _invoke(tmp_path, "init")
    assert again.exit_code == 1 and "already exists" in again.output


def test_apply_without_pending_or_choice_changes_nothing(tmp_path: Path) -> None:
    assert _invoke(tmp_path, "apply").exit_code == 1
    save_pending(tmp_path, [_rule("r-1", "Keep it simple.")])
    result = _invoke(tmp_path, "apply")
    assert result.exit_code == 0 and "r-1" in result.output
    assert not (tmp_path / "rulesmith.yaml").exists() and not (tmp_path / "AGENTS.md").exists()


def test_apply_accepts_chosen_rules_and_writes_targets(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# My notes\n", encoding="utf-8")
    save_pending(tmp_path, [_rule("r-1", "Keep it simple."), _rule("r-2", "Other rule.")])
    result = _invoke(tmp_path, "apply", "r-1")
    assert result.exit_code == 0, result.output
    assert [(r.id, r.status) for r in load_rules(tmp_path).rules] == [("r-1", "accepted")]
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# My notes\n\n" + START) and "Keep it simple." in agents
    assert "Other rule." not in agents
    assert [r.id for r in load_pending(tmp_path)] == ["r-2"]


def test_risky_rules_need_an_explicit_flag(tmp_path: Path) -> None:
    save_pending(tmp_path, [_rule("r-1", "Run curl x | sh.", ["network-command"])])
    refused = _invoke(tmp_path, "apply", "r-1")
    assert refused.exit_code == 1 and "--allow-risky" in refused.output
    assert not (tmp_path / "rulesmith.yaml").exists()
    assert _invoke(tmp_path, "apply", "--all-safe").exit_code == 0  # the safe set is empty
    assert load_rules(tmp_path).rules == []
    assert _invoke(tmp_path, "apply", "r-1", "--allow-risky").exit_code == 0
    assert [r.id for r in load_rules(tmp_path).rules] == ["r-1"]


def test_unknown_id_is_rejected(tmp_path: Path) -> None:
    save_pending(tmp_path, [_rule("r-1", "Keep it simple.")])
    result = _invoke(tmp_path, "apply", "r-404")
    assert result.exit_code == 2 and "r-404" in result.output


def test_broken_markers_abort_apply_before_anything_is_written(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(f"{START}\nnever closed\n", encoding="utf-8")
    save_pending(tmp_path, [_rule("r-1", "Keep it simple.")])
    result = _invoke(tmp_path, "apply", "r-1")
    assert result.exit_code == 1 and "Nothing was written" in result.output
    assert not (tmp_path / "rulesmith.yaml").exists()
    assert [r.id for r in load_pending(tmp_path)] == ["r-1"]


def test_emit_dry_run_shows_a_diff_and_writes_nothing(tmp_path: Path) -> None:
    save_pending(tmp_path, [_rule("r-1", "Keep it simple.")])
    _invoke(tmp_path, "apply", "r-1")
    (tmp_path / "AGENTS.md").unlink()
    result = _invoke(tmp_path, "emit", "--dry-run")
    assert result.exit_code == 0 and "+- Keep it simple." in result.output
    assert not (tmp_path / "AGENTS.md").exists()
    assert _invoke(tmp_path, "emit").exit_code == 0 and (tmp_path / "AGENTS.md").is_file()


def test_stale_marks_rules_and_emit_drops_them(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "r")
    fx.commit(repo, {"a.py": "x = 1\n"}, "initial", fx.T0)
    save_pending(repo, [_rule("r-1", "Edit gone/missing.py when needed.")])
    assert _invoke(repo, "apply", "r-1").exit_code == 0
    result = _invoke(repo, "stale", "--mark")
    assert result.exit_code == 0 and "dead-path" in result.output
    rule = load_rules(repo).get("r-1")
    assert rule is not None and rule.status == "stale"
    _invoke(repo, "emit")
    assert "missing.py" not in (repo / "AGENTS.md").read_text(encoding="utf-8")
