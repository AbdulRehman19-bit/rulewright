import subprocess
from pathlib import Path

import pytest

from rulewright.ingest.git_history import read_commits
from rulewright.mine.detectors import dedupe_signals, detect_refixes, detect_reverts
from rulewright.prove import judge
from rulewright.prove.tasks import build_tasks, materialize, task_prompt
from rulewright.rules.model import Evidence, Rule
from tests.fixtures import build_repos as fx


def _rule(*shas: str) -> Rule:
    ev = [Evidence(rule_id="r-1", signal_id=f"s{i}", commit_sha=s) for i, s in enumerate(shas)]
    return Rule(id="r-1", text="Import settings from app.config.", scope=["app/**"], evidence=ev)


def _tasks(repo: Path, rule: Rule):  # type: ignore[no-untyped-def]
    history = read_commits(repo, 500)
    signals = dedupe_signals(detect_reverts(history) + detect_refixes(history))
    return build_tasks(rule, history, signals)


def test_task_is_rebuilt_from_the_evidence_commit(tmp_path: Path) -> None:
    shas = fx.build_refix(tmp_path / "r")
    (task,) = _tasks(tmp_path / "r", _rule(shas["agent"]))
    assert task.commit_sha == shas["agent"] and task.prompt == "agent import"
    assert task.bad_lines == ["from app.settings import X"]
    assert task.parent_sha != shas["agent"] and task.files == ["app/mod.py"]


def test_evidence_outside_the_history_and_duplicates_are_skipped(tmp_path: Path) -> None:
    shas = fx.build_refix(tmp_path / "r")
    rule = _rule(shas["agent"], shas["agent"], "f" * 40)
    assert [t.commit_sha for t in _tasks(tmp_path / "r", rule)] == [shas["agent"]]


def test_at_most_the_limit_newest_first(tmp_path: Path) -> None:
    shas = fx.build_repeated_mistake(tmp_path / "r", repeats=5)
    repo = tmp_path / "r"
    history = read_commits(repo, 500)
    tasks = build_tasks(_rule(*shas), history, [], limit=3)
    assert [t.commit_sha for t in tasks] == shas[::-1][:3]
    assert all(t.bad_lines == [] for t in tasks)  # no signals given: only an LLM can judge


def test_the_prompt_is_the_message_without_attribution() -> None:
    from datetime import UTC, datetime

    from rulewright.ingest.git_history import Commit

    commit = Commit(
        sha="a" * 40, parents=["b" * 40], author_name="x", author_email="x@x", ts=datetime.now(UTC),
        subject="Add settings import", is_agent=True, files=[],
        body="Use the new helper.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
        "🤖 Generated with Claude Code",
    )  # fmt: skip
    assert task_prompt(commit) == "Add settings import\n\nUse the new helper."


def test_materialize_gives_the_old_tree_and_none_of_the_future(tmp_path: Path) -> None:
    shas = fx.build_refix(tmp_path / "r")
    (task,) = _tasks(tmp_path / "r", _rule(shas["agent"]))
    work = tmp_path / "work"
    materialize(tmp_path / "r", task.parent_sha, work)
    text = (work / "app" / "mod.py").read_text(encoding="utf-8")
    assert "line10" in text and "app.settings" not in text and "app.config" not in text
    assert not (work / ".git").exists()


def test_materialize_extracts_only_inside_the_destination(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "r")
    fx.commit(repo, {"a.txt": "a\n"}, "base", fx.T0)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    work = tmp_path / "work"
    materialize(repo, sha, work)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r", "work"]


# --- judge --------------------------------------------------------------------------------


def test_recurrence_is_the_agent_writing_a_line_the_human_removed() -> None:
    before = {"m.py": "a = 1\nb = 2\n"}
    bad = ["from app.settings import X"]
    wrote_bad = {"m.py": "a = 1\nb = 2\n  from app.settings import X\n"}
    wrote_good = {"m.py": "a = 1\nb = 2\nfrom app.config import X\n"}
    assert judge.recurred(before, wrote_bad, bad) is True  # whitespace does not hide it
    assert judge.recurred(before, wrote_good, bad) is False
    assert judge.recurred(before, before, bad) is False
    assert judge.recurred(before, wrote_bad, []) is None  # nothing to look for
    assert judge.recurred(before, wrote_bad, ["x"]) is None  # too short to identify anything


def test_a_line_already_in_the_old_tree_is_not_a_recurrence() -> None:
    before = {"m.py": "from app.settings import X\n"}
    assert judge.recurred(before, dict(before), ["from app.settings import X"]) is False


def test_snapshot_skips_symlinks_git_binary_and_huge_files(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    (tmp_path / "big.txt").write_text("x" * (judge.MAX_FILE_BYTES + 1), encoding="utf-8")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("host secret", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    assert judge.snapshot(tmp_path) == {"ok.txt": "hello"}
