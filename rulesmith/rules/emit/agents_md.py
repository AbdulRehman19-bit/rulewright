from rulesmith.rules.emit.base import GENERATED, Target, bullets
from rulesmith.rules.model import Rule


def render(rules: list[Rule]) -> str:
    return f"## Rules learned from this repo\n{GENERATED}\n\n{bullets(rules)}"


TARGET = Target(name="agents_md", path="AGENTS.md", render=render)
