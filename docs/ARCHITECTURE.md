# Architecture: flow, how it works, what is used

Keep this file current: update it in the same change that alters the flow, a module or a dependency.
Why things were chosen lives in [DECISIONS.md](DECISIONS.md).

## Flow

```
git history / session logs / PR comments
   -> ingest   raw history to uniform events (no judgment)
   -> mine     detectors find agent mistakes, score them -> Signal
   -> cluster  embed + HDBSCAN, keep clusters of 3+ signals
   -> propose  LLM drafts one rule per cluster, guards check it
   -> approve  user runs `apply` (or approves a PR)
   -> rules    rulewright.yaml (single source of truth) -> emit to AGENTS.md, CLAUDE.md, .cursorrules, copilot file
```
Ingest, mine and cluster run locally with no API key. Only propose calls an LLM.
Nothing is written to an agent file without approval, and only between the rulewright markers.

## Modules (package `rulewright/`)

| Module | Job | State |
|---|---|---|
| `cli.py` | Typer app: `init`, `scan`, `apply`, `emit`, `stale`, `prove` | done |
| `pipeline.py` | `run_scan`: ingest, detect, dedupe, score, embed, cluster, propose, stale check; writes no files itself (the CLI then writes the report and `.rulewright/` cache) | done |
| `report.py` | `rulewright-report.md` | done |
| `pr.py` | `scan --pr`: branch, commit, push, `gh pr create`; GitHub commit URLs | done |
| `config.py` | `.rulewright/config.toml` with defaults | done |
| `ingest/git_history.py` | `git log` to `Commit` with line ranges and patch, agent attribution | done |
| `ingest/sessions/base.py`, `claude_code.py` | `SessionSource` interface, Claude Code transcript reader | done |
| `ingest/sessions/codex.py` | Codex rollout reader (commands only), checked against a real rollout | done |
| `ingest/reviews.py` | PR review comments (needs token) | placeholder |
| `mine/signals.py` | `Signal` model | done |
| `mine/detectors.py` | revert, re-fix, correction detectors | done; review detector todo |
| `mine/score.py` | recency weighting and threshold | done |
| `cluster/embed.py` | `Embedder` interface, sentence-transformers embedder, SQLite embedding cache | done |
| `cluster/group.py` | HDBSCAN grouping, `Cluster` model, `coherence` score, cold-start message | done |
| `propose/redact.py` | secret redaction before any API call | done |
| `propose/guards.py` | risk scanner, scope cleaning, evidence verification | done |
| `propose/llm.py` | model choice (Ollama, else user key), LiteLLM client, cost estimate | done |
| `propose/writer.py` | prompt with delimited data block, schema parsing, `propose_rules` | done |
| `rules/model.py` | `Rule`, `Evidence` | done |
| `rules/source.py` | rulewright.yaml load/save, pending-proposals store | done |
| `rules/emit/` | marker-based block replacement, one writer per target (AGENTS.md, CLAUDE.md, .cursorrules, copilot-instructions.md) | done |
| `rules/stale.py` | dead paths, dead commands, dead scopes, silent rules | done |
| `prove/tasks.py` | rebuild past tasks from a rule's evidence (parent tree, prompt, known-bad lines) | done |
| `prove/sandbox.py` | `Sandbox` interface, `DockerSandbox` (no network, no env, caps dropped, limits) | done |
| `prove/agent.py` | `AgentRunner` interface, `BuiltinAgent` JSON-action loop | done |
| `prove/judge.py` | workspace snapshots, deterministic recurrence check | done |
| `prove/runner.py` | `prove_rule`: both arms, trials, cost ceiling, LLM-judge fallback, report | done |
| `guard/profile.py` | split commands, classify against a curated safe list, count uses, build the profile | done |
| `guard/emit.py` | add `Bash(<prefix>:*)` rules to `.claude/settings.json` (merge, diff, atomic write) | done |
| `store.py` | SQLite cache in `.rulewright/` (embeddings table) | embeddings done; signals and rule history later |

## How the finished pieces work

- **`guard`** (no LLM, nothing executed): reads the shell commands agents ran for this repo (Claude Code `Bash` tool calls with their results; Codex `shell` calls whose session `cwd` is the repo). Each command is split on `&&`, `||`, `;` and `|`; commands with `$(...)`, backticks, redirects to real files, here-documents or `FOO=bar cmd` are skipped whole (`2>&1` and `>/dev/null` are ignored). Each part is matched against our own curated list of safe prefixes (`git status`, `uv run pytest`, `cargo test`, ...); the longest match becomes the pattern, so the rule is always one of our prefixes and never text from the log. Tiers: **safe** (on the list), **review** (run often, not on the list; written only with `--include-review`), **never** (interpreters, wrappers like `timeout`, `rm`, `curl`, `git push`, `docker`, paths; reported, never offered). A command the person refused is left out. A pattern needs 3 uses (`--min-uses`). The output is `Bash(<prefix>:*)` in `permissions.allow` of `.claude/settings.json` (`--local` for settings.local.json), merged into the existing file, other keys untouched, written atomically, and only after `--apply` and a yes (or `--yes`). `--diff` prints the change and writes nothing. A settings file that is not valid JSON is refused. PowerShell commands (Claude's `PowerShell` tool, Codex `shell_command`) are counted and ignored because their rule syntax differs.

- **Commit reading**: one `git log -p -U0 --numstat --no-renames` call (lockfiles, minified bundles, snapshots, SVGs and source maps are excluded at the git level); records and header fields split by control characters. Each commit keeps per-file counts, changed line ranges, the text of added and removed lines (capped at 1000 per file) and a truncated patch. A commit is agent-authored if its body has a Co-Authored-By trailer naming a coding agent (Claude, Codex, Copilot, Cursor, Devin, Aider, OpenHands, Jules, Gemini, Sweep), or its author is a `[bot]` whose name contains one of those, or `noreply@anthropic.com`. Release and dependency bots (dependabot, github-actions, huppy-bot) are not agents.
- **Revert detector**: an agent commit followed within 7 days by a commit that names it in "This reverts commit <sha>" (confidence 1.0), or that removes at least 50% of the significant lines the agent added (matched by line content, each added line matching at most once) in the same files (0.6). The signal's excerpt is the human's change (`- removed` / `+ added`).
- **Re-fix detector**: a human commit within 48 hours that removes lines the agent commit added, where the removed line numbers also overlap the agent's added range in the same file (0.8). Explicit reverts are skipped; one event is one signal (revert and re-fix signals for the same commit pair are deduplicated, highest confidence kept).
- **Session reader**: reads `~/.claude/projects/<slug>/*.jsonl`, keeps only text blocks of user/assistant records, skips sidechains, meta records and malformed lines.
- **Correction detector** (0.5): a user message that follows an assistant turn, is at most 400 characters, is not harness-injected text, and opens with a rejection phrase ("no", "don't", "stop", "instead" ...). Its signal has no commit SHA; the evidence is the session message.
- **Scoring**: detectors emit confidence in `score`; `score_signals` multiplies by a recency weight (half-life 180 days) and drops anything below 0.2. Apply once per run.
- **Embedding**: each signal becomes a short summary (`kind: text (files: ...)`), embedded with `all-MiniLM-L6-v2` (offline after the first model download). Vectors are cached in SQLite keyed by model and SHA-256 of the text, so a rescan only embeds new signals.
- **Clustering**: scikit-learn HDBSCAN, min cluster size 3, first in its strict mode; only if that finds nothing is it retried allowing a single cluster (the lone repeated mistake case). Noise and groups under 3 are dropped. The embedded summary starts with the human's change excerpt, then the commit subjects. A cluster has a deterministic id (hash of its signal ids), a centroid and the text of the signal nearest the centroid as its summary.
- **Cold start**: fewer than 3 clusters gives a plain message suggesting `--commits 2000` or session logs, instead of inventing weak rules.
- **Propose**: per cluster, `build_prompt` sends the fixed instructions as the system message and the top 12 signals as JSON lines inside `<mined-data-RANDOM>` tags (the text can neither guess nor close the tag). Everything is redacted first (key patterns, secret-named assignments, high-entropy tokens; git SHAs are kept). The prompt demands a rule specific to the repository and lets the model decline with an empty rule (dropped as "no specific repeated mistake"). The reply must parse into `{rule, scope, cited_signals}`; anything else drops the cluster.
- **Guards**: a rule keeps only cited signals that belong to its cluster and whose evidence is real (the commit exists in the repo, or it is a session message). Evidence is counted per distinct commit (several fixes of one commit are one item); fewer than 3 means the rule is dropped. Rule text is scanned for URLs, network commands, credentials, env files, new shell commands, disabling tests or permissions, injection phrases and length over 30 words; prohibitions such as "never skip tests" pass. Flagged rules are kept with `risk_flags` set so the UI can show them red and unselected. Scope must be repo-relative globs.
- **Model and cost**: a local Ollama model if one is running, else `ANTHROPIC_API_KEY`, else `OPENAI_API_KEY`, else none. `estimate_cost` approximates tokens (chars / 4) and prices them through LiteLLM; local models are free, unknown models give no price. The scan command (phase 6) must show it and ask before a paid call.
- **Source of truth**: `rulewright.yaml` (version, targets, rules with id, text, scope, status, evidence refs). Evidence is stored as commit SHAs, or signal ids for session messages. `scan` (phase 6) will leave proposals in `.rulewright/pending.json`; nothing reaches the yaml until `apply`.
- **Emit**: only accepted rules are rendered. Content goes between `<!-- rulewright:start -->` and `<!-- rulewright:end -->`: a missing file is created, a file without markers gets the block appended after a blank line, a file with markers has only that span replaced (line endings kept). Broken markers (missing, duplicated, reversed) abort with an error before any file is written. Rule text is flattened to one line and marker strings are stripped from it.
- **Commands**: `init` writes an empty rulewright.yaml. `apply [ids] | --all-safe` moves pending rules into the yaml as accepted and emits the targets; risk-flagged rules need `--allow-risky`. `emit [--dry-run]` re-renders every target (the dry run prints a diff and writes nothing). `stale [--mark]` lists findings and can set those rules to `stale`, which the next `emit` leaves out.
- **Stale check**: for accepted rules, a path-like token that is not a tracked file or directory, a backticked `make X` / `npm run X` whose target or script does not exist, a scope glob matching no tracked file, and (given signals) no signal in the last 90 days that touches the rule's scope or shares two content words with it.
- **Scan** (`rulewright scan`): reads N commits (default 500) and Claude Code session logs, runs the detectors, keeps one signal per (agent commit, later commit) pair, scores, embeds, clusters. Fewer than 3 clusters means the cold-start message and no LLM call. Otherwise up to 10 clusters not already covered by a rule in rulewright.yaml go to the LLM, chosen by `select_clusters`: clusters whose changes share almost no identifiers (coherence under 0.10) are skipped with a note, and the rest are ranked by coherence, then distinct commits; the cost estimate is shown first and a paid call needs a yes (`--yes` skips the question; local models are free and never ask). It prints a Rich table (flagged rules red), writes `rulewright-report.md`, and stores the proposals in `.rulewright/pending.json` (replacing the previous list). It changes no agent file and not rulewright.yaml.
- **`--pr`**: puts the guard-approved rules (no risk flags) on a branch `rulewright/rules-<timestamp>` with rulewright.yaml and the target files, pushes it and opens a pull request with `gh`. The PR is the approval step. It refuses if the files it would edit have uncommitted changes or broken markers, and returns to the original branch. A missing remote or `gh` is reported as a note.
- **Report**: counts, the cold-start message, each rule with scope, risk flags and evidence as commit links (GitHub repos), dropped clusters and stale rules. It contains SHAs and signal kinds, never mined text.
- **Prove** (`rulewright prove <rule-id>`): re-mines the history to find the rule's evidence commits and, for each, the lines the human fix removed (the mistake). A task is the agent commit's parent tree (`git archive`, no history, so the fix is not visible), a prompt taken from the agent commit's message (attribution lines removed) and those bad lines. Up to 10 tasks. For each task and trial it runs two arms on fresh copies: without the rule and with the rule in the agent's system prompt. After a cost estimate (free for local models; a paid run asks first; `--max-cost` stops the run at the ceiling) each run goes: temp copy, Docker container (no network, no environment variables, all capabilities dropped, no-new-privileges, 1 GB memory, 1 CPU, 256 processes, commands killed after 60 s, image `python:3.12-slim`), built-in agent loop, then the judge. The agent loop runs on the host so the API key never enters the container; the container only executes the agent's file and shell actions. File reads and writes go through the container, never host-side, and host snapshots skip symlinks. The judge is deterministic when the evidence shows the mistake (the agent wrote one of the removed lines), otherwise an LLM judge decides. The report shows, per arm, recurrences out of judged runs, runs that changed files (so "did nothing" is not mistaken for "avoided the mistake"), runs that failed (excluded) and cost, and is saved to `.rulewright/prove/<rule-id>.json`.
- **Tests**: `tests/fixtures/build_repos.py` builds throwaway repos with planted patterns and negative cases; transcripts are generated in tmp dirs; tests assert detectors find exactly the planted signals.

## What is used

Python 3.11+, Typer + Rich (CLI), Pydantic v2 (models), PyYAML (rulewright.yaml), git via subprocess, hatchling (build), uv (dev), pytest, ruff, mypy (strict), GitHub Actions on Python 3.11-3.13.
CI also publishes to PyPI on `v*` tags (trusted publishing). Also now: docker (docker-py), sentence-transformers (torch), scikit-learn HDBSCAN, numpy, SQLite. LiteLLM (lazy import).  Planned: SQLite, PyYAML; v0.2 Docker (docker-py).

## Phases

Spec timeline: v0.1 ends week 3 (phases 0-6), v0.2 ends week 7 (phase 7), v0.3 after that (phase 8).
Week 1 = phases 0-1, week 2 = phase 2 plus embeddings/clustering (phase 3), week 3 = phases 4-6.

| Phase | Scope | Status |
|---|---|---|
| 0 Scaffold | repo layout, pyproject, CI, SECURITY.md, stub CLI | done |
| 1 Ingest + revert | git ingest, Signal, revert detector, fixture script | done |
| 2 Detect + score | re-fix detector, Claude Code session reader + correction detector, scoring and threshold done; optional review detector (GitHub token) todo | mostly done |
| 3 Cluster | local embeddings cached in SQLite, HDBSCAN, 3+ signal rule, cold-start message | done |
| 4 Propose + guard | LiteLLM writer with delimited data block, schema-parsed output, risk scanner, secret redaction, SHA verification, cost estimate | done (real LLM path not yet run) |
| 5 Rules + emit | rulewright.yaml, marker-based emitters with golden tests, stale check, `init`, `apply`, `emit`, `stale` | done |
| 6 Ship v0.1 | `scan`, Rich table, rulewright-report.md, `--pr`, injection tests, README; real-repo check on litellm, tldraw (done, found and fixed 4 bugs) | code done; launch gate partly done |

v0.1.0 and v0.1.1 are published on PyPI (tags v0.1.0 and v0.1.1, 2026-11-03). Launch gate (partly done: real scans on litellm with Groq reviewed by hand, four more repos surveyed; see D75, D77): run `scan` on 3 public repos that have agent co-authored commits and read the output by hand; record the GIF; renamed from `rulesmith`, which is taken on PyPI (D79, D80); rename the GitHub repository, then configure the PyPI trusted publisher. Done: LICENSE, package metadata, clean-install check, security contact via GitHub advisories (enable it in repo settings).
| 7 v0.2 prove | rebuild tasks, cost estimate/`--max-cost`, Docker sandbox, AgentRunner, judge, before/after report | code done and tested against real containers; real-model replay in progress (see D70) |
| 8 v0.3 guard | permission profiler, allowlist emitter, `guard --diff`/`--apply`, Codex reader | code done and tested; run on this machine's real logs (see D78) |
