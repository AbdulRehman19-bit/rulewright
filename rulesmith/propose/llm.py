"""LLM access through LiteLLM: model choice, the call itself, and a cost estimate."""

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel

OLLAMA_URL = "http://localhost:11434"
GROQ_MODEL = "groq/openai/gpt-oss-120b"
REQUEST_TIMEOUT = 120  # seconds; a stuck hosted call should fail, not hang the scan
LOCAL_TIMEOUT = 900  # a 7B model on a CPU needs minutes per call
RETRIES = 3  # rate limits (HTTP 429) are common on free tiers
COMPLETION_TOKENS = 200  # a rule is under 30 words plus a little JSON
CHARS_PER_TOKEN = 4


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


def ollama_models(url: str = OLLAMA_URL, timeout: float = 0.5) -> list[str]:
    """Models served by a local Ollama, or [] when it is not running."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as resp:  # noqa: S310
            return [m["name"] for m in json.load(resp).get("models", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def pick_model(env: Mapping[str, str], local_models: list[str]) -> str | None:
    """Local Ollama model if present, otherwise the user's own API key. None when neither.

    Among keys: Groq first (large open models, fast, has a free tier), then Anthropic, OpenAI.
    `--model` always overrides this.
    """
    if local_models:
        return f"ollama/{local_models[0]}"
    if env.get("GROQ_API_KEY"):
        return GROQ_MODEL
    if env.get("ANTHROPIC_API_KEY"):
        return "anthropic/claude-haiku-5-5"
    if env.get("OPENAI_API_KEY"):
        return "openai/gpt-4o-mini"
    return None


class ChatClient(Protocol):
    """Multi-turn access, for agents. `messages` alternate user and assistant turns."""

    model: str

    def chat(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> str: ...


class LiteLLMClient:
    def __init__(self, model: str) -> None:
        self.model = model

    def chat(
        self, system: str, messages: list[dict[str, str]], max_tokens: int = COMPLETION_TOKENS * 2
    ) -> str:
        import litellm  # slow import, load lazily

        response = litellm.completion(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            temperature=0,
            timeout=LOCAL_TIMEOUT if self.model.startswith("ollama/") else REQUEST_TIMEOUT,
            num_retries=RETRIES,
            max_tokens=max_tokens,
        )
        return str(response.choices[0].message.content or "")

    def complete(self, system: str, user: str) -> str:
        return self.chat(system, [{"role": "user", "content": user}], COMPLETION_TOKENS * 2)


class CostEstimate(BaseModel):
    model: str
    calls: int
    tokens_in: int
    tokens_out: int
    usd: float | None  # None when the model's price is unknown; 0.0 for local models


def litellm_price(model: str, tokens_in: int, tokens_out: int) -> float | None:
    import litellm

    try:
        p_in, p_out = litellm.cost_per_token(
            model=model, prompt_tokens=tokens_in, completion_tokens=tokens_out
        )
    except Exception:  # unknown model; LiteLLM raises plain exceptions here
        return None
    return float(p_in + p_out)


def estimate_cost(
    model: str,
    prompts: list[tuple[str, str]],
    pricer: Callable[[str, int, int], float | None] = litellm_price,
) -> CostEstimate:
    """Estimate before any paid call. Tokens are approximated as characters / 4."""
    tokens_in = sum((len(s) + len(u)) // CHARS_PER_TOKEN for s, u in prompts)
    tokens_out = COMPLETION_TOKENS * len(prompts)
    usd = 0.0 if model.startswith("ollama/") else pricer(model, tokens_in, tokens_out)
    return CostEstimate(
        model=model, calls=len(prompts), tokens_in=tokens_in, tokens_out=tokens_out, usd=usd
    )
