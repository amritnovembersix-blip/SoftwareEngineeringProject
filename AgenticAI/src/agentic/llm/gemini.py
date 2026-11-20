"""Gemini Adapters: adapt the google-genai SDK (Adaptee) to ChatModel / Embedder (Targets)."""
import math
import time
from typing import Literal

from google import genai
from google.genai import types

from agentic.llm.base import T, Usage, check_reasoning
from agentic.llm.ratelimit import MAX_RETRY_AFTER, RateLimiter, call_with_retries

_EMBED_BATCH = 100

# Reasoning level -> thinking budget in tokens, for models before Gemini 3 (gemini-3* take a level instead).
_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}
_THINKING_LEVEL = {"low": types.ThinkingLevel.LOW, "medium": types.ThinkingLevel.MEDIUM,
                   "high": types.ThinkingLevel.HIGH}


def thinking_config(model: str, reasoning: str) -> types.ThinkingConfig | None:
    """None for "default" (the model's own setting). Gemini 3 takes a `thinking_level`; its SDK enum has
    LOW, MEDIUM and HIGH (some 3.x models accept only LOW and HIGH and the API would refuse MEDIUM).
    Earlier models take a `thinking_budget`."""
    if reasoning == "default":
        return None
    if model.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level=_THINKING_LEVEL[reasoning])
    return types.ThinkingConfig(thinking_budget=_THINKING_BUDGET[reasoning])


def is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    return isinstance(code, int) and not isinstance(code, bool) and (code == 429 or code >= 500)


def retry_after_seconds(exc: Exception) -> float | None:
    """The server's Retry-After hint (seconds), when the error's HTTP response carries one, clamped to
    0..MAX_RETRY_AFTER."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        seconds = float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None
    if seconds is None or math.isnan(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)



def _http_options(timeout_s: float) -> types.HttpOptions:
    """A time limit on every request, so a hung call fails (and can be retried) instead of hanging a run."""
    return types.HttpOptions(timeout=int(timeout_s * 1000))  # the SDK takes milliseconds

class GeminiChatModel:
    def __init__(self, model: str, api_key: str, rpm: int, client=None, limiter: RateLimiter | None = None,
                 reasoning: str = "default", timeout_s: float = 120):
        """`limiter` lets several instances (parallel reviews) share one calls-per-minute budget.
        `reasoning` is default, low, medium or high (see `thinking_config`)."""
        self.model_name = model
        self.reasoning = check_reasoning(reasoning, "GeminiChatModel")
        self._client = client or genai.Client(api_key=api_key, http_options=_http_options(timeout_s))
        self._limiter = limiter or RateLimiter(rpm, sleep=_sleep)

    def complete(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=thinking_config(self.model_name, self.reasoning),
        )
        estimate = (len(system) + len(user)) // 4

        def call():
            self._limiter.acquire(estimate)
            return self._client.models.generate_content(
                model=self.model_name, contents=user, config=config
            )

        response = call_with_retries(call, is_retryable, sleep=_sleep, retry_after=retry_after_seconds)
        meta = getattr(response, "usage_metadata", None)
        if meta is not None and getattr(meta, "prompt_token_count", None) is not None:
            # thinking models bill their thoughts as output tokens
            thoughts = getattr(meta, "thoughts_token_count", None) or 0
            output = (getattr(meta, "candidates_token_count", None) or 0) + thoughts
            usage = Usage(input_tokens=meta.prompt_token_count, output_tokens=output, estimated=False,
                          thinking_tokens=thoughts)
        else:
            usage = Usage(input_tokens=estimate, output_tokens=len(response.text or "") // 4, estimated=True)
        self._limiter.record(usage.input_tokens + usage.output_tokens, estimate)
        parsed = response.parsed
        if isinstance(parsed, schema):
            return parsed, usage
        if isinstance(parsed, dict):
            return schema.model_validate(parsed), usage
        return schema.model_validate_json(response.text), usage

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        return self.complete(system, user, schema)[0]


class GeminiEmbedder:
    def __init__(self, model: str, api_key: str, dim: int, rpm: int, client=None, timeout_s: float = 120):
        self.model_name = model
        self._dim = dim
        self._client = client or genai.Client(api_key=api_key, http_options=_http_options(timeout_s))
        self._limiter = RateLimiter(rpm, sleep=_sleep)

    def embed(self, texts: list[str], kind: Literal["document", "query"]) -> list[list[float]]:
        task = "RETRIEVAL_DOCUMENT" if kind == "document" else "RETRIEVAL_QUERY"
        config = types.EmbedContentConfig(task_type=task, output_dimensionality=self._dim)
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]

            def call(batch=batch):
                self._limiter.acquire()
                return self._client.models.embed_content(
                    model=self.model_name, contents=batch, config=config
                )

            result = call_with_retries(call, is_retryable, sleep=_sleep)
            out.extend(list(e.values) for e in result.embeddings)
        return out
