"""Tests for the LLM provider wrapper and prompt registry."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.llm import provider
from app.llm.prompt_registry import clear_prompt_cache, load_prompt
from app.llm.provider import LLMClient, LLMConfig, LLMResponseError
from app.models import LLMUsage


class TinyAnswer(BaseModel):
    headline: str
    score: float


EXPECTED_PROMPTS = [
    "brief_writer",
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
        assert kwargs["messages"][0]["content"] == "Use only supplied evidence."
        assert '"topic": "rates"' in kwargs["messages"][1]["content"]
        return SimpleNamespace(
            model="openai/test-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"headline":"Rates","score":0.8}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            _hidden_params={"response_cost": 0.001},
        )

    monkeypatch.setattr(provider.litellm, "completion", fake_completion)
    client = LLMClient(LLMConfig(model="openai/test-model"))

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

    monkeypatch.setattr(provider.litellm, "completion", fail_completion)
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
