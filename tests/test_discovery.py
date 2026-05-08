"""Tests for Step 8: discovery scouts and orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.discovery.orchestrator import build_scouts, run_discovery
from app.discovery.scouts.base import (
    DiscoveryContext,
    FixtureDiscoveryScout,
    _EvidenceCandidateList,
    to_evidence_cards,
    web_search_and_structure,
)
from app.discovery.scouts.central_bank import CentralBankScout
from app.discovery.scouts.news import NewsScout
from app.discovery.scouts.podcast import PodcastScout
from app.discovery.scouts.research import ResearchScout
from app.discovery.scouts.x import XScout
from app.models import EvidenceCard, RunMode, SourceType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_context(mode: RunMode = RunMode.SAMPLE) -> DiscoveryContext:
    return DiscoveryContext(
        market_snapshots=[],
        calendar_events=[],
        themes=[{"id": "monetary_debasement", "keywords": ["Fed", "inflation"]}],
        portfolio={"core_positions": [{"rationale": "Monetary debasement hedge"}]},
        lookback_hours=24,
        data_cutoff=datetime(2026, 5, 8, 6, 45, tzinfo=timezone.utc),
        mode=mode,
    )


def _candidate_json(n: int = 2) -> str:
    cards = [
        {
            "title": f"Card {i}",
            "source_name": f"Source {i}",
            "url": f"https://example.com/{i}",
            "thesis": f"Thesis {i}",
            "evidence": f"Evidence {i}",
            "macro_relevance": "Macro",
            "portfolio_relevance": "Portfolio",
            "confidence": 0.8,
            "tags": ["macro"],
        }
        for i in range(n)
    ]
    return json.dumps({"cards": cards})


def _mock_litellm_response(content: str) -> MagicMock:
    """Mock a LiteLLM completion response (used by x scout and podcast filter)."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _mock_openai_responses_client(output_text: str) -> MagicMock:
    """Mock an OpenAI client whose responses.create() returns output_text."""
    oai_resp = MagicMock()
    oai_resp.output_text = output_text
    mock_client = MagicMock()
    mock_client.responses.create.return_value = oai_resp
    return mock_client


# ---------------------------------------------------------------------------
# 8.1 — DiscoveryContext and Scout protocol
# ---------------------------------------------------------------------------


def test_discovery_context_fields():
    ctx = _make_context()
    assert ctx.lookback_hours == 24
    assert ctx.mode == RunMode.SAMPLE
    assert isinstance(ctx.data_cutoff, datetime)


# ---------------------------------------------------------------------------
# 8.2 — FixtureDiscoveryScout
# ---------------------------------------------------------------------------


def test_fixture_scout_returns_evidence_cards():
    scout = FixtureDiscoveryScout()
    ctx = _make_context(RunMode.SAMPLE)
    cards = scout.run(ctx)
    assert len(cards) > 0
    for card in cards:
        assert isinstance(card, EvidenceCard)
        assert card.id
        assert card.thesis


def test_fixture_scout_cards_validate():
    scout = FixtureDiscoveryScout()
    ctx = _make_context(RunMode.SAMPLE)
    cards = scout.run(ctx)
    for card in cards:
        dumped = card.model_dump(mode="json")
        restored = EvidenceCard.model_validate(dumped)
        assert restored.id == card.id


def test_fixture_scout_raises_if_fixture_missing(tmp_path, monkeypatch):
    import app.discovery.scouts.base as base_mod
    monkeypatch.setattr(base_mod, "FIXTURE_DIR", tmp_path)
    scout = FixtureDiscoveryScout()
    with pytest.raises(FileNotFoundError):
        scout.run(_make_context())


# ---------------------------------------------------------------------------
# 8.1 — Orchestrator: failure handling
# ---------------------------------------------------------------------------


class _FailingScout:
    name = "failing"
    optional = True

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        raise RuntimeError("simulated scout failure")


class _SucceedingScout:
    name = "succeeding"
    optional = True

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        return [
            EvidenceCard(
                id="ev_test01",
                title="Test",
                source_name="Test Source",
                source_type=SourceType.NEWS,
                url="https://example.com",
                retrieved_at=datetime.now(UTC),
                thesis="Test thesis",
                evidence="Test evidence",
                macro_relevance="Macro",
                portfolio_relevance="Portfolio",
                confidence=0.9,
            )
        ]


def test_orchestrator_continues_after_optional_scout_failure():
    ctx = _make_context()
    failed: list[str] = []
    cards = run_discovery([_FailingScout(), _SucceedingScout()], ctx, failed)
    assert "failing" in failed
    assert len(cards) == 1
    assert cards[0].id == "ev_test01"


def test_orchestrator_raises_for_required_scout_failure():
    class _RequiredFailing:
        name = "required_failing"
        optional = False

        def run(self, context):
            raise RuntimeError("required scout down")

    ctx = _make_context()
    failed: list[str] = []
    with pytest.raises(RuntimeError, match="required_failing"):
        run_discovery([_RequiredFailing()], ctx, failed)


def test_orchestrator_collects_all_cards():
    ctx = _make_context()
    failed: list[str] = []
    cards = run_discovery([_SucceedingScout(), _SucceedingScout()], ctx, failed)
    assert len(cards) == 2
    assert not failed


# ---------------------------------------------------------------------------
# 8.1 — build_scouts returns fixture scout in sample mode
# ---------------------------------------------------------------------------


def test_build_scouts_sample_mode_returns_fixture():
    settings = MagicMock()
    settings.sources = {"scouts": {}, "llm": {}}
    settings.creds.openai_api_key = None
    settings.creds.xai_api_key = None
    settings.creds.listen_notes_api_key = None
    scouts = build_scouts(settings, RunMode.SAMPLE)
    assert len(scouts) == 1
    assert isinstance(scouts[0], FixtureDiscoveryScout)


def test_build_scouts_live_mode_builds_enabled_scouts():
    settings = MagicMock()
    settings.sources = {
        "scouts": {
            "news": {"enabled": True},
            "central_bank": {"enabled": True},
            "research": {"enabled": True},
            "podcast": {"enabled": True, "listen_notes_lookback_hours": 24},
            "x": {"enabled": True},
        },
        "llm": {"scout_model": "openai/gpt-5.4", "x_scout_model": "grok-4", "temperature": 0.2},
    }
    settings.creds.openai_api_key = "sk-test"
    settings.creds.xai_api_key = "xai-test"
    settings.creds.listen_notes_api_key = "ln-test"
    scouts = build_scouts(settings, RunMode.LIVE)
    names = [s.name for s in scouts]
    assert "news" in names
    assert "central_bank" in names
    assert "research" in names
    assert "podcast" in names
    assert "x" in names


def test_build_scouts_live_skips_podcast_without_key():
    settings = MagicMock()
    settings.sources = {
        "scouts": {"podcast": {"enabled": True}, "news": {"enabled": False},
                   "central_bank": {"enabled": False}, "research": {"enabled": False},
                   "x": {"enabled": False}},
        "llm": {"scout_model": "openai/gpt-5.4", "temperature": 0.2},
    }
    settings.creds.openai_api_key = "sk-test"
    settings.creds.xai_api_key = None
    settings.creds.listen_notes_api_key = None  # missing → podcast skipped
    scouts = build_scouts(settings, RunMode.LIVE)
    assert all(s.name != "podcast" for s in scouts)


# ---------------------------------------------------------------------------
# 8.3 — NewsScout (mocked OpenAI Responses API)
# ---------------------------------------------------------------------------


@patch("app.discovery.scouts.base.OpenAI")
def test_news_scout_returns_cards(mock_openai_cls):
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(2))
    scout = NewsScout(model="openai/gpt-5.4", temperature=0.2, api_key="sk-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 2
    assert all(c.source_type == SourceType.NEWS for c in cards)
    assert all(c.url.startswith("https://") for c in cards)


@patch("app.discovery.scouts.base.OpenAI")
def test_news_scout_handles_empty_response(mock_openai_cls):
    mock_openai_cls.return_value = _mock_openai_responses_client('{"cards": []}')
    scout = NewsScout(model="openai/gpt-5.4", temperature=0.2, api_key="sk-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert cards == []


# ---------------------------------------------------------------------------
# 8.3 — CentralBankScout (mocked OpenAI Responses API)
# ---------------------------------------------------------------------------


@patch("app.discovery.scouts.base.OpenAI")
def test_central_bank_scout_returns_cards(mock_openai_cls):
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))
    scout = CentralBankScout(model="openai/gpt-5.4", temperature=0.2, api_key="sk-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 1
    assert cards[0].source_type == SourceType.CENTRAL_BANK


# ---------------------------------------------------------------------------
# 8.3 — ResearchScout (mocked OpenAI Responses API)
# ---------------------------------------------------------------------------


@patch("app.discovery.scouts.base.OpenAI")
def test_research_scout_returns_cards(mock_openai_cls):
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(3))
    scout = ResearchScout(model="openai/gpt-5.4", temperature=0.2, api_key="sk-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 3
    assert all(c.source_type == SourceType.RESEARCH for c in cards)


# ---------------------------------------------------------------------------
# 8.3 — XScout (mocked xai_sdk)
# ---------------------------------------------------------------------------


def _mock_xai_chat(content: str) -> MagicMock:
    """Mock xai_sdk Client + chat.create() + sample() returning content."""
    result = MagicMock()
    result.content = content
    chat = MagicMock()
    chat.sample.return_value = result
    client = MagicMock()
    client.chat.create.return_value = chat
    return client


@patch("app.discovery.scouts.x.Client")
def test_x_scout_returns_social_cards(mock_client_cls):
    valid_url = "https://x.com/macro_analyst/status/12345678901234567"
    cards_json = json.dumps({"cards": [
        {"title": "Post A", "source_name": "macro_analyst", "url": valid_url,
         "thesis": "T", "evidence": "E", "macro_relevance": "M",
         "portfolio_relevance": "P", "confidence": 0.7, "tags": []},
    ]})
    mock_client_cls.return_value = _mock_xai_chat(cards_json)
    scout = XScout(model="grok-4", api_key="xai-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 1
    assert all(c.source_type == SourceType.SOCIAL for c in cards)


@patch("app.discovery.scouts.x.Client")
def test_x_scout_filters_hallucinated_urls(mock_client_cls):
    bad_url = "https://x.com/macro_analyst123/status/1787891234567890123fake"
    cards_json = json.dumps({"cards": [
        {"title": "", "source_name": "anon", "url": bad_url,
         "thesis": "T", "evidence": "E", "macro_relevance": "M",
         "portfolio_relevance": "P", "confidence": 0.5, "tags": []},
    ]})
    mock_client_cls.return_value = _mock_xai_chat(cards_json)
    scout = XScout(model="grok-4", api_key="xai-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    # Empty title → filtered out
    assert cards == []


@patch("app.discovery.scouts.x.Client")
def test_x_scout_handles_markdown_json(mock_client_cls):
    valid_url = "https://x.com/real_user/status/12345678901234567"
    inner = json.dumps({"cards": [
        {"title": "Post B", "source_name": "real_user", "url": valid_url,
         "thesis": "T", "evidence": "E", "macro_relevance": "M",
         "portfolio_relevance": "P", "confidence": 0.7, "tags": []},
    ]})
    wrapped = "Some preamble\n```json\n" + inner + "\n```\n"
    mock_client_cls.return_value = _mock_xai_chat(wrapped)
    scout = XScout(model="grok-4", api_key="xai-test")
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 1


# ---------------------------------------------------------------------------
# 8.3 — PodcastScout (mocked requests + litellm)
# ---------------------------------------------------------------------------


def _mock_listen_notes_response(n: int = 2) -> MagicMock:
    pub_date_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    episodes = [
        {
            "id": f"ep{i}",
            "title_original": f"Macro Insights Episode {i}",
            "description_original": "Discussion on Fed policy and global macro",
            "listennotes_url": f"https://listennotes.com/ep/{i}",
            "pub_date_ms": pub_date_ms,
            "podcast": {"title_original": f"Macro Pod {i}"},
        }
        for i in range(n)
    ]
    resp = MagicMock()
    resp.json.return_value = {"results": episodes}
    resp.raise_for_status = MagicMock()
    return resp


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_full_flow(mock_get, mock_completion, mock_openai_cls):
    # Listen Notes returns 2 episodes (called 4 times for 4 search terms)
    mock_get.return_value = _mock_listen_notes_response(2)
    # litellm: episode filter → both relevant
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0, 1]}')
    # OpenAI Responses API: content extraction for each episode
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))

    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
        lookback_hours=24,
    )
    cards = scout.run(_make_context(RunMode.LIVE))
    assert len(cards) == 2
    assert all(c.source_type == SourceType.PODCAST for c in cards)


@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_fetches_curated_first_then_freeflow_fallback(mock_get):
    def response_for(query: str) -> MagicMock:
        pub_date_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        podcast_title = "Macro Voices" if query == "Macro Voices" else "Freeflow Macro"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "results": [
                {
                    "id": f"{query}-ep",
                    "title_original": f"{query} latest",
                    "description_original": "Macro discussion",
                    "listennotes_url": f"https://listennotes.com/{query}",
                    "pub_date_ms": pub_date_ms,
                    "podcast": {"title_original": podcast_title},
                }
            ]
        }
        return resp

    mock_get.side_effect = lambda *_, **kwargs: response_for(kwargs["params"]["q"])
    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
        curated_podcasts=[{"name": "Macro Voices", "aliases": ["MacroVoices"], "priority": 1.0}],
        freeflow_search_terms=["Federal Reserve"],
        curated_min_episodes=2,
        max_curated_queries=1,
        max_freeflow_queries=1,
    )

    episodes = scout._fetch_episodes()

    queries = [call.kwargs["params"]["q"] for call in mock_get.call_args_list]
    assert queries == ["Macro Voices", "Federal Reserve"]
    assert [ep["id"] for ep in episodes] == ["Macro Voices-ep", "Federal Reserve-ep"]


@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_filters_stale_listen_notes_results(mock_get):
    fresh_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ms = 946684800000
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "results": [
            {
                "id": "stale",
                "title_original": "Old macro episode",
                "pub_date_ms": stale_ms,
                "podcast": {"title_original": "Macro Voices"},
            },
            {
                "id": "fresh",
                "title_original": "Fresh macro episode",
                "pub_date_ms": fresh_ms,
                "podcast": {"title_original": "Macro Voices"},
            },
        ]
    }
    mock_get.return_value = resp
    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
        curated_podcasts=[{"name": "Macro Voices"}],
        freeflow_search_terms=[],
        curated_min_episodes=0,
        max_curated_queries=1,
    )

    episodes = scout._fetch_episodes()

    assert [ep["id"] for ep in episodes] == ["fresh"]


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_handles_empty_listen_notes(mock_get, mock_completion, mock_openai_cls):
    resp = MagicMock()
    resp.json.return_value = {"results": []}
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
    )
    cards = scout.run(_make_context(RunMode.LIVE))
    assert cards == []
    mock_completion.assert_not_called()
    mock_openai_cls.assert_not_called()


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_handles_no_relevant_episodes(mock_get, mock_completion, mock_openai_cls):
    mock_get.return_value = _mock_listen_notes_response(2)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": []}')
    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
    )
    cards = scout.run(_make_context(RunMode.LIVE))
    assert cards == []
    mock_openai_cls.assert_not_called()


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_skips_failed_episode_extraction(mock_get, mock_completion, mock_openai_cls):
    mock_get.return_value = _mock_listen_notes_response(1)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0]}')
    # Content extraction raises
    mock_openai_cls.return_value.responses.create.side_effect = RuntimeError("LLM timeout")
    scout = PodcastScout(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        listen_notes_api_key="ln-test",
    )
    cards = scout.run(_make_context(RunMode.LIVE))
    # Failed extraction is skipped; run does not crash
    assert cards == []


# ---------------------------------------------------------------------------
# Helpers — to_evidence_cards
# ---------------------------------------------------------------------------


def test_to_evidence_cards_parses_published_at():
    raw = _EvidenceCandidateList.model_validate_json(
        json.dumps({
            "cards": [{
                "title": "T",
                "source_name": "S",
                "url": "https://example.com",
                "published_at": "2026-05-07T22:00:00Z",
                "thesis": "T",
                "evidence": "E",
                "macro_relevance": "M",
                "portfolio_relevance": "P",
                "confidence": 0.7,
                "tags": [],
            }]
        })
    )
    cards = to_evidence_cards(raw, SourceType.NEWS)
    assert cards[0].published_at is not None
    assert cards[0].published_at.year == 2026


def test_to_evidence_cards_handles_bad_published_at():
    raw = _EvidenceCandidateList.model_validate_json(
        json.dumps({
            "cards": [{
                "title": "T",
                "source_name": "S",
                "url": "https://example.com",
                "published_at": "not-a-date",
                "thesis": "T",
                "evidence": "E",
                "macro_relevance": "M",
                "portfolio_relevance": "P",
                "confidence": 0.7,
            }]
        })
    )
    cards = to_evidence_cards(raw, SourceType.RESEARCH)
    # Bad date should not crash — just sets published_at to None
    assert cards[0].published_at is None
