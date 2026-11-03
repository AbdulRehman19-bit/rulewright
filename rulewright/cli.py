import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from rulewright.cluster.embed import Embedder, SentenceTransformerEmbedder
from rulewright.config import load_config
from rulewright.guard.emit import (
    SettingsError,
    load_settings,
    render,
    rule_for,
    settings_path,
    unified_diff,
    with_rules,
    write_atomic,
)
from rulewright.guard.profile import MIN_USES, Pattern, allowable, build_profile
from rulewright.ingest.git_history import HistoryError, read_commits
from rulewright.ingest.sessions.base import ToolCall, ToolCallSource
from rulewright.ingest.sessions.claude_code import ClaudeCodeSource
from rulewright.ingest.sessions.codex import CodexSource
from rulewright.mine.detectors import (
    dedupe_signals,
    detect_corrections,
    detect_refixes,
    detect_reverts,
)
from rulewright.pipeline import run_scan
from rulewright.pr import PRError, create_pr
from rulewright.propose.llm import (
    CostEstimate,
    LiteLLMClient,
    ollama_models,
    pick_model,
)
from rulewright.prove.agent import BuiltinAgent
from rulewright.prove.runner import ProveReport, estimate_runs, prove_rule
from rulewright.prove.sandbox import DEFAULT_IMAGE, DockerSandbox, Sandbox
from rulewright.prove.tasks import MAX_TASKS, build_tasks
from rulewright.report import REPORT_NAME, render_report
from rulewright.rules.emit import MarkerError, plan_emit, write_changes
from rulewright.rules.model import Rule
from rulewright.rules.source import (
    FILENAME,
    RulesFile,
    RulesFileError,
    load_pending,
    load_rules,
    save_pending,
    save_rules,
)
from rulewright.rules.stale import stale_findings

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
    """rulewright command line."""


@app.command()
def init(repo: RepoOption = Path(".")) -> None:
    """Create an empty rulewright.yaml."""
    path = repo / FILENAME
    if path.exists():
        raise _fail(f"{FILENAME} already exists; nothing changed.")
    save_rules(repo, RulesFile())
    console.print(f"Created {path}. Add `.rulewright/` to .gitignore; commit {FILENAME}.")


def _make_embedder() -> Embedder:
    return SentenceTransformerEmbedder()


def _make_client(model: str | None) -> LiteLLMClient | None:
    chosen = model or pick_model(os.environ, ollama_models())
    return LiteLLMClient(chosen) if chosen else None


def _short(rule: Rule) -> str:
    refs = [(e.commit_sha or e.signal_id)[:7] for e in rule.evidence[:3]]
    more = len(rule.evidence) - len(refs)
    return ", ".join(refs) + (f" +{more}" if more > 0 else "")


@app.command()
def scan(
    repo: RepoOption = Path("."),
    commits: Annotated[
        int | None, typer.Option(min=1, help="How many recent commits to read.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="LiteLLM model, e.g. ollama/llama3.1.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before a paid LLM call.")] = False,
    judge: Annotated[
        bool, typer.Option("--judge/--no-judge", help="Second LLM call that drops generic rules.")
    ] = True,
    pr: Annotated[
        bool, typer.Option("--pr", help="Open a pull request with the guard-approved rules.")
    ] = False,
) -> None:
    """Find repeated agent mistakes and propose rules. Changes no agent file."""
    limit = commits or load_config(repo).commits
    existing = _load(repo)
    now = datetime.now(UTC)

    with console.status("Starting...") as status:

        def confirm(estimate: CostEstimate) -> bool:
            price = "unknown price" if estimate.usd is None else f"about ${estimate.usd:.4f}"
            status.stop()  # a live spinner hides the prompt, so the scan looks stuck
            try:
                console.print(
                    f"LLM: {estimate.model}, {estimate.calls} call(s), ~{estimate.tokens_in} "
                    f"tokens in, ~{estimate.tokens_out} out: {price}"
                )
                if estimate.usd == 0.0 or yes:
                    return True
                return typer.confirm("Proceed with the paid call?")
            finally:
                status.start()

        def progress(i: int, n: int) -> None:
            status.update(f"Writing rule {i} of {n} with the LLM (a local model can be slow)...")

        try:
            result = run_scan(
                repo, limit, _make_embedder(), _make_client(model), existing, now, confirm,
                sessions=[ClaudeCodeSource()], progress=progress, judge=judge,
                stage=lambda name: status.update(name + "..."),
            )  # fmt: skip
        except HistoryError as e:
            status.stop()
            raise _fail(str(e)) from None

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
        console.print(f"[dim]dropped {d.cluster_id}: {escape(d.reason)}[/dim]")
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
        console.print("Nothing has been applied. Accept rules with `rulewright apply <id> ...`.")
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
    """Re-render every target file from the accepted rules in rulewright.yaml."""
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
    """Accept pending proposals into rulewright.yaml and write the target files."""
    pending = load_pending(repo)
    if not pending:
        raise _fail("No pending proposals. Run `rulewright scan` first.")
    if not ids and not all_safe:
        table = Table("id", "rule", "risk")
        for r in pending:
            flags = ", ".join(r.risk_flags) or "-"
            table.add_row(r.id, r.text, flags, style="red" if r.risk_flags else "")
        console.print(table)
        console.print("Choose rules: `rulewright apply <id> ...` or `--all-safe`.")
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
    commits: Annotated[
        int | None, typer.Option(min=1, help="Commits to read for activity.")
    ] = None,
    mark: Annotated[
        bool, typer.Option("--mark", help="Set stale rules to status 'stale' in rulewright.yaml.")
    ] = False,
) -> None:
    """List accepted rules that mention missing paths or commands, or that went silent."""
    rules_file = _load(repo)
    limit = commits or load_config(repo).commits
    try:
        history = read_commits(repo, limit)
    except HistoryError as e:
        raise _fail(str(e)) from None
    signals = detect_reverts(history) + detect_refixes(history)
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
        console.print(f"Marked {marked} rule(s) stale; run `rulewright emit`.")


def _docker_problem() -> str | None:
    """Why the sandbox cannot be used, or None when Docker is reachable."""
    try:
        import docker

        docker.from_env().ping()
    except Exception as e:  # the SDK raises several types; all mean the same to the user
        return f"Docker is not available ({type(e).__name__}). Start Docker Desktop or the daemon."
    return None


def _make_sandbox_factory(image: str) -> Callable[[Path], Sandbox]:
    return lambda work: DockerSandbox(work, image)


def _rate(report: ProveReport, arm: str) -> str:
    rate = report.rate(arm)
    if rate is None:
        return "no judged runs"
    return f"{report.recurrences(arm)}/{len(report.judged(arm))} ({rate:.0%})"


@app.command()
def prove(
    rule_id: Annotated[str, typer.Argument(help="Id of an accepted rule in rulewright.yaml.")],
    repo: RepoOption = Path("."),
    tasks: Annotated[int, typer.Option(help="Past tasks to replay (at most 10).")] = 10,
    trials: Annotated[int, typer.Option(help="Repeat each task this many times.")] = 1,
    max_cost: Annotated[float | None, typer.Option(help="Stop before spending more (USD).")] = None,
    model: Annotated[str | None, typer.Option(help="LiteLLM model for the agent.")] = None,
    image: Annotated[str, typer.Option(help="Docker image the agent works in.")] = DEFAULT_IMAGE,
    commits: Annotated[
        int, typer.Option(min=1, help="History to search for the evidence.")
    ] = 2000,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before a paid run.")] = False,
) -> None:
    """Replay past tasks with and without a rule and report how often the mistake recurs."""
    rule = _load(repo).get(rule_id)
    if rule is None:
        raise _fail(f"No rule {rule_id} in {FILENAME}.", 2)
    try:
        history = read_commits(repo, commits)
    except HistoryError as e:
        raise _fail(str(e)) from None
    signals = dedupe_signals(detect_reverts(history) + detect_refixes(history))
    chosen = build_tasks(rule, history, signals, min(tasks, MAX_TASKS))
    if not chosen:
        raise _fail(
            f"None of the evidence commits for {rule_id} is in the last {commits} commits; "
            "try a larger --commits."
        )
    client = _make_client(model)
    if client is None:
        raise _fail("No LLM available: start Ollama, set GROQ_API_KEY or similar, or use --model.")
    if (problem := _docker_problem()) is not None:
        raise _fail(problem)

    runs = len(chosen) * trials * 2
    tokens_in, tokens_out, usd = estimate_runs(client.model, runs)
    price = "unknown price" if usd is None else f"about ${usd:.2f}"
    console.print(
        f"{len(chosen)} task(s) x 2 arms x {trials} trial(s) = {runs} agent runs with "
        f"{client.model}: ~{tokens_in:,} tokens in, ~{tokens_out:,} out, {price}."
    )
    if max_cost is not None and usd is not None and usd > max_cost:
        console.print(f"[yellow]The estimate is above --max-cost ${max_cost:.2f}; "
                      "the run will stop at the ceiling.[/yellow]")  # fmt: skip
    if usd != 0.0 and not yes and not typer.confirm("Proceed with the paid runs?"):
        raise _fail("Cancelled before any run.", 0)

    with console.status("Replaying tasks in the sandbox...") as status:

        def progress(i: int, n: int) -> None:
            status.update(f"Agent run {i} of {n} (each in a fresh container)...")

        report = prove_rule(
            rule, chosen, repo, BuiltinAgent(client), _make_sandbox_factory(image), trials,
            max_cost, judge_client=client, progress=progress,
        )  # fmt: skip

    table = Table("", "without the rule", "with the rule", title=f"{rule.id}: {rule.text}")
    table.add_row("mistake recurred", _rate(report, "without"), _rate(report, "with"))
    table.add_row(
        "runs that changed files", str(report.changed("without")), str(report.changed("with"))
    )
    console.print(table)
    cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
    console.print(f"{len(report.records)} run(s), approximate cost {cost}.")
    if report.aborted:
        console.print(f"[yellow]{report.aborted}; results are partial.[/yellow]")
    errors = [r for r in report.records if r.error]
    if errors:
        first = errors[0].error
        console.print(f"[yellow]{len(errors)} run(s) failed and are not counted: {first}[/yellow]")
    console.print(
        "[dim]Evidence, not proof: a handful of tasks, nondeterministic agents and "
        "prompts rebuilt from commit messages.[/dim]"
    )
    out = repo / ".rulewright" / "prove"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rule.id}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _tool_call_sources() -> list[ToolCallSource]:
    return [ClaudeCodeSource(), CodexSource()]


def _guard_table(title: str, patterns: list[Pattern], style: str = "") -> None:
    table = Table("pattern", "uses", "sessions", "failed", "example", title=title)
    for p in patterns:
        table.add_row(p.pattern, str(p.uses), str(p.sessions), str(p.failures), p.example,
                      style=style)  # fmt: skip
    console.print(table)


@app.command()
def guard(
    repo: RepoOption = Path("."),
    min_uses: Annotated[int, typer.Option(help="Times a command must have run.")] = MIN_USES,
    include_review: Annotated[
        bool, typer.Option("--include-review", help="Also offer commands outside the safe list.")
    ] = False,
    local: Annotated[
        bool, typer.Option("--local", help="Use .claude/settings.local.json, not settings.json.")
    ] = False,
    diff: Annotated[bool, typer.Option("--diff", help="Show the change; write nothing.")] = False,
    apply: Annotated[bool, typer.Option("--apply", help="Write the change after asking.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before writing.")] = False,
) -> None:
    """Propose a Claude Code permission allowlist from the commands agents ran here."""
    calls: list[ToolCall] = []
    for source in _tool_call_sources():
        calls += source.read_calls(repo)
    profile = build_profile(calls, min_uses)
    if profile.commands_seen == 0:
        console.print(
            "No shell commands found in your Claude Code or Codex session logs for this repo."
        )
        raise typer.Exit(0)
    console.print(
        f"Read {profile.commands_seen} shell command(s) from agent sessions"
        + (f"; ignored {profile.skipped_other_shell} PowerShell command(s)"
           if profile.skipped_other_shell else "")
        + "."
    )  # fmt: skip
    safe, review = profile.tier("safe"), profile.tier("review")
    if safe:
        _guard_table("Safe to allow (read-only, test, lint, build)", safe)
    if review:
        note = "included" if include_review else "not written unless --include-review"
        _guard_table(f"Run often, but your call ({note})", review, "yellow")
    if never := profile.tier("never"):
        names = ", ".join(f"{p.pattern} ({p.uses}x)" for p in never[:8])
        console.print(f"[dim]Never offered, Claude Code keeps asking: {names}[/dim]")
    refused = [p.pattern for p in profile.patterns if p.denied]
    if refused:
        console.print(f"[dim]You refused these at least once, so they are left out: "
                      f"{', '.join(refused)}[/dim]")  # fmt: skip

    chosen = allowable(profile, include_review)
    if not chosen:
        console.print("Nothing to allow yet. Try a lower --min-uses or more sessions.")
        raise typer.Exit(0)
    path = settings_path(repo, local)
    try:
        old = load_settings(path)
    except SettingsError as e:
        raise _fail(str(e)) from None
    new, added = with_rules(old, [rule_for(p) for p in chosen])
    if not added:
        console.print(f"Every rule is already in {path.name}. Nothing to change.")
        raise typer.Exit(0)
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    text = unified_diff(before, render(new), f".claude/{path.name}")
    if diff or apply:
        console.print(text, markup=False, highlight=False)
    if not apply:
        console.print(f"{len(added)} rule(s) would be added. Nothing was written. "
                      "Use --diff to see the change and --apply to write it.")  # fmt: skip
        raise typer.Exit(0)
    if not yes and not typer.confirm(f"Write {len(added)} rule(s) to {path}?"):
        raise _fail("Cancelled; nothing was written.", 0)
    write_atomic(path, render(new))
    console.print(f"Wrote {len(added)} rule(s) to {path}.")
