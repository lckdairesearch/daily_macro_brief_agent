"""Thin LiteLLM-backed provider wrapper."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from app.models import LLMUsage

T = TypeVar("T", bound=BaseModel)


class LLMResponseError(ValueError):
    """Raised when a model response cannot be parsed into the requested schema."""


@dataclass(frozen=True)
class LLMConfig:
    """Runtime options for one LLM client."""

    model: str
    temperature: float = 0.2
    max_tokens: int = 2000
    timeout_seconds: int = 60
    fake_response: str | dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    """Structured model output plus metadata."""

    output: T
    usage: LLMUsage
    raw_text: str


class LLMClient:
    """Small project wrapper around LiteLLM."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Generate free-form text (e.g. Python code). No schema validation."""
        if self.config.fake_response is not None:
            return _fake_response_text(self.config.fake_response)

        response = litellm.completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout_seconds,
        )
        return _extract_text(response)

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[T],
    ) -> LLMResult[T]:
        """Generate JSON and validate it against a Pydantic model."""
        started = time.perf_counter()

        if self.config.fake_response is not None:
            raw_text = _fake_response_text(self.config.fake_response)
            latency = time.perf_counter() - started
            return LLMResult(
                output=_parse_structured(raw_text, schema),
                usage=LLMUsage(model=self.config.model, latency_seconds=latency),
                raw_text=raw_text,
            )

        response = litellm.completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout_seconds,
            response_format={"type": "json_object"},
        )
        latency = time.perf_counter() - started

        raw_text = _extract_text(response)
        return LLMResult(
            output=_parse_structured(raw_text, schema),
            usage=_usage_from_response(response, self.config.model, latency),
            raw_text=raw_text,
        )


def _fake_response_text(fake_response: str | dict[str, Any]) -> str:
    if isinstance(fake_response, str):
        return fake_response
    return json.dumps(fake_response, ensure_ascii=False)


def _extract_text(response: Any) -> str:
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMResponseError("LiteLLM response did not include message content") from exc
    if not text:
        raise LLMResponseError("LiteLLM response content was empty")
    return text


def _parse_structured(raw_text: str, schema: type[T]) -> T:
    try:
        return schema.model_validate_json(raw_text)
    except ValidationError as exc:
        raise LLMResponseError(f"LLM response did not match schema {schema.__name__}") from exc
    except ValueError:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("LLM response was not valid JSON") from exc
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM response did not match schema {schema.__name__}") from exc


def _usage_from_response(response: Any, fallback_model: str, latency_seconds: float) -> LLMUsage:
    usage_obj = getattr(response, "usage", None)
    cost = getattr(response, "_hidden_params", {}).get("response_cost") if response else None
    return LLMUsage(
        model=getattr(response, "model", fallback_model),
        latency_seconds=latency_seconds,
        prompt_tokens=getattr(usage_obj, "prompt_tokens", None),
        completion_tokens=getattr(usage_obj, "completion_tokens", None),
        total_tokens=getattr(usage_obj, "total_tokens", None),
        estimated_cost_usd=cost,
    )
