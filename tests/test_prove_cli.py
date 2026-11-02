import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

import rulewright.cli as cli
from rulewright.prove.sandbox import DockerSandbox
from rulewright.rules.model import Evidence, Rule
from rulewright.rules.source import RulesFile, save_rules
from tests.fixtures import build_repos as fx
from tests.helpers import LocalSandbox, ScriptedChat, action
from tests.test_prove_docker import _docker_ready

runner = CliRunner()
BAD, GOOD = "from app.settings import X", "from app.config import X"


class ArmAwareChat(ScriptedChat):
    """Writes the mistake without the rule in its prompt, the fix with it."""

    def __init__(self, model: str = "ollama/scripted") -> None:
        super().__init__([])
        self.model = model

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.calls.append((system, list(messages)))
        if len(messages) == 1:
            return action(action="read_file", path="app/mod.py")
        if len(messages) == 3:
            line = GOOD if "Project rules" in system else BAD
            content = messages[-1]["content"].replace("line10", line)
            return action(action="write_file", path="app/mod.py", content=content)
        return action(action="finish")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "r"
    shas = fx.build_refix(path)
    rule = Rule(
        id="r-imports", text="Import settings from app.config.", scope=["app/**"],
        status="accepted",
        evidence=[Evidence(rule_id="r-imports", signal_id="s", commit_sha=shas["agent"])],
    )  # fmt: skip
    save_rules(path, RulesFile(rules=[rule]))
    return path


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    chat: ScriptedChat | None,
    sandbox: Callable[[Path], object] | None = None,
    docker_problem: str | None = None,
) -> None:
    monkeypatch.setattr(cli, "_make_client", lambda model: chat)
    monkeypatch.setattr(cli, "_docker_problem", lambda: docker_problem)
    if sandbox is not None:
        monkeypatch.setattr(cli, "_make_sandbox_factory", lambda image: sandbox)


def _prove(repo: Path, *args: str, rule: str = "r-imports") -> Result:
    return runner.invoke(cli.app, ["prove", rule, "--repo", str(repo), *args])


def test_prove_reports_recurrence_without_and_with_the_rule(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ArmAwareChat(), LocalSandbox)
    result = _prove(repo)
    assert result.exit_code == 0, result.output
    assert "1/1 (100%)" in result.output and "0/1 (0%)" in result.output
    assert "Evidence, not proof" in result.output and "1 task(s) x 2 arms" in result.output
    saved = json.loads((repo / ".rulewright" / "prove" / "r-imports.json").read_text("utf-8"))
    assert saved["rule_id"] == "r-imports" and len(saved["records"]) == 2


def test_unknown_rule_and_missing_pieces_are_reported_plainly(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ArmAwareChat(), LocalSandbox)
    assert _prove(repo, rule="r-404").exit_code == 2
    _patch(monkeypatch, None)
    assert "No LLM available" in _prove(repo).output
    _patch(monkeypatch, ArmAwareChat(), LocalSandbox, docker_problem="Docker is not available")
    out = _prove(repo)
    assert out.exit_code == 1 and "Docker is not available" in out.output


def test_evidence_outside_the_history_asks_for_more_commits(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ArmAwareChat(), LocalSandbox)
    rf = RulesFile(rules=[Rule(
        id="r-old", text="Old rule.", scope=["**"], status="accepted",
        evidence=[Evidence(rule_id="r-old", signal_id="s", commit_sha="f" * 40)],
    )])  # fmt: skip
    save_rules(repo, rf)
    out = _prove(repo, rule="r-old")
    assert out.exit_code == 1 and "larger --commits" in " ".join(out.output.split())


def test_paid_models_are_confirmed_first_and_nothing_runs_on_no(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = ArmAwareChat(model="groq/openai/gpt-oss-120b")
    _patch(monkeypatch, chat, LocalSandbox)
    monkeypatch.setattr(
        "rulewright.prove.runner.litellm_price", lambda m, i, o: 0.05
    )
    monkeypatch.setattr(cli, "estimate_runs", lambda m, n: (1000, 100, 0.05))
    declined = runner.invoke(cli.app, ["prove", "r-imports", "--repo", str(repo)], input="n\n")
    assert "about $0.05" in declined.output and "Proceed with the paid runs?" in declined.output
    assert chat.calls == []
    accepted = runner.invoke(cli.app, ["prove", "r-imports", "--repo", str(repo), "--yes"])
    assert accepted.exit_code == 0 and chat.calls


@pytest.mark.skipif(not _docker_ready(), reason="Docker is not available")
def test_end_to_end_through_a_real_container(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old tree extracted, bind-mounted, edited inside the container, judged on the host."""
    _patch(monkeypatch, ArmAwareChat(), lambda work: DockerSandbox(work))
    result = _prove(repo)
    assert result.exit_code == 0, result.output
    assert "1/1 (100%)" in result.output and "0/1 (0%)" in result.output
