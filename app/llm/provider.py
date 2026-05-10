"""Thin LiteLLM-backed provider wrapper."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import litellm_compat
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
    reasoning_effort: str | None = None
    verbosity: str | None = None
    fake_response: str | dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    """Structured model output plus metadata."""

    output: T
    usage: LLMUsage
    raw_text: str


@dataclass(frozen=True)
class LLMTextResult:
    """Free-form model output plus metadata."""

    text: str
    usage: LLMUsage


class LLMClient:
    """Small project wrapper around LiteLLM."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_message: str,
        stage: str | None = None,
    ) -> LLMTextResult:
        """Generate free-form text (e.g. Python code). No schema validation."""
        started = time.perf_counter()
        if self.config.fake_response is not None:
            latency = time.perf_counter() - started
            return LLMTextResult(
                text=_fake_response_text(self.config.fake_response),
                usage=LLMUsage(model=self.config.model, latency_seconds=latency, stage=stage),
            )

        response = litellm_compat.completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            **_completion_options(self.config),
        )
        latency = time.perf_counter() - started
        return LLMTextResult(
            text=_extract_text(response),
            usage=usage_from_response(
                response,
                fallback_model=self.config.model,
                latency_seconds=latency,
                stage=stage,
            ),
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[T],
        stage: str | None = None,
    ) -> LLMResult[T]:
        """Generate JSON and validate it against a Pydantic model."""
        started = time.perf_counter()

        if self.config.fake_response is not None:
            raw_text = _fake_response_text(self.config.fake_response)
            latency = time.perf_counter() - started
            return LLMResult(
                output=_parse_structured(raw_text, schema),
                usage=LLMUsage(model=self.config.model, latency_seconds=latency, stage=stage),
                raw_text=raw_text,
            )

        response = litellm_compat.completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            **_completion_options(self.config),
            response_format={"type": "json_object"},
        )
        latency = time.perf_counter() - started

        raw_text = _extract_text(response)
        return LLMResult(
            output=_parse_structured(raw_text, schema),
            usage=usage_from_response(
                response,
                fallback_model=self.config.model,
                latency_seconds=latency,
                stage=stage,
            ),
            raw_text=raw_text,
        )


def _fake_response_text(fake_response: str | dict[str, Any]) -> str:
    if isinstance(fake_response, str):
        return fake_response
    return json.dumps(fake_response, ensure_ascii=False)


def _completion_options(config: LLMConfig) -> dict[str, Any]:
    options: dict[str, Any] = {
        "max_tokens": config.max_tokens,
        "timeout": config.timeout_seconds,
    }
    if _should_send_temperature(config):
        options["temperature"] = config.temperature
    if config.reasoning_effort is not None:
        options["reasoning_effort"] = config.reasoning_effort
    if config.verbosity is not None:
        options["verbosity"] = config.verbosity
    return options


def _should_send_temperature(config: LLMConfig) -> bool:
    model = config.model.lower()
    # GPT-5 family currently rejects non-default temperatures in Chat Completions.
    # Let the provider/model default stand unless the caller explicitly uses 1.0.
    if "gpt-5" in model and config.temperature != 1:
        return False
    return True


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


def usage_from_response(
    response: Any,
    *,
    fallback_model: str,
    latency_seconds: float,
    stage: str | None = None,
) -> LLMUsage:
    usage_obj = getattr(response, "usage", None)
    hidden_params = getattr(response, "_hidden_params", {}) if response else {}
    cost = None
    if isinstance(hidden_params, dict):
        cost = _coerce_float(hidden_params.get("response_cost"))
    prompt_tokens = _usage_value(usage_obj, "prompt_tokens", "input_tokens")
    completion_tokens = _usage_value(usage_obj, "completion_tokens", "output_tokens")
    total_tokens = _usage_value(usage_obj, "total_tokens")
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    prompt_details = _usage_details(usage_obj, "prompt_tokens_details", "input_tokens_details")
    completion_details = _usage_details(
        usage_obj,
        "completion_tokens_details",
        "output_tokens_details",
        "output_token_details",
    )
    return LLMUsage(
        model=_response_model(response, fallback_model),
        latency_seconds=latency_seconds,
        stage=stage,
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=_detail_value(prompt_details, "cached_tokens", "cached_token_count"),
        completion_tokens=completion_tokens,
        reasoning_tokens=_detail_value(
            completion_details,
            "reasoning_tokens",
            "reasoning_token_count",
        ),
        total_tokens=total_tokens,
        tool_calls_count=_tool_calls_count(response),
        estimated_cost_usd=cost,
    )


def _usage_value(usage_obj: Any, *names: str) -> int | None:
    for name in names:
        if usage_obj is None:
            return None
        value = None
        if isinstance(usage_obj, dict):
            value = usage_obj.get(name)
        else:
            value = getattr(usage_obj, name, None)
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


def _usage_details(usage_obj: Any, *names: str) -> Any:
    for name in names:
        if usage_obj is None:
            return None
        if isinstance(usage_obj, dict) and usage_obj.get(name) is not None:
            return usage_obj[name]
        value = getattr(usage_obj, name, None)
        if value is not None:
            return value
    return None


def _detail_value(details: Any, *names: str) -> int | None:
    for name in names:
        if details is None:
            return None
        value = None
        if isinstance(details, dict):
            value = details.get(name)
        else:
            value = getattr(details, name, None)
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


def _tool_calls_count(response: Any) -> int | None:
    output = getattr(response, "output", None)
    if isinstance(output, list):
        count = 0
        for item in output:
            item_type = getattr(item, "type", None)
            if item_type == "tool_call":
                count += 1
            content = getattr(item, "content", None)
            if isinstance(content, list):
                count += sum(1 for entry in content if getattr(entry, "type", None) == "tool_call")
        return count or None
    return None


def _response_model(response: Any, fallback_model: str) -> str:
    model = getattr(response, "model", None)
    return model if isinstance(model, str) and model else fallback_model


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
