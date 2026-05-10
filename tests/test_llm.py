"""Tests for the LLM provider wrapper and prompt registry."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.llm import litellm_compat, provider
from app.llm.prompt_registry import clear_prompt_cache, load_prompt
from app.llm.provider import LLMClient, LLMConfig, LLMResponseError
from app.models import LLMUsage


class TinyAnswer(BaseModel):
    headline: str
    score: float


EXPECTED_PROMPTS = [
    "brief_writer",
    "brief_reviewer",
    "chart_codegen",
    "critic",
    "scouts/news_search",
    "scouts/central_bank_extract",
    "scouts/research_search",
    "scouts/podcast_extract",
    "scouts/x_narrative_search",
    "scouts/consensus_enrichment",
]


def test_prompt_registry_loads_all_prompts():
    clear_prompt_cache()
    for name in EXPECTED_PROMPTS:
        prompt = load_prompt(name)
        assert prompt.name == name
        assert prompt.path.name.endswith(".md")
        assert any(phrase in prompt.text for phrase in ("Do not invent", "do not invent", "Never invent", "never invent", "do not fabricate", "fabricate"))
        if not name.startswith("chart_"):
            assert "source" in prompt.text.lower()


def test_prompt_registry_rejects_path_traversal():
    with pytest.raises(ValueError):
        load_prompt("../settings")


def test_prompt_registry_missing_prompt_fails_clearly():
    with pytest.raises(FileNotFoundError, match="Prompt not found"):
        load_prompt("does_not_exist")


def test_provider_returns_structured_output_from_mocked_litellm(monkeypatch):
    def fake_completion(**kwargs):
        assert kwargs["model"] == "openai/test-model"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["reasoning_effort"] == "high"
        assert kwargs["verbosity"] == "low"
        assert kwargs["messages"][0]["content"] == "Use only supplied evidence."
        assert '"topic": "rates"' in kwargs["messages"][1]["content"]
        return SimpleNamespace(
            model="openai/test-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"headline":"Rates","score":0.8}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            _hidden_params={"response_cost": 0.001},
        )

    monkeypatch.setattr(provider.litellm_compat, "completion", fake_completion)
    client = LLMClient(LLMConfig(model="openai/test-model", reasoning_effort="high", verbosity="low"))

    result = client.generate_structured(
        system_prompt="Use only supplied evidence.",
        user_payload={"topic": "rates"},
        schema=TinyAnswer,
    )

    assert result.output == TinyAnswer(headline="Rates", score=0.8)
    assert isinstance(result.usage, LLMUsage)
    assert result.usage.total_tokens == 15
    assert result.usage.estimated_cost_usd == 0.001


def test_provider_fake_response_avoids_litellm(monkeypatch):
    def fail_completion(**kwargs):
        raise AssertionError("LiteLLM should not be called for fake_response")

    monkeypatch.setattr(provider.litellm_compat, "completion", fail_completion)
    client = LLMClient(
        LLMConfig(
            model="fake/test",
            fake_response={"headline": "Fixture-backed sample", "score": 1.0},
        )
    )

    result = client.generate_structured(
        system_prompt="Use only supplied evidence.",
        user_payload={"mode": "sample"},
        schema=TinyAnswer,
    )

    assert result.output.headline == "Fixture-backed sample"
    assert result.usage.model == "fake/test"


def test_provider_passes_reasoning_controls_for_text_calls(monkeypatch):
    def fake_completion(**kwargs):
        assert kwargs["reasoning_effort"] == "medium"
        assert kwargs["verbosity"] == "high"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )

    monkeypatch.setattr(provider.litellm_compat, "completion", fake_completion)
    client = LLMClient(
        LLMConfig(
            model="openai/test-model",
            reasoning_effort="medium",
            verbosity="high",
        )
    )

    result = client.generate_text(system_prompt="system", user_message="user")

    assert result == "ok"


def test_provider_omits_non_default_temperature_for_gpt5(monkeypatch):
    def fake_completion(**kwargs):
        assert "temperature" not in kwargs
        return SimpleNamespace(
            model="openai/gpt-5.5",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"headline":"Rates","score":0.8}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            _hidden_params={"response_cost": 0.001},
        )

    monkeypatch.setattr(provider.litellm_compat, "completion", fake_completion)
    client = LLMClient(
        LLMConfig(
            model="openai/gpt-5.5",
            temperature=0.1,
            reasoning_effort="high",
            verbosity="low",
        )
    )

    result = client.generate_structured(
        system_prompt="Use only supplied evidence.",
        user_payload={"topic": "rates"},
        schema=TinyAnswer,
    )

    assert result.output.headline == "Rates"


@pytest.mark.parametrize(
    "raw_text",
    [
        "not json",
        '{"headline": "Missing score"}',
    ],
)
def test_provider_rejects_invalid_json_or_schema(raw_text):
    client = LLMClient(LLMConfig(model="fake/test", fake_response=raw_text))

    with pytest.raises(LLMResponseError):
        client.generate_structured(
            system_prompt="Use only supplied evidence.",
            user_payload={},
            schema=TinyAnswer,
        )


def test_litellm_compat_sets_event_loop_before_sync_completion(monkeypatch):
    events: list[str] = []

    class FakeLoop:
        pass

    def fake_get_running_loop():
        raise RuntimeError("no running loop")

    def fake_new_event_loop():
        events.append("new")
        return FakeLoop()

    def fake_set_event_loop(loop):
        assert isinstance(loop, FakeLoop)
        events.append("set")

    def fake_completion(**kwargs):
        assert kwargs["model"] == "openai/test-model"
        events.append("completion")
        return "ok"

    monkeypatch.setattr(asyncio, "get_running_loop", fake_get_running_loop)
    monkeypatch.setattr(asyncio, "new_event_loop", fake_new_event_loop)
    monkeypatch.setattr(asyncio, "set_event_loop", fake_set_event_loop)
    monkeypatch.setattr(litellm_compat.litellm, "completion", fake_completion)

    result = litellm_compat.completion(model="openai/test-model")

    assert result == "ok"
    assert events == ["new", "set", "completion"]
