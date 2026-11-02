from rulewright.rules.emit.base import GENERATED, Target, bullets
from rulewright.rules.model import Rule


def render(rules: list[Rule]) -> str:
    return f"## Repository rules\n{GENERATED}\n\n{bullets(rules)}"


TARGET = Target(name="copilot", path=".github/copilot-instructions.md", render=render)
