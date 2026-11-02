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

## Phase 4: Propose and guard

**D24. The LLM sits behind an `LLMClient` protocol; tests script its replies.**
Why: the spec requires mocked LLM calls in unit tests and an opt-in real suite. `LiteLLMClient` is the only real implementation and imports LiteLLM lazily. The real path (Ollama or a paid key) has not been exercised yet; do it before launch.

**D25. Prompt injection defence has three layers: delimited data, schema, guards.**
Mined text goes only into the user message inside a tag with a random 16-hex nonce, any occurrence of that tag in the text is stripped, and the system prompt says the block is data. The reply must match a strict schema (extra keys rejected). Even then the rule text is not trusted: it is risk-scanned. The model is told not to mention URLs or commands, but the scanner does not rely on it obeying.

**D26. Risky rules are flagged, not dropped; unevidenced or malformed ones are dropped.**
Why: the spec says flagged rules are shown red and never pre-selected, so the user can still read them. A hostile commit that makes the model emit "run curl ... | sh" therefore ends up visible and flagged. Rules with a broken reply or under 3 verified evidence items cannot be shown honestly and are reported in `dropped` with a reason.

**D27. Negation clears command-like flags only.**
Why: the best rules are prohibitions ("never delete failing tests", "avoid curl"). A forty-character window before the match containing never/don't/avoid/instead of clears network-command, new-shell-command, disables-tests and disables-permissions. URLs, credentials, env files and injection phrases are always flagged. Known gap: a negation word nearby can hide a hostile instruction ("never forget to run curl ..."). It errs towards fewer false alarms; tighten if real repos show abuse.

**D28. Evidence is the cited signals that are in the cluster and verified; at least 3 are required (D14 resolved).**
Why: the model cannot invent evidence, since invented ids are discarded and SHAs are checked with `git cat-file -e sha^{commit}`. Session-message signals (empty SHA) count as evidence. The spec says "commits or comments".
Known gap: a rule citing 2 of 5 signals is dropped even if the cluster is solid. Alternative: fall back to all cluster signals; chosen strict because the cited list is the model's own claim of support.

**D29. Redaction is regex plus entropy.**
Why: the spec names key patterns and high-entropy strings. Entropy threshold 4.0 bits on tokens of 24+ characters containing digits and letters keeps git SHAs readable (hex is about 3.8) and masks random keys. Known gap: redaction is best effort; secrets in odd formats can slip through. Only the first 300/400 characters of text and diff excerpts are sent.

**D30. Model choice: local Ollama, else Anthropic key, else OpenAI key.**
Why: the spec says local Ollama if present, otherwise the user's key. Default models are my choice (`anthropic/claude-haiku-5-5`, `openai/gpt-4o-mini`), cheap and small enough for one-sentence rules. Ollama detection is a 0.5 s request to localhost:11434.

## Phase 5: Rules, emit and stale

**D31. Proposals wait in `.rulesmith/pending.json`; only `apply` touches rulesmith.yaml and agent files.**
Why: the spec says nothing is written without approval, yet `scan` needs to hand its result to `apply` across runs. A local, git-ignored file does that without changing anything committed. Alternative: write proposals into rulesmith.yaml with status `proposed` (the spec's status list allows it); rejected because `scan` would then modify a committed file unasked.

**D32. `apply` takes explicit rule ids, or `--all-safe`; risky rules also need `--allow-risky`.**
Why: the spec says flagged rules are never pre-selected. With no arguments `apply` only lists pending rules, so nothing is accepted by accident. The targets are planned before anything is saved, so a broken marker aborts the whole apply with the pending list intact.

**D33. Evidence in the yaml is a flat list of refs (SHA or signal id), as in the spec's example.**
Why: matches the spec file shape and keeps diffs readable. Cost: reading it back loses which signal a commit came from (`signal_id` is empty for SHA refs); nothing needs it yet.

**D34. Emit is a pure plan step plus an explicit write step.**
Why: `emit --dry-run` and the pre-check in `apply` both need to know what would change without writing. Files are written as bytes, so a CRLF file stays CRLF. Existing files without markers get the block appended, never rewritten, so hand-written content is never touched.

**D35. Broken markers are an error, not a repair.**
Why: with one marker missing or duplicated we cannot know which text is ours. Refusing is the only way to honour "only content between markers is edited". The error names the counts found.

**D36. Rule text is flattened and stripped of marker strings before rendering.**
Why: rule text comes from an LLM that read hostile commits. A rule containing the end marker plus new instructions could otherwise escape its block or add headings. Tested with a rule containing both markers and a fake heading.

**D37. Target files: AGENTS.md, CLAUDE.md, .cursorrules, .github/copilot-instructions.md; default targets exclude copilot.**
Why: spec's example yaml lists `[agents_md, claude_md, cursor]`; copilot is opt-in by adding `copilot` to `targets`. `.cursorrules` is plain text, so it gets no markdown heading. Emitted files do not repeat evidence (it lives in rulesmith.yaml) to save agent tokens.

**D38. Stale heuristics are deliberately cheap and local.**
Dead path: tokens with a slash or a known source extension, URLs removed first, sentence punctuation trimmed. Dead command: only `make` targets and `npm run` scripts can be checked reliably, so other commands are never called stale. Silent rule: the spec says "no matching signal in 90 days", but rules do not store an embedding, so a signal matches when it touches the rule's scope or shares 2+ content words (lowercased, plural s removed).
Known gaps: the silent check can miss a rule whose mistakes are described in different words, and flags every rule when the scanned history is short. Revisit with real repos.

**D39. `.gitattributes` forces LF.**
Why: golden files are compared byte for byte; Windows checkouts with autocrlf would break them. Also removes the CRLF warnings seen on every commit.

**D40. Bugs found while testing, fixed before commit.**
A regex written with an escaped `` produced a backspace character and silently matched nothing (URLs were treated as paths); trailing sentence dots made existing paths look dead; "imports" did not match "import". Each now has a test.

## Phase 6: Ship v0.1

**D41. Revert and re-fix signals are deduplicated per (agent commit, later commit) pair, keeping the higher confidence.**
Found by the new fixture: a human fixing the one line an agent wrote also "deletes most of" that commit, so both detectors fired and one event counted twice (inflating clusters and evidence). Now an event is one signal; re-fix (0.8) beats the deletion heuristic (0.6) and an explicit revert (1.0) beats both. Ids end in `<agent7>-<later7>`, which is what the pair key uses.

**D42. The cluster summary moved inside the data block (injection hole found by a test).**
The prompt had a "Mistake pattern summary" line outside the delimited block, and that text is a signal's commit subject, so a hostile subject escaped the block. The injection test caught it; the summary is now a JSON line inside the block. Lesson kept as a test: no mined text may appear outside the block, and none in the system prompt.

**D43. Fewer than 3 clusters: message only, no LLM call, no rules.**
Why: the spec says to say so plainly "rather than inventing weak rules". Costs nothing, and is the literal reading. Alternative: still propose rules for the 1-2 clusters found (each has 3+ evidence items). Easy to relax in `pipeline.run_scan` if real repos show it is too strict.

**D44. `scan` replaces the pending list each run and skips clusters already covered by a rule.**
Why: pending proposals are cheap to regenerate and stale ones are confusing. "Covered" means a rule in rulesmith.yaml, in any status, has the id derived from the cluster (`r-` plus the cluster hash), so re-running after `apply` proposes nothing new. Verified by hand with a real run. Capped at 10 rules per scan (spec: 5-10).
Known gap: a cluster that gains or loses a member gets a new id and is proposed again as a new rule; matching by similarity would fix it.

**D45. `--pr` opens a pull request with guard-approved rules; the PR is the approval.**
Why: spec says output may be a branch and PR that edits rulesmith.yaml and the target files, and that nothing changes without an approved PR. Flagged rules are never included. It refuses when the files it would touch have uncommitted changes, checks for broken markers before creating any branch, always returns to the starting branch, and the commit message carries no attribution trailer. `gh` is injected so tests use a fake. Note: on this machine `gh` is an unrelated Python package (v0.0.4), not the GitHub CLI; its failure is reported as a note and the pushed branch is kept.

**D46. Real end-to-end run with Ollama (closes the gap in D24).**
On a demo repo with three planted mistakes (45 commits), the real MiniLM embedder gave 3 clusters and `qwen2.5-coder:7b` produced 3 rules with 5 evidence items each, correct scopes and no flags; `apply --all-safe` wrote all three targets; a second `scan` proposed nothing. Quality notes: rule wording was literal and sometimes long ("... in files within the 'imports' directory"); the 30-word limit is enforced only as a flag, not by rewriting. This was a synthetic repo, not a public one, so it does not satisfy the launch gate.

**D47. The suite takes about 2 minutes.**
Why: fixture repos are real git repos with 45+ commits and git on Windows is slow. Repos are built once per module and shared (read-only). Revisit (smaller fixtures, or `git fast-import`) if it slows development.

**D48. Not done: marking commits agent-authored from matching session logs.**
The spec's ingest step lists "co-author trailers, bot authors, or matching session logs". Only the first two are implemented; agents that commit without a trailer are missed. Correction signals from session logs are mined separately.

**D49. README is honest about its state.**
The spec wants a GIF and a real rule from a public repo; neither exists yet. The README says pre-release, installs from the GitHub URL, labels its example as a demo repo, and leaves the GIF as a hidden TODO.

## Phase 6b: Real-repo check (BerriAI/litellm, tldraw/tldraw, pydantic/pydantic-ai)

Repos were found with the GitHub commit search ("Co-Authored-By: Claude"; litellm 1432 hits, pydantic-ai 379, tldraw 244), shallow-cloned and scanned with the real embedder, with no LLM first. The spec's gate is to read the output by hand; doing so found four bugs that no fixture had.

**D50. Revert and re-fix matching is by line content, not by deletion counts.**
Found: on litellm, 74 agent commits produced 123 signals, 24 from one commit. Cause: the revert heuristic counted deleted lines anywhere in a file the agent touched, and a shared data file (model prices JSON) is edited by everyone. Now a later commit counts only if it removes lines the agent added (each matched at most once; short or punctuation-only lines ignored; lockfiles and generated files skipped). Signals fell to 20, and the regression is a test (`build_busy_file`).

**D51. A bot author is an agent only if it names a coding agent.**
Found: tldraw's "revert" signals were `Bump versions [skip ci]` commits by a release bot, because any `[bot]` author counted. Now: trailer naming Claude, Codex, Copilot, Cursor, Devin, Aider, OpenHands, Jules, Gemini or Sweep, or a bot author containing one of those, or `noreply@anthropic.com`. litellm's 56 `devin-ai-integration[bot]` commits are correctly agents; dependabot, github-actions and huppy-bot are not. A human named Claude is not an agent.

**D52. Detector speed: index each agent commit once.**
Found: scanning 500 litellm commits took over 6 minutes; `detect_reverts` alone 360 s, because the first content-matching version rebuilt a line counter for every (later, agent) pair. Now it is built once per agent commit with a quick reject before any copying: 2.8 s. Reading 500 commits is 15-20 s (`git log -p`, 70 MB on litellm); 2000 commits is about 2.5 minutes in total with the embedder.

**D53. HDBSCAN runs strict first; single-cluster mode is only a retry.**
Found: with `allow_single_cluster=True` always, 163 of litellm's 257 signals landed in one cluster, which would make one useless rule. Strict mode gave 19 distinct clusters. The retry keeps the case of one repeated mistake working (fixture with six repeats).

**D54. The summary that gets embedded now leads with the human's change.**
Commit subjects of a busy repository are all different, so subject-only embeddings could not group anything. The excerpt (`- from app.settings import X` / `+ from app.config import X`) is in `Signal.diff_excerpt` and first in the embedded text.

**D55. What the real-repo check says about quality (not fixed, to be judged by the user).**
Read by hand, litellm's 19 clusters group by area of churn (a feature under heavy agent work, a Rust refactor, MCP, model-price rows), not by one repeated mistake, because in a high-velocity repo most "an agent line changed again within 48 hours" events are normal iteration. The clearest real pattern was model-price rows missing capability flags. The 3-evidence minimum and the cold-start message keep weak repos from producing rules, but whether the rules are valuable is a judgment the user makes from the evidence links, which is why every rule shows them. A trailer-less agent is invisible (D48), and longer history (`--commits 2000`) finds more clusters.

**D56. `scan` shows progress while the LLM works.**
Found: five rules from a 7B model on CPU took 13 minutes with nothing on screen. The status line now reads "Writing rule i of n with the LLM (a local model can be slow)". There is no timeout or parallelism yet.

**D57. Evidence counts distinct commits, not signals; clusters need 3 distinct commits.**
Found: litellm rules listed the same commit up to four times, because one agent commit with several follow-up fixes yields several signals. "Cites 3+ commits" was therefore not true. Now a cluster needs 3 distinct commits (session messages count individually), and a rule's evidence keeps one signal per commit; fewer than 3 distinct drops the rule with the reason "only N distinct verified evidence items".

**D58. The model may decline: an empty rule means "no specific repeated mistake".**
Found: a local 7B model wrote generic rules from real clusters ("Import necessary modules at the beginning of each file", "Ensure consistent formatting and structure in JSON files"). The prompt now requires a rule specific to the repository (a module, function, file pattern or practice from the data), forbids generic advice, and allows `{"rule": ""}`, which is reported as a dropped cluster. A heuristic "vague" flag was considered and rejected: it would flag good rules such as "Do not skip failing pytest tests". The user still judges from the evidence links. Prompt quality depends on the model; a small local model abstains less than a larger one.

**D59. Result of the second real LLM run (litellm, 2000 commits, `qwen2.5-coder:7b`, top 3 of 15 clusters).**
Evidence is now correct: every cited commit is distinct (9, 10 and 6 commits). But the 7B model still wrote generic rules ("Import necessary modules at the top of each file", "Ensure all imports are grouped and sorted alphabetically within each file", "Ensure consistent formatting of cost-related data in JSON files") and did not abstain. So on a busy real repository the mining and clustering are usable, but rule quality is limited by the model, and this model is not good enough for launch-quality output. Not tested: a larger model (no API key was available in this session). Before launch, run `--model` with a stronger model on three public repos and keep a rule worth showing in the README; until then the README example is from a demo repo.
Also not done: pydantic-ai was cloned but not scanned (only 8 Claude trailers in its last 500 commits); the check covered litellm and tldraw. tldraw at 2000 commits gave 3 distinct-commit clusters at most, so it falls under cold start for LLM purposes.

## Phase 6c: Rule quality and Groq

**D60. Groq is supported through LiteLLM; auto-selection prefers it among API keys.**
`GROQ_API_KEY` selects `groq/llama-3.3-70b-versatile`. Order: a running local Ollama, then Groq, Anthropic, OpenAI; `--model` always wins. Groq goes first among keys because the 70B open model is far stronger than the local 7B that produced generic rules (D59) and has a free tier, but the cost estimate is still shown first and any non-free model asks before running. Calls now retry 3 times (free tiers return rate-limit errors) and time out after 120 s (local models 900 s, since a 7B on CPU needs minutes per call). Not verified against the real Groq API: no key was available in the session; the unit tests cover selection and pricing only.

**D61. A second "judge" call drops generic rules (`--judge`, on by default).**
Why: the first prompt asks for specific rules but a weak model ignores that. A separate call sees the proposed rule and the evidence and answers whether the rule names something concrete from the evidence that would not apply to most codebases. Generic rules are dropped with the reason "judged generic: ...". It doubles the calls and is included in the cost estimate. Fail-open: an unreadable or failed judge keeps the rule, because a flaky judge should not discard evidence-backed rules. The rule text goes into the judge's data block too, since it is model output derived from hostile text. `--no-judge` skips it.

**D62. The prompt now leads each item with `human_fix`.**
The removed and added lines are the mistake itself; the model is told to base the rule on what the fixes have in common (this replaces the old `diff_excerpt` key).

**D63. A failed LLM call drops one cluster, never the scan.**
Found by a real run: the new 180 s timeout hit the local 7B model, the exception was not caught, and `scan` crashed with a traceback after minutes of work. Now a timeout, rate limit or auth error is reported as "LLM call failed (TimeoutError): ..." for that cluster and the rest continue; a judge failure keeps the rule.

## Phase 7: v0.2 `prove`

**D64. The agent loop runs on the host; only its tool calls run in the container.**
Why: the spec says replays run with the network off (or limited to the model API) and no host secrets. With a host-side loop the container can have no network at all and the API key never enters it. Cost: the agent cannot reach package registries, so it cannot install dependencies.

**D65. File access goes through the container; the host only snapshots regular files.**
An agent can create a symlink in the bind-mounted copy. Reading or writing through the host would follow it to host paths, so reads use `head` in the container, writes use a tar archive into the container, and the judge's snapshot skips symlinks, `.git`, binary and huge files. The old tree is extracted with the tar `data` filter, which refuses absolute paths and links outside the destination. Container limits: no network, no environment variables, all capabilities dropped, no-new-privileges, 1 GB, 1 CPU, 256 processes, commands killed after 60 s, container removed after each run. Each of these has a test against a real container (network blocked, secrets absent, capabilities zero, runaway killed, memory cap, container removed).

**D66. Tasks are rebuilt from evidence commits: parent tree, commit message, known-bad lines.**
The agent gets only the parent's files (`git archive`, no history) so the fix is not visible. The prompt is the agent commit's message minus attribution lines (the spec also lists linked issues and the first session message; not implemented). The mistake is the set of lines the human fix removed, re-mined from history, so `prove` needs the evidence commits to be within `--commits` (default 2000). Honest limit: a commit message can describe the solution, and a reconstructed prompt is not the original.

**D67. The judge is deterministic first, an LLM second, and "did nothing" is shown.**
A run recurred if the agent wrote one of the removed lines (whitespace ignored; lines under 4 characters are too weak to identify anything). With no known bad lines an LLM judge reads the rule, the task and the added lines. Runs that failed (model error, timeout) are excluded from the rates, and each arm shows how many runs changed any file, because an agent that does nothing "avoids" the mistake trivially.

**D68. Cost: an estimate before, a ceiling during.**
Estimate = runs x 8 assumed steps x 3000 tokens in and 250 out, priced through LiteLLM (free for Ollama, unknown price shown as such). `--max-cost` is checked before each run, so it can be overshot by at most one run. A paid run asks first; `--yes` skips the question.

**D69. Default sandbox image is `python:3.12-slim`; `--image` overrides it.**
It has `sh`, `timeout` and Python and is small, but not the repository's own toolchain, and there is no network to install one. So the agent can edit files and run basic commands, but not the project's tests unless the image already has what they need. The built-in agent is simple on purpose (JSON actions as text, no native tool calling) so any model works; stronger agents such as Claude Code headless would plug in behind `AgentRunner` and are not implemented.

**D70. First real replay (demo repo, `qwen2.5-coder:7b` as the agent, real Docker, 1 task, 1 trial).**
Both arms finished in 4 steps. Recurrence was 0/1 without the rule and 0/1 with it, so nothing can be concluded from it; the useful signal was in the other row: the without-rule run changed files and the with-rule run changed none, which is exactly the "agent did nothing" case D67 reports separately. What it shows: the whole chain works with a real model (task rebuild, container, JSON-action loop, judge, report). What it does not show: that a rule helps. That needs more tasks and trials, a stronger agent model, and real repositories; it was not done in this session.

**D71. The scan spinner no longer covers the paid-call prompt, and it names its stage.**
The confirmation ran inside `console.status`, so with a priced model (Groq, Anthropic, OpenAI) the "Proceed?" prompt was hidden behind a spinner reading "Reading history and clustering mistakes..." and the scan looked frozen. `confirm` now stops the spinner, asks, and restarts it, and `run_scan` takes a `stage` callback so the spinner shows reading, detecting, and embedding/clustering in turn. Alternatives: always pass `--yes` (hides the cost check). Revisit if more prompts are added: any prompt must run with the spinner stopped.

**D72. `tenacity` is a declared dependency.**
litellm imports it lazily when `num_retries` is set and does not require it, so every hosted-model call failed with "tenacity import failed" on a clean install (found on the first real Groq scan; Ollama runs had it installed by chance). Declared it. Alternative: do our own retry loop and drop `num_retries`; not worth it for one small package. Revisit if litellm starts declaring it.

**D73. Groq default is `groq/openai/gpt-oss-120b`.**
The first real Groq scan failed with "model `llama-3.3-70b-versatile` does not exist or you do not have access": Groq retired it for non-enterprise accounts on 2026-08-16 and recommends `openai/gpt-oss-120b`. D63 chose the old id from memory, which was the mistake; model ids age quickly. Switched the default; `--model` still overrides, and the hosted-model failure message already names the model. Alternative: list models from the Groq API at run time and pick one; more moving parts than a documented default. Revisit when Groq deprecates this one (check console.groq.com/docs/deprecations).

**D74. Reasoning models and free-tier rate limits (found on the first working Groq scan).**
`openai/gpt-oss-120b` thinks before it answers and the thinking counts against `max_tokens`, so 400 tokens left an empty or cut-off JSON reply ("did not fit the schema"). For `gpt-oss` models the client now sets `reasoning_effort=low` and allows 2000 tokens. Separately, the free tier caps tokens per minute and 20 quick calls hit HTTP 429 even with litellm retries, so the client waits (the server hint "try again in Xs", else 10s x attempt, at most 75s) up to 6 attempts before giving up on that cluster. A scan on a free tier is therefore slow (several minutes) rather than failing. Alternatives: fewer calls (`--no-judge` halves them), a fixed delay between all calls (slows paid users too). Revisit if Groq changes limits or the hint format.

**D75. First good Groq scan on litellm (gpt-oss-120b, 2000 commits): the verdict.**
15 clusters gave 4 rules, 3 clusters were judged generic, 3 had fewer than 3 distinct verified commits. By hand: two rules are specific and useful (do not add duplicate keys in `model_prices_and_context_window.json`; append to `pytestmark` instead of overwriting it), one is vague (never remove imports/constants/SQL in the lens modules) and one slipped past the judge as generic (update imports after refactoring). So roughly half of what survives is launch-quality; the output is a shortlist for a human, which is what the tool promises (nothing is applied without approval). Open: the judge is too lenient on "keep imports consistent" style rules; a stricter judge prompt or a second opinion is the next quality lever. Also set `litellm.suppress_debug_info` to hide its per-retry banner.
