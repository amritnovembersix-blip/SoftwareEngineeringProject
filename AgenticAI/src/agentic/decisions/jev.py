"""Adapter: TypeSafe Jev HTTP API (Adaptee) -> DecisionModel (Target)."""
import time

import httpx

from agentic.decisions.base import DecisionError, NoulQuestion, NoulResult
from agentic.llm.ratelimit import call_with_retries

_RETRYABLE = {429, 529}


class _RetryableStatus(Exception):
    def __init__(self, status: int):
        super().__init__(f"jev http {status}")
        self.status = status


class JevDecisionModel:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/v1/systemone"
        self._model = model
        self._sleep = sleep
        self._client = httpx.Client(transport=transport, timeout=60.0)

    def noul(self, state: dict, questions: dict[str, NoulQuestion]) -> NoulResult:
        body = {
            "state": state,
            "model": self._model,
            "questions": {
                k: {
                    "type": "noul",
                    "instructions": q.instructions,
                    **({"criteria": q.criteria} if q.criteria else {}),
                }
                for k, q in questions.items()
            },
        }

        def attempt() -> httpx.Response:
            try:
                resp = self._client.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {self._api_key}"}
                )
            except httpx.TransportError as exc:
                raise DecisionError(f"jev transport error: {type(exc).__name__}") from None
            if resp.status_code in _RETRYABLE:
                raise _RetryableStatus(resp.status_code)
            if not resp.is_success:
                raise DecisionError(f"jev http {resp.status_code}")
            return resp

        try:
            resp = call_with_retries(
                attempt, lambda e: isinstance(e, _RetryableStatus), sleep=self._sleep
            )
        except _RetryableStatus as exc:
            raise DecisionError(f"jev http {exc.status}") from None

        try:
            data = resp.json()
            answers = data["answers"]
            return NoulResult(
                probabilities={k: float(answers[k]["noul"]) for k in questions if k in answers},
                model=data["model"],
                input_tokens=data["usage"]["input_tokens"],
            )
        except (ValueError, KeyError, TypeError):
            raise DecisionError("jev malformed response") from None
