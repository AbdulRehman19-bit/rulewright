# rulesmith

**Your coding agent learns from its mistakes, provably, and safely.**

rulesmith reads your repo's git history (and Claude Code session logs), finds the mistakes
coding agents keep repeating, and proposes evidence-backed rules for `AGENTS.md`,
`CLAUDE.md`, `.cursorrules` and Copilot instructions. Every rule cites at least 3 commits or
messages. Nothing is written until you approve it.

> **Status: pre-release (v0.1).** Not on PyPI yet, and not yet tried on public repos.
> `prove` (replay past tasks to check a rule works) is planned for v0.2.

<!-- TODO before launch: GIF of `rulesmith scan` on a known public repo -->

## Install

```
pipx install git+https://github.com/AbdulRehman19-bit/rulesmith.git
```

Python 3.11+. After the first release: `pipx install rulesmith`.

## Use

```
rulesmith scan            # read the last 500 commits, print proposed rules, change nothing
rulesmith apply r-3e6f373f r-5f86d161    # accept chosen rules, write rulesmith.yaml and agent files
rulesmith emit --dry-run  # preview re-rendering every agent file from rulesmith.yaml
rulesmith stale           # rules that mention paths or commands that no longer exist
```

Mining and clustering run locally with no API key. Only the last step, writing the rule text,
calls an LLM: a local Ollama model if one is running, otherwise your own `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`. The estimated cost is shown, and you are asked, before any paid call.

## What a result looks like

This is real output on a small demo repo with planted mistakes (not a public repo), using a local
`qwen2.5-coder:7b`:

```
Read 45 commits (15 agent-authored); 15 signal(s) [refix] in 3 cluster(s).
 id          proposed rule                                      scope         evidence
 r-5f86d161  Do not skip failing pytest tests; fix them instead. tests/*.py    e0d279a, b4b4120, 4365f63 +2
```

`rulesmith-report.md` lists the evidence behind each rule as commit links.

**Rule quality depends on the model.** On busy real repositories a small local model (7B) wrote
generic rules from good clusters. Judge every rule by its evidence, and for real repos use a
stronger model: `rulesmith scan --model anthropic/claude-haiku-5-5` (your own API key; the
cost is shown first).

## How it stays safe

rulesmith writes text that agents will obey, so everything it mines is treated as hostile.

- Mined text reaches the LLM only inside a delimited data block (random tag), and the reply must
  match a fixed schema.
- Proposed rules are scanned for URLs, curl/wget, credentials, env files, new shell commands and
  disabling tests or permissions. Flagged rules are shown in red and never pre-selected.
- A rule needs 3+ pieces of evidence, and each cited commit must exist in your repo.
- Likely secrets are redacted before any API call.
- Nothing is written without `apply` (or a pull request you review). Only the content between
  `<!-- rulesmith:start -->` and `<!-- rulesmith:end -->` is ever edited.

See [SECURITY.md](SECURITY.md).

## Roadmap

- **v0.1** `scan`, `apply`, `emit`, `stale`: this release.
- **v0.2** `prove`: replay past tasks in a Docker sandbox with and without a rule and report how
  often the mistake recurs. Results are evidence, not statistical proof.
- **v0.3** `guard`: learn a least-privilege permission allowlist from the same session logs.

How it works and why each choice was made: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/DECISIONS.md](docs/DECISIONS.md).
