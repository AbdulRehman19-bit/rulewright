from rulewright.rules.emit.base import GENERATED, Target, bullets
from rulewright.rules.model import Rule


def render(rules: list[Rule]) -> str:
    return f"## Rules learned from this repo\n{GENERATED}\n\n{bullets(rules)}"


TARGET = Target(name="claude_md", path="CLAUDE.md", render=render)
