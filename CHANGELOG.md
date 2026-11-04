# Changelog

## 0.1.2

- Fixed a crash when a commit message contains certain control characters, and a silent loss of
  signals for files with non-English names. A message in another text encoding no longer stops
  a scan.
- Damaged or hand-edited files now give one plain sentence: `rulewright.yaml` (line and column,
  or which rule and field), `.rulewright/config.toml`, and a damaged `.rulewright/pending.json`.
- A corrupt `.rulewright/cache.db` is replaced. A read-only checkout works: the report and the
  proposals are skipped with a warning and the results are still shown.
- `guard` reads a `.claude/settings.json` that starts with a byte-order mark. `emit` refuses a
  target file that is not UTF-8 and leaves it untouched.
- README: the tagline no longer says "provably".

## 0.1.1

- A folder that is not a git repository, a repository with no commits and a missing path now
  give one plain sentence instead of a raw git error. `--commits` must be 1 or more.
- `stale` reads the history once instead of twice.
- When the judge drops a cluster as generic, the message now shows the dropped rule, so you can
  see and recover it.
- README: demo GIF, and it now says what `scan` leaves in the repository (`rulewright-report.md`
  and a `.rulewright/` cache folder).

## 0.1.0

First release.

- `scan`: reads git history (and Claude Code session logs), finds reverts, quick human fixes of
  agent commits and user corrections, clusters them, and proposes rules that each cite at least
  3 distinct commits or messages. Clusters are ranked by how much their changes have in common
  before any LLM call. Changes no file.
- `apply`, `emit`, `init`, `stale`: accept proposed rules into `rulewright.yaml` and render
  `AGENTS.md`, `CLAUDE.md`, `.cursorrules` and Copilot instructions between markers; find rules
  that mention paths or commands that no longer exist. `scan --pr` opens a pull request.
- `prove`: replay past tasks in a Docker sandbox (no network, no secrets) with and without a
  rule and report how often the mistake recurs. Evidence, not statistical proof.
- `guard`: propose Claude Code `permissions.allow` rules from the commands agents ran, limited
  to a built-in list of read-only, test, lint and build commands. Reads Claude Code and Codex
  logs; `--diff` and `--apply`.
- LLM: a local Ollama model, or your own Groq, Anthropic or OpenAI key (any LiteLLM model name).
  The cost is shown and a paid call needs a yes.

Known limits: git history alone gives few or no rules on many repositories; Codex-authored
commits without a trailer are not recognised; PowerShell commands are ignored by `guard`.
