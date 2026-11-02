import pytest

from rulesmith.ingest.git_history import is_agent_commit


@pytest.mark.parametrize(
    ("name", "email", "body"),
    [
        ("Dev", "dev@example.com", "msg\n\nCo-Authored-By: Claude <noreply@anthropic.com>"),
        ("Dev", "dev@example.com", "Co-authored-by: aider (gpt-4o) <noreply@aider.chat>"),
        ("devin-ai-integration[bot]", "1+devin-ai-integration[bot]@users.noreply.github.com", ""),
        ("copilot-swe-agent[bot]", "198982749+Copilot@users.noreply.github.com", ""),
        ("Copilot", "198982749+Copilot@users.noreply.github.com", ""),
        ("Claude", "noreply@anthropic.com", ""),
    ],
)
def test_coding_agents_are_recognised(name: str, email: str, body: str) -> None:
    assert is_agent_commit(name, email, body)


@pytest.mark.parametrize(
    ("name", "email", "body"),
    [
        ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com", ""),
        ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com", ""),
        ("huppy-bot[bot]", "1+huppy-bot[bot]@users.noreply.github.com", "Bump versions"),
        ("Claude Dupont", "claude@dupont.example", "a human who happens to be named Claude"),
        ("Dev", "dev@example.com", "Co-authored-by: Alice <alice@example.com>"),
    ],
)
def test_release_bots_and_humans_are_not_agents(name: str, email: str, body: str) -> None:
    assert not is_agent_commit(name, email, body)
