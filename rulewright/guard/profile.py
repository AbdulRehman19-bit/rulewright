"""Turn the shell commands an agent ran into a minimal, safe permission allowlist.

No LLM is involved and no command is executed. A pattern only reaches the "safe" tier by being
one of our own curated prefixes (SAFE_PREFIXES), so text from a log can never become an allow
rule on its own. Everything else that was run often is reported as "review" (a person decides)
or "never" (too broad or too dangerous to offer at all).
"""

import re
import shlex
from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from rulesmith.ingest.sessions.base import ToolCall
from rulesmith.propose.redact import redact

Tier = Literal["safe", "review", "never"]

MIN_USES = 3
# Tools whose commands are POSIX shell, i.e. what a `Bash(...)` rule matches. Codex's
# `shell_command` is PowerShell on Windows, so it is left out and reported instead.
BASH_TOOLS = {"Bash", "shell", "exec_command", "local_shell"}

# Read-only, test, lint and build commands. Longest match wins; nothing here can delete,
# publish, install or reach the network on its own.
SAFE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pwd",), ("ls",), ("cd",), ("wc",),
    ("git", "status"), ("git", "diff"), ("git", "log"), ("git", "show"),
    ("git", "rev-parse"), ("git", "ls-files"), ("git", "blame"),
    ("pytest",), ("python", "-m", "pytest"), ("uv", "run", "pytest"),
    ("ruff", "check"), ("uv", "run", "ruff", "check"),
    ("mypy",), ("uv", "run", "mypy"),
    ("npm", "test"), ("npm", "run", "test"), ("npm", "run", "lint"),
    ("npm", "run", "typecheck"), ("npm", "run", "build"),
    ("pnpm", "test"), ("pnpm", "run", "test"), ("pnpm", "run", "lint"),
    ("pnpm", "run", "typecheck"), ("pnpm", "run", "build"),
    ("yarn", "test"), ("yarn", "lint"), ("yarn", "build"),
    ("cargo", "test"), ("cargo", "check"), ("cargo", "clippy"), ("cargo", "build"),
    ("go", "test"), ("go", "vet"), ("go", "build"),
    ("make", "test"), ("make", "lint"),
)  # fmt: skip

# A rule for these would allow arbitrary code or arbitrary deletion, whatever follows.
NEVER_PROGRAMS = {
    "python", "python3", "py", "node", "deno", "bun", "ruby", "perl", "php", "bash", "sh", "zsh",
    "pwsh", "powershell", "cmd", "npx", "uvx", "pipx", "eval", "exec", "xargs", "env", "find",
    "awk", "sed", "rm", "rmdir", "sudo", "su", "chmod", "chown", "dd", "mkfs", "kill", "pkill",
    "curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "ftp", "docker", "podman", "kubectl",
    "terraform", "mv", "cp", "tee", "xdg-open", "open", "start",
    # wrappers: allowing `timeout` would allow `timeout 5 rm -rf x`
    "timeout", "nice", "nohup", "time", "command", "builtin", "stdbuf", "watch", "setsid", "doas",
}  # fmt: skip
NEVER_PAIRS = {
    ("git", "push"), ("git", "reset"), ("git", "clean"), ("git", "checkout"), ("git", "rebase"),
    ("git", "restore"), ("git", "stash"), ("git", "branch"), ("git", "config"), ("git", "remote"),
    ("npm", "install"), ("npm", "publish"), ("npm", "exec"), ("pip", "install"),
    ("uv", "run"), ("uv", "add"), ("uv", "publish"), ("gh", "api"), ("gh", "auth"),
}  # fmt: skip

# Programs whose first argument is a subcommand; for any other, an argument is a file or a value.
SUBCOMMAND_PROGRAMS = {
    "git", "npm", "pnpm", "yarn", "cargo", "go", "gh", "make", "uv", "pip", "pip3", "poetry",
    "brew", "apt", "apt-get", "systemctl", "dotnet", "mvn", "gradle", "bundle", "rake",
}  # fmt: skip
# Redirects that cannot write anywhere: stderr into stdout, or output thrown away.
_HARMLESS_REDIRECT = re.compile(r"\s\d?>&\d(?=\s|$)|\s\d?>>?\s*/dev/null(?=\s|$)")
_OPERATORS = {";", "&&", "||", "|", "&", "|&"}
_WORD = re.compile(r"^[A-Za-z][\w.+-]*$")


class Pattern(BaseModel):
    pattern: str  # e.g. "git status"; the rule is `Bash(git status:*)`
    tier: Tier
    uses: int
    sessions: int
    failures: int
    denied: int
    last_used: datetime
    example: str  # one redacted, shortened command


class Profile(BaseModel):
    commands_seen: int
    skipped_other_shell: int  # PowerShell and the like: not matched by a Bash(...) rule
    patterns: list[Pattern]

    def tier(self, tier: Tier) -> list[Pattern]:
        return [p for p in self.patterns if p.tier == tier]


def split_command(command: str) -> list[list[str]] | None:
    """Simple commands joined by &&, ||, ; or |. None when it cannot be judged safely:
    unbalanced quotes, command substitution, redirects, here-documents, env assignments."""
    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        return None
    command = _HARMLESS_REDIRECT.sub("", command)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    parts: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _OPERATORS:
            parts.append([])
        elif set(tok) <= set("<>&|;()") and tok:
            return None  # a redirect, subshell or odd operator
        else:
            parts[-1].append(tok)
    parts = [p for p in parts if p]
    if not parts or any("=" in p[0] and not p[0].startswith("-") for p in parts):
        return None  # empty, or FOO=bar cmd: the environment changes what the command does
    return parts


def classify(tokens: list[str]) -> tuple[str, Tier]:
    """(pattern, tier) for one simple command."""
    best = max(
        (p for p in SAFE_PREFIXES if tuple(tokens[: len(p)]) == p), key=len, default=None
    )
    if best is not None:
        return " ".join(best), "safe"
    program = tokens[0]
    if "/" in program or "\\" in program or not _WORD.match(program):
        return program, "never"  # a path or odd name: not something to allow by prefix
    takes_sub = program in SUBCOMMAND_PROGRAMS and len(tokens) > 1 and _WORD.match(tokens[1])
    sub = tokens[1] if takes_sub else ""
    pattern = f"{program} {sub}".strip()
    if program.lower() in NEVER_PROGRAMS or (program, sub) in NEVER_PAIRS:
        return pattern, "never"
    return pattern, "review"


def build_profile(calls: list[ToolCall], min_uses: int = MIN_USES) -> Profile:
    usable = [c for c in calls if c.tool in BASH_TOOLS]
    seen: dict[tuple[str, Tier], list[tuple[ToolCall, str]]] = defaultdict(list)
    for call in usable:
        parts = split_command(call.command)
        if parts is None:
            continue
        for tokens in parts:
            pattern, tier = classify(tokens)
            seen[(pattern, tier)].append((call, " ".join(tokens)))
    patterns = []
    for (pattern, tier), hits in seen.items():
        if len(hits) < min_uses:
            continue
        calls_for = [c for c, _ in hits]
        patterns.append(
            Pattern(
                pattern=pattern, tier=tier, uses=len(hits),
                sessions=len({c.session_id for c in calls_for}),
                failures=sum(c.failed for c in calls_for),
                denied=sum(c.denied for c in calls_for),
                last_used=max(c.ts for c in calls_for),
                example=redact(hits[-1][1])[:100],
            )
        )  # fmt: skip
    patterns.sort(key=lambda p: (-p.uses, p.pattern))
    return Profile(
        commands_seen=len(usable), skipped_other_shell=len(calls) - len(usable), patterns=patterns
    )


def allowable(profile: Profile, include_review: bool = False) -> list[Pattern]:
    """Patterns that may be written: never one the person refused, never the 'never' tier."""
    tiers: set[Tier] = {"safe", "review"} if include_review else {"safe"}
    return [p for p in profile.patterns if p.tier in tiers and p.denied == 0]
