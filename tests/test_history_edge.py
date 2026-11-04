"""Histories a real repository can contain and a tidy fixture never does."""

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from rulewright.ingest.git_history import _header_path, _unquote, read_commits
from rulewright.mine.detectors import detect_refixes
from tests.fixtures import build_repos as fx


def _refix_in(repo: Path, path: str) -> None:
    """An agent line replaced by a human 5 hours later, in a file called `path`."""
    lines = [f"line{i}\n" for i in range(1, 21)]
    fx.commit(repo, {path: "".join(lines)}, "initial", fx.T0)
    lines[9] = "from app.settings import X\n"
    fx.commit(repo, {path: "".join(lines)}, "agent import", fx.T0 + timedelta(hours=1), agent=True)
    lines[9] = "from app.config import X\n"
    fx.commit(repo, {path: "".join(lines)}, "fix import", fx.T0 + timedelta(hours=6))


@pytest.mark.parametrize(
    "path",
    [
        "dir with space/module.py",
        "café/müdüle.py",  # git quotes non-ASCII names unless told not to
        "文档/说明 ☃.py",
        "it's a file.py",
        "-leading-dash.py",
        "a b/c.py",  # a directory named "a b" and a file "c.py": the header holds " b/"
    ],
)
def test_unusual_file_names_still_give_signals(tmp_path: Path, path: str) -> None:
    repo = fx.init_repo(tmp_path / "r")
    _refix_in(repo, path)
    (signal,) = detect_refixes(read_commits(repo))
    assert signal.files == [path]


def test_control_characters_in_a_commit_message_do_not_break_reading(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "r")
    fx.commit(repo, {"a.txt": "1\n"}, "before", fx.T0)
    nasty = "separators \x1e \x1f \x1d inside"
    message = nasty + "\n\nbody \x1e\x1f\x1d too"
    fx.commit(repo, {"b.txt": "2\n"}, message, fx.T0 + timedelta(hours=1))
    fx.commit(repo, {"c.txt": "3\n"}, "after", fx.T0 + timedelta(hours=2))
    commits = read_commits(repo)
    assert [c.subject for c in commits] == ["before", nasty, "after"]
    assert "\x1e\x1f\x1d" in commits[1].body


def test_a_message_in_another_encoding_does_not_crash_reading(tmp_path: Path) -> None:
    repo = fx.init_repo(tmp_path / "r")
    fx.commit(repo, {"a.txt": "1\n"}, "before", fx.T0)
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "i18n.commitEncoding=latin1", "commit", "-q", "-F", "-"],
        input=b"caf\xe9 \xff bytes\n", check=True, capture_output=True,
    )  # fmt: skip
    commits = read_commits(repo)
    assert len(commits) == 2 and commits[1].subject.startswith("caf")


def test_unquote_undoes_gits_c_style_quoting() -> None:
    assert _unquote("plain/name.py") == "plain/name.py"
    assert _unquote('"dir/m\\303\\274d.py"') == "dir/müd.py"
    assert _unquote('"q\\"uote\\\\back.py"') == 'q"uote\\back.py'
    assert _unquote('"tab\\tname"') == "tab\tname"
    assert _unquote('"raw é stays"') == "raw é stays"  # quotepath=false keeps UTF-8
    assert _unquote('"broken\\') == '"broken\\'  # an unterminated quote is left alone


def test_header_paths_are_found_by_length_not_by_guessing() -> None:
    assert _header_path("diff --git a/x.py b/x.py") == "x.py"
    assert _header_path("diff --git a/x y.py b/x y.py") == "x y.py"
    assert _header_path("diff --git a/a b/c.py b/a b/c.py") == "a b/c.py"
    assert _header_path('diff --git "a/q\\"x" "b/q\\"x"') == 'q"x'
    assert _header_path("diff --git a/x.py b/y.py") is None  # halves differ: not ours to parse
    assert _header_path("index 123..456 100644") is None
