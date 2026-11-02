import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

import rulewright.cli as cli
from rulewright.report import REPORT_NAME
from rulewright.rules.source import load_pending
from tests.fixtures import build_repos as fx
from tests.helpers import CitingLLM, FakeEmbedder

runner = CliRunner()


@pytest.fixture(scope="module")
def recent_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three mistake patterns dated relative to today, so recency weighting never expires."""
    path = tmp_path_factory.mktemp("scan") / "r"
    fx.build_three_patterns(path, start=datetime.now(UTC) - timedelta(days=40))
    return path


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[CitingLLM]:
    client = CitingLLM()
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(cli, "_make_client", lambda model: client)
    yield client


def _scan(repo: Path, *args: str, user_input: str | None = None) -> Result:
    return runner.invoke(cli.app, ["scan", "--repo", str(repo), *args], input=user_input)


def _git_status(repo: Path) -> str:
    """Porcelain status ignoring the files scan is allowed to write."""
    excluded = [":!.rulewright", f":!{REPORT_NAME}"]
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", ".", *excluded],
        capture_output=True,
        text=True,
    ).stdout


def test_scan_prints_rules_with_evidence_and_changes_no_agent_file(
    recent_repo: Path, llm: CitingLLM
) -> None:
    result = _scan(recent_repo)
    assert result.exit_code == 0, result.output
    assert "Import settings" in result.output
    assert "Nothing has been applied" in result.output
    assert not (recent_repo / "AGENTS.md").exists()
    assert not (recent_repo / "rulewright.yaml").exists()
    assert _git_status(recent_repo) == ""  # the working tree is untouched


def test_scan_writes_report_and_pending_proposals(recent_repo: Path, llm: CitingLLM) -> None:
    assert _scan(recent_repo).exit_code == 0
    report = (recent_repo / REPORT_NAME).read_text(encoding="utf-8")
    assert "# rulewright report" in report and "## Proposed rules" in report
    assert "Nothing here has been applied" in report and "refix:" in report
    pending = load_pending(recent_repo)
    assert len(pending) >= 3 and all(r.status == "proposed" for r in pending)


def test_paid_model_asks_first_and_no_call_is_made_on_no(
    recent_repo: Path, llm: CitingLLM
) -> None:
    llm.model = "anthropic/paid"
    declined = _scan(recent_repo, user_input="n\n")
    assert "LLM: anthropic/paid" in declined.output
    assert "Proceed with the paid call?" in declined.output
    assert llm.prompts == [] and "Cancelled" in declined.output
    accepted = _scan(recent_repo, user_input="y\n")
    assert llm.prompts and "Nothing has been applied" in accepted.output


def test_yes_skips_the_question(recent_repo: Path, llm: CitingLLM) -> None:
    llm.model = "anthropic/paid"
    result = _scan(recent_repo, "--yes")
    assert "Proceed with the paid call?" not in result.output and llm.prompts


def test_local_model_is_free_and_not_confirmed(recent_repo: Path, llm: CitingLLM) -> None:
    result = _scan(recent_repo)
    assert "about $0.0000" in result.output and "Proceed" not in result.output


def test_cold_start_is_plain_and_exits_cleanly(tmp_path: Path, llm: CitingLLM) -> None:
    fx.build_repeated_mistake(tmp_path / "r", start=datetime.now(UTC) - timedelta(days=10))
    result = _scan(tmp_path / "r")
    assert result.exit_code == 0 and "--commits 2000" in result.output
    assert llm.prompts == [] and (tmp_path / "r" / REPORT_NAME).is_file()


def test_no_llm_still_reports_clusters(
    recent_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(cli, "_make_client", lambda model: None)
    result = _scan(recent_repo)
    assert result.exit_code == 0 and "No LLM available" in result.output
