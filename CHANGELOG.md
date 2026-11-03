# Changelog

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
