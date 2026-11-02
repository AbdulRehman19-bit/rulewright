import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from rulesmith.cluster.embed import Embedder, SentenceTransformerEmbedder
from rulesmith.config import load_config
from rulesmith.ingest.git_history import read_commits
from rulesmith.ingest.sessions.claude_code import ClaudeCodeSource
from rulesmith.mine.detectors import detect_corrections, detect_refixes, detect_reverts
from rulesmith.pipeline import run_scan
from rulesmith.pr import PRError, create_pr
from rulesmith.propose.llm import (
    CostEstimate,
    LiteLLMClient,
    LLMClient,
    ollama_models,
    pick_model,
)
from rulesmith.report import REPORT_NAME, render_report
from rulesmith.rules.emit import MarkerError, plan_emit, write_changes
from rulesmith.rules.model import Rule
from rulesmith.rules.source import (
    FILENAME,
    RulesFile,
    RulesFileError,
    load_pending,
    load_rules,
    save_pending,
    save_rules,
)
from rulesmith.rules.stale import stale_findings

app = typer.Typer(help="Learn rules for your coding agent from its past mistakes.")
console = Console()

RepoOption = Annotated[Path, typer.Option("--repo", help="Repository root.")]


def _fail(message: str, code: int = 1) -> typer.Exit:
    console.print(f"[red]{message}[/red]")
    return typer.Exit(code)


def _load(repo: Path) -> RulesFile:
    try:
        return load_rules(repo)
    except RulesFileError as e:
        raise _fail(str(e)) from e


def _emit(repo: Path, rules_file: RulesFile, dry_run: bool) -> None:
    try:
        changes = plan_emit(repo, rules_file)
    except (MarkerError, ValueError) as e:
        raise _fail(f"Nothing was written: {e}") from e
    if not changes:
        console.print("Target files are already up to date.")
        return
    for c in changes:
        if dry_run:
            console.print(f"[bold]{c.path}[/bold]")
            console.print(c.diff(), markup=False, highlight=False)
        else:
            console.print(f"{'created' if c.old is None else 'updated'} {c.path}")
    if not dry_run:
        write_changes(changes)


@app.callback()
def main() -> None:
    """rulesmith command line."""


@app.command()
def init(repo: RepoOption = Path(".")) -> None:
    """Create an empty rulesmith.yaml."""
    path = repo / FILENAME
    if path.exists():
        raise _fail(f"{FILENAME} already exists; nothing changed.")
    save_rules(repo, RulesFile())
    console.print(f"Created {path}. Add `.rulesmith/` to .gitignore; commit {FILENAME}.")


def _make_embedder() -> Embedder:
    return SentenceTransformerEmbedder()


def _make_client(model: str | None) -> LLMClient | None:
    chosen = model or pick_model(os.environ, ollama_models())
    return LiteLLMClient(chosen) if chosen else None


def _short(rule: Rule) -> str:
    refs = [(e.commit_sha or e.signal_id)[:7] for e in rule.evidence[:3]]
    more = len(rule.evidence) - len(refs)
    return ", ".join(refs) + (f" +{more}" if more > 0 else "")


@app.command()
def scan(
    repo: RepoOption = Path("."),
    commits: Annotated[int | None, typer.Option(help="How many recent commits to read.")] = None,
    model: Annotated[str | None, typer.Option(help="LiteLLM model, e.g. ollama/llama3.1.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before a paid LLM call.")] = False,
    pr: Annotated[
        bool, typer.Option("--pr", help="Open a pull request with the guard-approved rules.")
    ] = False,
) -> None:
    """Find repeated agent mistakes and propose rules. Changes no agent file."""
    limit = commits or load_config(repo).commits
    existing = _load(repo)
    now = datetime.now(UTC)

    def confirm(estimate: CostEstimate) -> bool:
        price = "unknown price" if estimate.usd is None else f"about ${estimate.usd:.4f}"
        console.print(
            f"LLM: {estimate.model}, {estimate.calls} call(s), ~{estimate.tokens_in} tokens in, "
            f"~{estimate.tokens_out} out: {price}"
        )
        if estimate.usd == 0.0 or yes:
            return True
        return typer.confirm("Proceed with the paid call?")

    with console.status("Reading history and clustering mistakes..."):
        result = run_scan(
            repo, limit, _make_embedder(), _make_client(model), existing, now, confirm,
            sessions=[ClaudeCodeSource()],
        )  # fmt: skip

    kinds = ", ".join(sorted({s.kind for s in result.signals}))
    console.print(
        f"Read {result.commits_read} commits ({result.agent_commits} agent-authored); "
        f"{len(result.signals)} signal(s)" + (f" [{kinds}]" if kinds else "")
        + f" in {len(result.clusters)} cluster(s)."
    )
    if result.cold_start:
        console.print(f"[yellow]{result.cold_start}[/yellow]")
    for note in result.notes:
        console.print(f"[yellow]{note}[/yellow]")

    rules = result.proposals.rules if result.proposals else []
    if rules:
        table = Table("id", "proposed rule", "scope", "evidence", "risk")
        for r in rules:
            table.add_row(
                r.id, r.text, ", ".join(r.scope), _short(r), ", ".join(r.risk_flags) or "-",
                style="red" if r.risk_flags else "",
            )  # fmt: skip
        console.print(table)
    for d in result.proposals.dropped if result.proposals else []:
        console.print(f"[dim]dropped {d.cluster_id}: {d.reason}[/dim]")
    if result.stale:
        table = Table("stale rule", "reason", "detail")
        for f in result.stale:
            table.add_row(f.rule_id, f.reason, f.detail)
        console.print(table)

    report = repo / REPORT_NAME
    report.write_text(render_report(result, repo, now), encoding="utf-8")
    console.print(f"Report: {report}")
    if result.proposals is not None:
        save_pending(repo, rules)
    if rules:
        console.print("Nothing has been applied. Accept rules with `rulesmith apply <id> ...`.")
    if pr:
        safe = [r for r in rules if not r.risk_flags]
        if not safe:
            console.print("[yellow]No rules passed the guards, so no pull request.[/yellow]")
            return
        try:
            outcome = create_pr(repo, safe, existing, now)
        except (PRError, MarkerError) as e:
            raise _fail(f"Pull request not created: {e}") from e
        console.print(f"Branch {outcome.branch}" + (f"; PR {outcome.url}" if outcome.url else ""))
        for note in outcome.notes:
            console.print(f"[yellow]{note}[/yellow]")


@app.command()
def emit(
    repo: RepoOption = Path("."),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show a diff, write nothing.")] = False,
) -> None:
    """Re-render every target file from the accepted rules in rulesmith.yaml."""
    _emit(repo, _load(repo), dry_run)


@app.command()
def apply(
    ids: Annotated[list[str] | None, typer.Argument(help="Rule ids to accept.")] = None,
    all_safe: Annotated[
        bool, typer.Option("--all-safe", help="Accept every pending rule without risk flags.")
    ] = False,
    allow_risky: Annotated[
        bool, typer.Option("--allow-risky", help="Allow accepting rules that carry risk flags.")
    ] = False,
    repo: RepoOption = Path("."),
) -> None:
    """Accept pending proposals into rulesmith.yaml and write the target files."""
    pending = load_pending(repo)
    if not pending:
        raise _fail("No pending proposals. Run `rulesmith scan` first.")
    if not ids and not all_safe:
        table = Table("id", "rule", "risk")
        for r in pending:
            flags = ", ".join(r.risk_flags) or "-"
            table.add_row(r.id, r.text, flags, style="red" if r.risk_flags else "")
        console.print(table)
        console.print("Choose rules: `rulesmith apply <id> ...` or `--all-safe`.")
        return
    by_id = {r.id: r for r in pending}
    unknown = [i for i in ids or [] if i not in by_id]
    if unknown:
        raise _fail(f"Unknown rule ids: {', '.join(unknown)}", 2)
    chosen = [by_id[i] for i in ids or []]
    if all_safe:
        chosen += [r for r in pending if not r.risk_flags and r.id not in {c.id for c in chosen}]
    risky = [r.id for r in chosen if r.risk_flags]
    if risky and not allow_risky:
        raise _fail(f"Refusing risk-flagged rules without --allow-risky: {', '.join(risky)}")
    rules_file = _load(repo)
    try:  # check the targets first so a broken marker leaves everything untouched
        changes = plan_emit(repo, _with(rules_file, chosen))
    except (MarkerError, ValueError) as e:
        raise _fail(f"Nothing was written: {e}") from e
    save_rules(repo, _with(rules_file, chosen))
    write_changes(changes)
    chosen_ids = {r.id for r in chosen}
    save_pending(repo, [r for r in pending if r.id not in chosen_ids])
    console.print(f"Accepted {len(chosen)} rule(s); updated {len(changes)} file(s).")
    for c in changes:
        console.print(f"  {c.path}")


def _with(rules_file: RulesFile, chosen: list[Rule]) -> RulesFile:
    updated = rules_file.model_copy(deep=True)
    for rule in chosen:
        updated.add(rule, "accepted")
    return updated


@app.command()
def stale(
    repo: RepoOption = Path("."),
    commits: Annotated[int | None, typer.Option(help="Commits to read for activity.")] = None,
    mark: Annotated[
        bool, typer.Option("--mark", help="Set stale rules to status 'stale' in rulesmith.yaml.")
    ] = False,
) -> None:
    """List accepted rules that mention missing paths or commands, or that went silent."""
    rules_file = _load(repo)
    limit = commits or load_config(repo).commits
    signals = detect_reverts(read_commits(repo, limit)) + detect_refixes(read_commits(repo, limit))
    signals += detect_corrections(ClaudeCodeSource().read(repo))
    findings = stale_findings(rules_file.rules, repo, signals, datetime.now(UTC))
    if not findings:
        console.print("No stale rules.")
        return
    table = Table("rule", "reason", "detail")
    for f in findings:
        table.add_row(f.rule_id, f.reason, f.detail)
    console.print(table)
    if mark:
        for rule_id in {f.rule_id for f in findings}:
            rule = rules_file.get(rule_id)
            if rule:
                rules_file.add(rule, "stale")
        save_rules(repo, rules_file)
        marked = len({f.rule_id for f in findings})
        console.print(f"Marked {marked} rule(s) stale; run `rulesmith emit`.")
