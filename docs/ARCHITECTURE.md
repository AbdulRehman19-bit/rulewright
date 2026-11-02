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
   -> rules    rulesmith.yaml (single source of truth) -> emit to AGENTS.md, CLAUDE.md, .cursorrules, copilot file
```
Ingest, mine and cluster run locally with no API key. Only propose calls an LLM.
Nothing is written to an agent file without approval, and only between the rulesmith markers.

## Modules (package `rulesmith/`)

| Module | Job | State |
|---|---|---|
| `cli.py` | Typer app: scan, apply, emit, stale, init | stub `scan` |
| `config.py` | `.rulesmith/config.toml` with defaults | done |
| `ingest/git_history.py` | `git log` to `Commit` with line ranges and patch, agent attribution | done |
| `ingest/sessions/base.py`, `claude_code.py` | `SessionSource` interface, Claude Code transcript reader | done |
| `ingest/reviews.py`, `sessions/codex.py` | PR review comments (needs token), Codex reader | placeholder |
| `mine/signals.py` | `Signal` model | done |
| `mine/detectors.py` | revert, re-fix, correction detectors | done; review detector todo |
| `mine/score.py` | recency weighting and threshold | done |
| `cluster/embed.py` | `Embedder` interface, sentence-transformers embedder, SQLite embedding cache | done |
| `cluster/group.py` | HDBSCAN grouping, `Cluster` model, cold-start message | done |
| `propose/redact.py` | secret redaction before any API call | done |
| `propose/guards.py` | risk scanner, scope cleaning, evidence verification | done |
| `propose/llm.py` | model choice (Ollama, else user key), LiteLLM client, cost estimate | done |
| `propose/writer.py` | prompt with delimited data block, schema parsing, `propose_rules` | done |
| `rules/model.py` | `Rule`, `Evidence` | done |
| `rules/source.py`, `stale.py`, `emit/*` | rulesmith.yaml, stale check, per-format writers | placeholder files |
| `prove/`, `guard/` | v0.2 replay, v0.3 permission learning | reserved |
| `store.py` | SQLite cache in `.rulesmith/` (embeddings table) | embeddings done; signals and rule history later |

## How the finished pieces work

- **Commit reading**: one `git log -p -U0 --numstat --no-renames` call; records and header fields split by control characters. Each commit keeps per-file added/deleted counts, the changed line ranges (old and new side) and a truncated patch. A commit is agent-authored if the body has a Co-Authored-By trailer naming Claude/Codex/Copilot/Cursor, or the author looks like a bot.
- **Revert detector**: an agent commit followed within 7 days by a commit that names it in "This reverts commit <sha>" (confidence 1.0) or deletes at least 50% of its added lines in the same files (0.6).
- **Re-fix detector**: a human commit within 48 hours whose removed/replaced lines overlap lines the agent commit added or changed in the same file (0.8). Explicit reverts are skipped so nothing is double counted.
- **Session reader**: reads `~/.claude/projects/<slug>/*.jsonl`, keeps only text blocks of user/assistant records, skips sidechains, meta records and malformed lines.
- **Correction detector** (0.5): a user message that follows an assistant turn, is at most 400 characters, is not harness-injected text, and opens with a rejection phrase ("no", "don't", "stop", "instead" ...). Its signal has no commit SHA; the evidence is the session message.
- **Scoring**: detectors emit confidence in `score`; `score_signals` multiplies by a recency weight (half-life 180 days) and drops anything below 0.2. Apply once per run.
- **Embedding**: each signal becomes a short summary (`kind: text (files: ...)`), embedded with `all-MiniLM-L6-v2` (offline after the first model download). Vectors are cached in SQLite keyed by model and SHA-256 of the text, so a rescan only embeds new signals.
- **Clustering**: scikit-learn HDBSCAN (min cluster size 3, single cluster allowed). Noise and groups under 3 are dropped. A cluster has a deterministic id (hash of its signal ids), a centroid and the text of the signal nearest the centroid as its summary.
- **Cold start**: fewer than 3 clusters gives a plain message suggesting `--commits 2000` or session logs, instead of inventing weak rules.
- **Propose**: per cluster, `build_prompt` sends the fixed instructions as the system message and the top 12 signals as JSON lines inside `<mined-data-RANDOM>` tags (the text can neither guess nor close the tag). Everything is redacted first (key patterns, secret-named assignments, high-entropy tokens; git SHAs are kept). The reply must parse into `{rule, scope, cited_signals}`; anything else drops the cluster.
- **Guards**: a rule keeps only cited signals that belong to its cluster and whose evidence is real (the commit exists in the repo, or it is a session message). Fewer than 3 means the rule is dropped. Rule text is scanned for URLs, network commands, credentials, env files, new shell commands, disabling tests or permissions, injection phrases and length over 30 words; prohibitions such as "never skip tests" pass. Flagged rules are kept with `risk_flags` set so the UI can show them red and unselected. Scope must be repo-relative globs.
- **Model and cost**: a local Ollama model if one is running, else `ANTHROPIC_API_KEY`, else `OPENAI_API_KEY`, else none. `estimate_cost` approximates tokens (chars / 4) and prices them through LiteLLM; local models are free, unknown models give no price. The scan command (phase 6) must show it and ask before a paid call.
- **Tests**: `tests/fixtures/build_repos.py` builds throwaway repos with planted patterns and negative cases; transcripts are generated in tmp dirs; tests assert detectors find exactly the planted signals.

## What is used

Python 3.11+, Typer + Rich (CLI), Pydantic v2 (models), git via subprocess, hatchling (build), uv (dev), pytest, ruff, mypy (strict), GitHub Actions on Python 3.11-3.13.
CI also publishes to PyPI on `v*` tags (trusted publishing). Also now: sentence-transformers (torch), scikit-learn HDBSCAN, numpy, SQLite. LiteLLM (lazy import).  Planned: SQLite, PyYAML; v0.2 Docker (docker-py).

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
| 5 Rules + emit | rulesmith.yaml, marker-based emitters with golden tests, stale check, `apply`, `emit`, `stale` | todo |
| 6 Ship v0.1 | Rich table, rulesmith-report.md, `--pr`, injection tests, run on 3 public repos, README + GIF, PyPI | todo (launch gate: useful on 3 real repos) |
| 7 v0.2 prove | rebuild tasks, cost estimate/`--max-cost`, Docker sandbox, AgentRunner, judge, before/after report | later |
| 8 v0.3 guard | permission profiler, allowlist emitter, `guard --diff`, Codex reader | later |
