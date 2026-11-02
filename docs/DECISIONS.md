# Decision log: how and why, step by step

Append a new entry for every meaningful decision, newest last. Format: **Decision** / Why / Alternatives / Revisit if.
Architecture and flow live in [ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 0: Scaffold

**D1. Flat `rulesmith/` package, hatchling backend, dev tools in a `[dependency-groups]` dev group.**
Why: matches the spec's repo layout; hatchling needs no config for a flat package; uv reads dependency groups.
Alternative: `src/` layout (safer against accidental local imports, but diverges from the spec). Revisit if packaging bugs appear.

**D2. Only light dependencies at first (typer, rich, pydantic).**
Why: embeddings, sklearn and LiteLLM are heavy and unused until phases 3-4; keeps install and CI fast now.

**D3. Strict mypy with the pydantic plugin, ruff rules E, F, I, B, UP, line length 100.**
Why: the spec lists mypy and ruff as the quality gate; strict from day one is cheaper than retrofitting.

**D4. Placeholder security contact `security@rulesmith.invalid`.**
Why: the spec asks for a reporting address from day one but none was given. Replace before release.

## Phase 1: Ingest and revert detector

**D5. Read git through `subprocess` and one `git log --numstat` call.**
Why: spec choice (no native build, works wherever git works); a single call is fast for 500 commits.
Parsing: records and fields separated by ASCII 0x1e/0x1f so commit messages cannot break parsing. Numstat lines are told apart from body lines by regex; binary files (`-`) count as 0.

**D6. Agent attribution by trailer or bot name only (for now).**
Why: the cheapest reliable signal in git. Regex matches Co-Authored-By naming claude, codex, copilot or cursor, and `[bot]`/`-bot` authors. Session-log matching is added in phase 2.
Known gap: agents that commit without a trailer are missed.

**D7. Revert detector has two paths with different scores.**
Explicit "This reverts commit" scores 1.0; a later commit deleting at least 50% of the agent commit's added lines (same files) scores 0.6.
Why: explicit reverts are certain, the heuristic is a guess, so it scores lower. The 7-day window and 50% threshold come from the spec ("deletes most of") and are constants to tune against real repos.
Known gap: the deletion heuristic ignores whether the author is the agent, and can match unrelated cleanups.

**D8. Fixtures are built by a script, not committed as repos.**
Why: spec requirement; commit dates are pinned so the time-window tests are deterministic. Each detector gets one positive and at least one negative fixture (late revert, human-commit revert).

**D9. Memory rule: keep these two docs updated as work proceeds.**
Why: requested by the user; ARCHITECTURE.md is the flow/what-is-used doc, DECISIONS.md is the decision trail.

## Phase 2: Detect and score

**D10. Ingest keeps line ranges and a truncated patch (`-p -U0`).**
Why: the re-fix detector needs to know which lines changed, and signals need a `diff_excerpt`. `-U0` keeps the patch small; the patch is capped at 20k characters per commit. `--no-renames` keeps numstat paths simple.
Header and patch are separated by an end marker (0x1d) instead of guessing where the body stops.

**D11. Re-fix compares line numbers directly.**
Why: tracking lines through intermediate commits (blame) is costly and the 48 hour window keeps drift small. Confidence 0.8: below an explicit revert, above a heuristic one.
Known gap: unrelated commits between the agent commit and the fix can shift lines and cause a miss or a false match. Revisit after the real-repo check.

**D12. Detectors emit confidence in `score`; recency weighting and the threshold live in `mine/score.py`.**
Why: the spec says Score weights confidence and recency, and keeping it separate means detectors stay testable with fixed answers. Half-life 180 days and threshold 0.2 are guesses to tune on real repos.
Alternative: a separate `confidence` field (cleaner, but adds a field the spec does not list).

**D13. Corrections are matched by opening phrase, not by an LLM.**
Why: the spec wants ingest/detect/cluster to run locally without an API key. Short messages (at most 400 chars) that start with a rejection phrase, after an assistant turn. The first user message in a session is skipped because it is the task. Harness-injected text and meta records are ignored so hostile or system text is not mined as a human correction.
Known gap: misses polite corrections ("could you use X instead") and may hit false positives such as "no problem".

**D14. Correction signals have an empty `commit_sha`.**
Why: evidence is a session message. The "cite 3+ commits, each SHA verified" guard in phase 4 must therefore count session-message evidence separately instead of requiring a SHA. Revisit when writing the guard.

**D15. Claude Code transcript format confirmed from real local files, not docs.**
Why: the spec says to confirm it before building. JSONL under `~/.claude/projects/<slug>/`, slug is the path with non-alphanumerics replaced by `-`, matched case-insensitively (Windows drive letter). The reader skips anything it does not recognise so format changes degrade quietly; a format version check is still todo (the records carry a `version` field).
Not checked against the official docs; verify before launch.

**D16. CI publishes to PyPI on `v*` tags using trusted publishing, after tests pass.**
Why: the spec requires publish on tags; trusted publishing avoids storing a token. Needs a `pypi` environment and a trusted publisher configured on PyPI before the first tag. CI also now runs only on pushes to main, tags and pull requests, to avoid duplicate runs.

**D17. Placeholder module files for the rest of the spec's layout.**
Why: the spec lists them and imports should not move later. Only `config.py`, `rules/model.py` and `store.py` (just opens a DB) have code.

## Phase 3: Cluster

**D18. Embeddings behind an `Embedder` protocol; tests use a deterministic fake.**
Why: unit tests must not download a model or need a network. The real `SentenceTransformerEmbedder` (all-MiniLM-L6-v2, normalized vectors) was run once by hand and separated two planted patterns of 8 signals each, leaving 2 unrelated signals as noise. It imports sentence-transformers lazily because the import is slow.
Risk: sentence-transformers pulls in torch, a large install that works against the "useful in 2 minutes" goal. If the install is too heavy for pipx users, switch to an ONNX embedder (for example fastembed) behind the same protocol. Revisit before the v0.1 launch.

**D19. Embedding cache keyed by (model, SHA-256 of summary text) in SQLite.**
Why: the spec asks for embeddings cached in SQLite between runs. Keying by text, not signal id, means identical summaries share a vector and a changed summary is re-embedded. All SQL stays in `store.py`.

**D20. Summaries embed `kind`, signal text and up to 5 files, not the diff.**
Why: MiniLM truncates long inputs and diffs are mostly noise. The spec says "what the agent did, what the human changed"; for now the signal text is the commit subjects (agent -> human fix). Revisit if clusters look weak on real repos.

**D21. HDBSCAN with `min_cluster_size=3`, `allow_single_cluster=True`, default `min_samples`.**
Why: 3 is the spec minimum; without `allow_single_cluster` a repo with one dominant mistake yields no cluster at all. I tried `min_samples=1`: it pulled unrelated noise into clusters, so it was dropped. On very small inputs (3-4 points) HDBSCAN can drop a border point and return a cluster of 3 out of 4; that is normal behaviour, so tests use realistic group sizes (8) and assert at least 7 of 8.

**D22. Cluster id is a hash of its sorted signal ids; summary is the member nearest the centroid.**
Why: ids stay stable across runs for the same members, which phase 5 needs to track a rule's history. Choosing a real member as summary keeps the text human-written, not synthesized. The LLM writes the actual rule in phase 4.

**D23. Cold-start message triggers below 3 clusters and suggests a scan four times deeper (at least 2000 commits).**
Why: spec says to suggest `--commits 2000` or session logs and not invent weak rules. The exact multiplier is mine.
