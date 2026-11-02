"""rulesmith-report.md: what a scan found, with the evidence behind every rule."""

from datetime import datetime
from pathlib import Path

from rulesmith.pipeline import ScanResult
from rulesmith.pr import commit_url

REPORT_NAME = "rulesmith-report.md"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_report(result: ScanResult, repo: Path, now: datetime) -> str:
    kinds: dict[str, int] = {}
    for s in result.signals:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    by_id = {s.id: s for s in result.signals}
    out = [
        "# rulesmith report",
        "",
        f"Generated {now:%Y-%m-%d %H:%M} UTC. Nothing here has been applied.",
        "",
        f"- Commits read: {result.commits_read} ({result.agent_commits} agent-authored)",
        "- Signals: "
        + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"),
        f"- Clusters (3+ similar signals): {len(result.clusters)}",
        "",
    ]
    if result.cold_start:
        out += [f"> {result.cold_start}", ""]
    out += [f"> {n}" for n in result.notes]
    if result.notes:
        out.append("")

    rules = result.proposals.rules if result.proposals else []
    out.append(f"## Proposed rules ({len(rules)})")
    out.append("")
    for r in rules:
        out.append(f"### {r.id}" + ("  (RISK FLAGGED)" if r.risk_flags else ""))
        out.append(f"> {_one_line(r.text)}")
        out.append("")
        out.append("- Scope: " + ", ".join(f"`{s}`" for s in r.scope))
        if r.risk_flags:
            out.append("- Risk flags: " + ", ".join(r.risk_flags) + " (never pre-selected)")
        out.append("- Evidence:")
        for e in r.evidence:
            kind = by_id[e.signal_id].kind if e.signal_id in by_id else "signal"
            ref = e.commit_sha or e.signal_id
            url = e.url or (commit_url(repo, e.commit_sha) if e.commit_sha else "")
            out.append(f"  - {kind}: [{ref[:7]}]({url})" if url else f"  - {kind}: {ref[:12]}")
        out.append("")
    if result.proposals and result.proposals.dropped:
        out += ["## Dropped clusters", ""]
        out += [f"- {d.cluster_id}: {d.reason}" for d in result.proposals.dropped]
        out.append("")
    if result.stale:
        out += ["## Stale rules", ""]
        out += [f"- {f.rule_id}: {f.reason} ({_one_line(f.detail)})" for f in result.stale]
        out.append("")
    return "\n".join(out)
