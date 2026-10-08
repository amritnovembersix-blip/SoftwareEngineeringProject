"""Gemini Adapters: adapt the google-genai SDK (Adaptee) to ChatModel / Embedder (Targets)."""
import time
from typing import Literal

from google import genai
from google.genai import types

from agentic.llm.base import T
from agentic.llm.ratelimit import RateLimiter, call_with_retries

_EMBED_BATCH = 100


def is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    return isinstance(code, int) and not isinstance(code, bool) and (code == 429 or code >= 500)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class GeminiChatModel:
    def __init__(self, model: str, api_key: str, rpm: int, client=None):
        self.model_name = model
        self._client = client or genai.Client(api_key=api_key)
        self._limiter = RateLimiter(rpm, sleep=_sleep)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )

        def call():
            self._limiter.acquire()
            return self._client.models.generate_content(
                model=self.model_name, contents=user, config=config
            )

        response = call_with_retries(call, is_retryable, sleep=_sleep)
        parsed = response.parsed
        if isinstance(parsed, schema):
            return parsed
        if isinstance(parsed, dict):
            return schema.model_validate(parsed)
        return schema.model_validate_json(response.text)


class GeminiEmbedder:
    def __init__(self, model: str, api_key: str, dim: int, rpm: int, client=None):
        self.model_name = model
        self._dim = dim
        self._client = client or genai.Client(api_key=api_key)
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
