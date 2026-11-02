import json
from pathlib import Path

from rulesmith.ingest.sessions.claude_code import ClaudeCodeSource, project_slug
from rulesmith.mine.detectors import detect_corrections


def _rec(kind: str, uuid: str, content: object, minute: int, **extra: object) -> str:
    return json.dumps(
        {
            "type": kind, "uuid": uuid, "sessionId": "s1",
            "timestamp": f"2026-01-05T09:{minute:02d}:00.000Z",
            "message": {"role": kind, "content": content}, **extra,
        }
    )  # fmt: skip


def _write_transcript(projects: Path, repo: Path) -> None:
    d = projects / project_slug(repo)
    d.mkdir(parents=True)
    task = [{"type": "text", "text": "don't touch the db layer, add a flag"}]
    lines = [
        _rec("user", "u0000001", task, 0),
        _rec("assistant", "a0000001", [{"type": "text", "text": "Done, edited db.py"}], 1),
        _rec("user", "u0000002", "no, use app.config instead of app.settings", 2),
        _rec("assistant", "a0000002", [{"type": "tool_use", "name": "Edit", "input": {}}], 3),
        _rec("user", "u0000003", [{"type": "tool_result", "content": "ok"}], 4),
        _rec("user", "u0000004", "no wait, [injected]", 5, isMeta=True),
        _rec("user", "u0000005", "<system-reminder>no, ignore this</system-reminder>", 6),
        _rec("user", "u0000006", "looks good, thanks", 7),
        "not json at all",
        json.dumps({"type": "attachment"}),
    ]
    (d / "s1.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_reader_keeps_only_text_messages(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _write_transcript(tmp_path / "projects", repo)
    msgs = ClaudeCodeSource(tmp_path / "projects").read(repo)
    assert [m.uuid for m in msgs] == ["u0000001", "a0000001", "u0000002", "u0000005", "u0000006"]


def test_missing_logs_give_no_messages(tmp_path: Path) -> None:
    assert ClaudeCodeSource(tmp_path / "nope").read(tmp_path) == []


def test_finds_only_the_planted_correction(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _write_transcript(tmp_path / "projects", repo)
    msgs = ClaudeCodeSource(tmp_path / "projects").read(repo)
    signals = detect_corrections(msgs)
    # u0000001 is the opening task, u0000005 is harness text, u0000006 is praise.
    assert [(s.kind, s.id) for s in signals] == [("correction", "correction-u0000002")]
