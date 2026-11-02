import json
from collections.abc import Callable
from pathlib import Path

from rulesmith.ingest.git_history import read_commits
from rulesmith.mine.detectors import dedupe_signals, detect_refixes, detect_reverts
from rulesmith.prove.agent import BuiltinAgent
from rulesmith.prove.runner import estimate_runs, prove_rule
from rulesmith.prove.tasks import Task, build_tasks
from rulesmith.rules.model import Evidence, Rule
from tests.fixtures import build_repos as fx
from tests.helpers import LocalSandbox, ScriptedChat, action

BAD = "from app.settings import X"
GOOD = "from app.config import X"


def _setup(tmp_path: Path) -> tuple[Path, Rule, list[Task]]:
    repo = tmp_path / "r"
    shas = fx.build_refix(repo)
    rule = Rule(
        id="r-1", text="Import settings from app.config.", scope=["app/**"], status="accepted",
        evidence=[Evidence(rule_id="r-1", signal_id="s0", commit_sha=shas["agent"])],
    )  # fmt: skip
    history = read_commits(repo, 500)
    signals = dedupe_signals(detect_reverts(history) + detect_refixes(history))
    return repo, rule, build_tasks(rule, history, signals)


def _arm_aware_replies() -> Callable[[], list[object]]:
    """An agent that writes BAD without the rule and GOOD when the rule is in its prompt."""

    def edit(system: str, messages: list[dict[str, str]]) -> str:
        text = messages[-1]["content"]
        has_rule = "Project rules" in system
        line = GOOD if has_rule else BAD
        return action(action="write_file", path="app/mod.py", content=text.replace("line10", line))

    return lambda: [action(action="read_file", path="app/mod.py"), edit, action(action="finish")]


class Factory:
    def __init__(self) -> None:
        self.sandboxes: list[LocalSandbox] = []

    def __call__(self, work: Path) -> LocalSandbox:
        sandbox = LocalSandbox(work)
        self.sandboxes.append(sandbox)
        return sandbox


class RepeatingChat(ScriptedChat):
    """Starts each run's script over, so one model object can serve many runs."""

    def __init__(self, script: Callable[[], list[object]]) -> None:
        super().__init__([])
        self.script = script

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> str:
        if len(messages) == 1:  # a new run starts with only the task message
            self.replies = self.script()
        return super().chat(system, messages, max_tokens)


def test_the_rule_is_proved_when_it_prevents_the_mistake(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    factory = Factory()
    agent = BuiltinAgent(RepeatingChat(_arm_aware_replies()))
    report = prove_rule(rule, tasks, repo, agent, factory)
    assert (report.recurrences("without"), len(report.judged("without"))) == (1, 1)
    assert (report.recurrences("with"), len(report.judged("with"))) == (0, 1)
    assert report.rate("without") == 1.0 and report.rate("with") == 0.0
    assert report.changed("without") == 1 and report.changed("with") == 1
    assert len(factory.sandboxes) == 2 and all(s.closed for s in factory.sandboxes)
    assert report.cost_usd == 0.0 and report.aborted is None


def test_a_useless_rule_shows_no_difference(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    # an agent that ignores the rule: always writes the mistake
    always_bad = [action(action="read_file", path="app/mod.py"),
                  lambda s, m: action(action="write_file", path="app/mod.py",
                                      content=m[-1]["content"].replace("line10", BAD)),
                  action(action="finish")]  # fmt: skip
    agent = BuiltinAgent(RepeatingChat(lambda: list(always_bad)))
    report = prove_rule(rule, tasks, repo, agent, Factory())
    assert report.rate("without") == report.rate("with") == 1.0


def test_each_run_starts_from_the_old_tree_and_leaves_no_trace(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    factory = Factory()
    prove_rule(rule, tasks, repo, BuiltinAgent(RepeatingChat(_arm_aware_replies())), factory)
    assert all(not s.root.exists() for s in factory.sandboxes)  # temp copies are removed
    assert (repo / "app" / "mod.py").read_text(encoding="utf-8").count("app.config") == 1


def test_trials_repeat_every_task_in_both_arms(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    report = prove_rule(
        rule, tasks, repo, BuiltinAgent(RepeatingChat(_arm_aware_replies())), Factory(), trials=3
    )
    assert report.trials == 3 and len(report.records) == 6
    assert [r.trial for r in report.records] == [1, 1, 2, 2, 3, 3]


def test_failed_runs_are_not_counted_in_the_rates(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)

    def boom(system: str, messages: list[dict[str, str]]) -> str:
        raise TimeoutError("model down")

    report = prove_rule(rule, tasks, repo, BuiltinAgent(ScriptedChat([boom, boom])), Factory())
    assert all(r.error for r in report.records)
    assert report.rate("without") is None and report.rate("with") is None


def test_max_cost_stops_the_run_with_partial_results(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    agent = BuiltinAgent(RepeatingChat(_arm_aware_replies()))
    agent.model = "paid/model"  # not a free local model
    report = prove_rule(
        rule, tasks, repo, agent, Factory(), trials=2, max_cost=0.01, pricer=lambda m, i, o: 0.02
    )
    assert len(report.records) == 1  # the first run exceeded the ceiling, so nothing else ran
    assert report.aborted and "--max-cost" in report.aborted


def test_tasks_without_known_bad_lines_fall_back_to_an_llm_judge(tmp_path: Path) -> None:
    repo, rule, tasks = _setup(tmp_path)
    blind = [t.model_copy(update={"bad_lines": []}) for t in tasks]
    judge = ScriptedChat([])
    judge.complete = lambda system, user: json.dumps({"violated": "Project" not in user})  # type: ignore[attr-defined]
    agent = BuiltinAgent(RepeatingChat(_arm_aware_replies()))
    report = prove_rule(rule, blind, repo, agent, Factory(), judge_client=judge)  # type: ignore[arg-type]
    assert report.rate("without") == 1.0 and report.rate("with") == 1.0  # judge said "violated"


def test_estimate_is_free_for_local_models_and_priced_otherwise() -> None:
    tin, tout, usd = estimate_runs("ollama/qwen", 20)
    assert usd == 0.0 and tin > tout > 0
    assert estimate_runs("x/y", 20, pricer=lambda m, i, o: i / 1e6)[2] == tin / 1e6
    assert estimate_runs("x/y", 20, pricer=lambda m, i, o: None)[2] is None
