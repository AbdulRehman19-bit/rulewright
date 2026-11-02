from datetime import UTC, datetime, timedelta
from pathlib import Path

from rulewright.mine.signals import Signal
from rulewright.rules.model import Rule
from rulewright.rules.stale import glob_match, stale_findings
from tests.fixtures import build_repos as fx

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _rule(text: str, scope: list[str] | None = None, status: str = "accepted") -> Rule:
    return Rule(id="r-1", text=text, scope=scope or ["**"], status=status)  # type: ignore[arg-type]


def _repo(tmp_path: Path) -> Path:
    repo = fx.init_repo(tmp_path / "r")
    fx.commit(
        repo,
        {
            "app/config.py": "X = 1\n",
            "docs/guide.md": "hi\n",
            "Makefile": "lint:\n\techo lint\n",
            "package.json": '{"scripts": {"test": "jest"}}',
        },
        "initial",
        fx.T0,
    )
    return repo


def _reasons(repo: Path, rule: Rule) -> list[tuple[str, str]]:
    return [(f.reason, f.detail) for f in stale_findings([rule], repo)]


def test_glob_semantics() -> None:
    assert glob_match("app/x/y.py", "app/**") and glob_match("a.py", "**/*.py")
    assert glob_match("src/a/b.py", "**/*.py") and not glob_match("app/x/y.py", "app/*.py")
    assert glob_match("app/y.py", "app/*.py") and not glob_match("lib/y.py", "app/**")


def test_existing_paths_and_commands_are_fine(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rule = _rule("Edit app/config.py and docs/, run `make lint` and `npm run test`.", ["app/**"])
    assert _reasons(repo, rule) == []


def test_dead_paths_are_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    found = _reasons(repo, _rule("Put settings in app/settings.py, see docs/old/guide.md."))
    assert found == [("dead-path", "app/settings.py"), ("dead-path", "docs/old/guide.md")]


def test_module_names_and_urls_are_not_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    text = "Import from app.config, never app.settings; see https://example.com/a/b."
    assert _reasons(repo, _rule(text)) == []


def test_dead_commands_are_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    found = _reasons(repo, _rule("Run `make deploy` then `npm run build`, or `make lint`."))
    assert found == [("dead-command", "make deploy"), ("dead-command", "npm run build")]


def test_scope_matching_no_files_is_dead(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _reasons(repo, _rule("Be tidy.", ["legacy/**"])) == [("dead-scope", "legacy/**")]


def _sig(text: str, files: list[str], age_days: int) -> Signal:
    return Signal(
        id="s", kind="refix", commit_sha="a" * 7, files=files, text=text, author_is_agent=True,
        score=0.8, ts=NOW - timedelta(days=age_days),
    )  # fmt: skip


def test_silent_rule_has_no_recent_matching_signal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rule = _rule("Import settings from app.config, never app.settings.", ["app/**"])
    recent_by_scope = _sig("something else", ["app/config.py"], 10)
    recent_by_words = _sig("agent imports settings from app.settings", [], 10)
    old = _sig("agent imports settings from app.settings", ["app/config.py"], 120)
    unrelated = _sig("renamed a docs heading", ["docs/guide.md"], 5)

    def silent(signals: list[Signal]) -> bool:
        return any(f.reason == "silent" for f in stale_findings([rule], repo, signals, NOW))

    assert not silent([recent_by_scope]) and not silent([recent_by_words])
    assert silent([old]) and silent([unrelated]) and silent([])
    assert not any(f.reason == "silent" for f in stale_findings([rule], repo))  # not asked for


def test_only_accepted_rules_are_checked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert stale_findings([_rule("See nope/missing.py.", status="proposed")], repo) == []
