from pathlib import Path

import pytest

from rulesmith.prove.agent import MAX_STEPS, BuiltinAgent, parse_action, system_prompt
from rulesmith.prove.sandbox import SandboxError, safe_relpath
from tests.helpers import LocalSandbox, ScriptedChat, action


def _agent(replies: list[object], **kw: int) -> tuple[BuiltinAgent, ScriptedChat]:
    chat = ScriptedChat(replies)
    return BuiltinAgent(chat, **kw), chat


def test_write_then_finish(tmp_path: Path) -> None:
    write = action(action="write_file", path="a/b.txt", content="hi")
    agent, chat = _agent([write, action(action="finish")])
    sandbox = LocalSandbox(tmp_path)
    result = agent.run("make a file", [], sandbox)
    assert result.finished and result.steps == 2 and result.error == ""
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hi"
    assert chat.calls[1][1][-1]["content"] == "wrote a/b.txt"  # the result goes back to the model
    assert result.tokens_in > 0 and result.tokens_out > 0


def test_read_and_run_results_are_fed_back(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("contents", encoding="utf-8")
    agent, chat = _agent([
        action(action="read_file", path="f.txt"),
        action(action="run", command="echo done"),
        action(action="finish"),
    ])  # fmt: skip
    assert agent.run("t", [], LocalSandbox(tmp_path)).finished
    assert chat.calls[1][1][-1]["content"] == "contents"
    assert chat.calls[2][1][-1]["content"].startswith("exit code 0")


def test_bad_replies_and_bad_paths_are_reported_to_the_model_not_raised(tmp_path: Path) -> None:
    agent, chat = _agent([
        "I will now edit the file",
        action(action="read_file", path="../../etc/passwd"),
        action(action="write_file", path="/abs.txt", content="x"),
        action(action="teleport"),
        action(action="finish"),
    ])  # fmt: skip
    result = agent.run("t", [], LocalSandbox(tmp_path))
    assert result.finished and result.steps == 5
    seen = [call[1][-1]["content"] for call in chat.calls[1:]]
    assert "one JSON object" in seen[0]
    assert seen[1].startswith("error:") and seen[2].startswith("error:")
    assert "unknown or incomplete" in seen[3]
    assert not (tmp_path.parent / "etc").exists()


def test_the_step_limit_ends_the_run(tmp_path: Path) -> None:
    agent, _ = _agent([action(action="run", command="echo x")] * 50, max_steps=4)
    result = agent.run("t", [], LocalSandbox(tmp_path))
    assert not result.finished and result.steps == 4 and "step limit" in result.error
    assert MAX_STEPS >= 8


def test_a_model_failure_ends_only_this_run(tmp_path: Path) -> None:
    def boom(system: str, messages: list[dict[str, str]]) -> str:
        raise TimeoutError("timed out")

    result, _ = _agent([boom])[0].run("t", [], LocalSandbox(tmp_path)), None
    assert not result.finished and "model call failed" in result.error


def test_rules_reach_the_system_prompt_only_when_given() -> None:
    assert "Project rules" not in system_prompt([])
    prompt = system_prompt(["Import settings from app.config."])
    assert "Project rules" in prompt and "- Import settings from app.config." in prompt


def test_action_parsing_is_strict_about_shape() -> None:
    assert parse_action('```json\n{"action": "finish"}\n```') == {"action": "finish"}
    assert parse_action('{"action": "run", "command": "ls", "n": 3}') == {
        "action": "run", "command": "ls",
    }  # fmt: skip
    for bad in ["", "nothing", '{"action": 3}', "[1]", '{"command": "ls"}']:
        assert parse_action(bad) is None


def test_safe_paths() -> None:
    assert safe_relpath("a/./b.py") == "a/b.py" and safe_relpath("a\\b.py") == "a/b.py"
    for bad in ["", ".", "..", "../x", "a/../../x", "/etc/passwd", "a\x00b"]:
        with pytest.raises(SandboxError):
            safe_relpath(bad)
