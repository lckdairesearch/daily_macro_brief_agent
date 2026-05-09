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


@patch("app.discovery.scouts.base.OpenAI")
def test_web_search_and_structure_uses_long_timeout(mock_openai_cls):
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))

    web_search_and_structure(
        model="openai/gpt-5.4",
        api_key="sk-test",
        system_prompt="system",
        user_payload={"instruction": "test"},
    )

    mock_openai_cls.assert_called_once()
    assert mock_openai_cls.call_args.kwargs["timeout"] == 180


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


def test_orchestrator_records_scout_timings_for_success_and_failure():
    ctx = _make_context()
    failed: list[str] = []
    timings: list[dict] = []

    cards = run_discovery([_FailingScout(), _SucceedingScout()], ctx, failed, timings)

    assert len(cards) == 1
    assert timings[0]["component"] == "scout:failing"
    assert timings[0]["status"] == "failed"
    assert timings[1]["component"] == "scout:succeeding"
    assert timings[1]["status"] == "success"
    assert timings[1]["cards"] == 1
    assert all(isinstance(t["seconds"], float) for t in timings)


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
    settings.creds.taddy_user_id = "uid-test"
    settings.creds.taddy_api_key = "key-test"
    scouts = build_scouts(settings, RunMode.LIVE)
    names = [s.name for s in scouts]
    assert "news" in names
    assert "central_bank" in names
    assert "research" in names
    assert "podcast" in names
    assert "x" in names



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
    mock_client_cls.assert_called_once()
    assert mock_client_cls.call_args.kwargs["timeout"] == 180
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


# ---------------------------------------------------------------------------
# 8.3 — PodcastScout (mocked requests.post for Taddy GraphQL)
# ---------------------------------------------------------------------------

from app.discovery.scouts.podcast import _strip_html, _is_fresh_episode, _fetch_plain_transcript


def _make_taddy_episode(
    uid: str = "uuid-1",
    name: str = "Macro Insights",
    pub_offset_seconds: int = 0,
    transcript_urls: list[dict] | None = None,
) -> dict:
    pub_ts = int(datetime.now(timezone.utc).timestamp()) - pub_offset_seconds
    return {
        "uuid": uid,
        "name": name,
        "description": "<p>Discussion on <b>Fed policy</b> and global macro.</p>",
        "audioUrl": f"https://audio.example.com/{uid}.mp3",
        "datePublished": pub_ts,
        "taddyTranscribeStatus": "COMPLETED" if transcript_urls else "NOT_STARTED",
        "transcriptUrlsWithDetails": transcript_urls or [],
        "podcastSeries": {"uuid": "series-1", "name": "Macro Pod", "websiteUrl": "https://macropod.com"},
    }


def _taddy_search_response(episodes: list[dict]) -> MagicMock:
    """Mock requests.post returning a Taddy search GraphQL response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": {
            "search": {
                "searchId": "test-search-id",
                "podcastEpisodes": episodes,
            }
        }
    }
    return resp


def _taddy_series_response(episodes: list[dict], series_name: str = "Macro Pod") -> MagicMock:
    """Mock requests.post returning a Taddy getPodcastSeries GraphQL response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": {
            "getPodcastSeries": {
                "uuid": "series-1",
                "name": series_name,
                "websiteUrl": "https://macropod.com",
                "episodes": episodes,
            }
        }
    }
    return resp


def _make_scout(**kwargs) -> PodcastScout:
    defaults = dict(
        scout_model="openai/gpt-5.4",
        api_key="sk-test",
        taddy_user_id="uid-test",
        taddy_api_key="key-test",
        lookback_hours=24,
        use_transcripts=False,  # web-search path by default; transcript tests opt in
    )
    defaults.update(kwargs)
    return PodcastScout(**defaults)


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_full_flow(mock_post, mock_completion, mock_openai_cls):
    eps = [_make_taddy_episode(f"ep{i}", f"Macro Episode {i}") for i in range(2)]
    mock_post.return_value = _taddy_search_response(eps)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0, 1]}')
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))

    scout = _make_scout(freeflow_search_terms=["global macro"], max_freeflow_queries=1)
    cards = scout.run(_make_context(RunMode.LIVE))

    assert len(cards) == 2
    assert all(c.source_type == SourceType.PODCAST for c in cards)


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_respects_relevant_episode_limit(mock_post, mock_completion, mock_openai_cls):
    eps = [_make_taddy_episode(f"ep{i}") for i in range(5)]
    mock_post.return_value = _taddy_search_response(eps)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0,1,2,3,4]}')
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))

    scout = _make_scout(
        freeflow_search_terms=["global macro"],
        max_freeflow_queries=1,
        max_relevant_episodes=3,
    )
    cards = scout.run(_make_context(RunMode.LIVE))

    assert len(cards) == 3


@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_curated_then_freeflow_fallback(mock_post):
    """Curated fetch uses getPodcastSeries; freeflow uses search when below threshold."""
    fresh_ep = _make_taddy_episode("ep-curated", "Curated Episode")
    freeflow_ep = _make_taddy_episode("ep-free", "Freeflow Episode")

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        body = kwargs.get("json", {}).get("query", "")
        if "getPodcastSeries" in body:
            return _taddy_series_response([fresh_ep], "Macro Voices")
        return _taddy_search_response([freeflow_ep])

    mock_post.side_effect = side_effect

    scout = _make_scout(
        curated_podcasts=[{"name": "Macro Voices", "priority": 1.0}],
        freeflow_search_terms=["Federal Reserve"],
        curated_min_episodes=2,  # 1 curated < 2 threshold → freeflow also runs
        max_curated_queries=1,
        max_freeflow_queries=1,
    )
    episodes = scout._fetch_episodes()

    assert any(ep["uuid"] == "ep-curated" for ep in episodes)
    assert any(ep["uuid"] == "ep-free" for ep in episodes)
    assert call_count["n"] == 2  # 1 curated + 1 freeflow


@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_filters_stale_episodes(mock_post):
    fresh_ep = _make_taddy_episode("fresh", pub_offset_seconds=3600)    # 1 hour ago
    stale_ep = _make_taddy_episode("stale", pub_offset_seconds=999999)  # way old

    mock_post.return_value = _taddy_series_response([fresh_ep, stale_ep])

    scout = _make_scout(
        curated_podcasts=[{"name": "Macro Voices", "priority": 1.0}],
        freeflow_search_terms=[],
        curated_min_episodes=0,
        max_curated_queries=1,
    )
    episodes = scout._fetch_episodes()

    assert [ep["uuid"] for ep in episodes] == ["fresh"]


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_handles_empty_search(mock_post, mock_completion, mock_openai_cls):
    mock_post.return_value = _taddy_search_response([])
    scout = _make_scout(freeflow_search_terms=["global macro"], max_freeflow_queries=1)
    cards = scout.run(_make_context(RunMode.LIVE))

    assert cards == []
    mock_completion.assert_not_called()
    mock_openai_cls.assert_not_called()


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_handles_no_relevant_episodes(mock_post, mock_completion, mock_openai_cls):
    eps = [_make_taddy_episode("ep0")]
    mock_post.return_value = _taddy_search_response(eps)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": []}')

    scout = _make_scout(freeflow_search_terms=["global macro"], max_freeflow_queries=1)
    cards = scout.run(_make_context(RunMode.LIVE))

    assert cards == []
    mock_openai_cls.assert_not_called()


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
def test_podcast_scout_skips_failed_episode_extraction(mock_post, mock_completion, mock_openai_cls):
    eps = [_make_taddy_episode("ep0")]
    mock_post.return_value = _taddy_search_response(eps)
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0]}')
    mock_openai_cls.return_value.responses.create.side_effect = RuntimeError("LLM timeout")

    scout = _make_scout(freeflow_search_terms=["global macro"], max_freeflow_queries=1)
    cards = scout.run(_make_context(RunMode.LIVE))

    assert cards == []  # failed extraction skipped; run does not crash


@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_uses_transcript_when_available(mock_get, mock_post, mock_completion):
    """When a non-exclusive plain-text transcript URL exists, litellm extraction is used."""
    transcript_url = {"url": "https://transcripts.example.com/ep0.txt", "type": "text/plain",
                      "isTaddyExclusive": False, "language": "en"}
    ep = _make_taddy_episode("ep0", transcript_urls=[transcript_url])
    mock_post.return_value = _taddy_search_response([ep])

    # First litellm call = episode filter; second = transcript extraction
    mock_completion.side_effect = [
        _mock_litellm_response('{"relevant_indices": [0]}'),
        _mock_litellm_response(_candidate_json(1)),
    ]
    transcript_resp = MagicMock()
    transcript_resp.raise_for_status = MagicMock()
    transcript_resp.text = "Fed raised rates. Gold is relevant to our book."
    mock_get.return_value = transcript_resp

    scout = _make_scout(
        freeflow_search_terms=["global macro"],
        max_freeflow_queries=1,
        use_transcripts=True,
    )
    cards = scout.run(_make_context(RunMode.LIVE))

    assert len(cards) == 1
    assert mock_completion.call_count == 2  # filter + extraction (no OpenAI web search)


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_falls_back_to_web_search_when_no_transcript(
    mock_get, mock_post, mock_completion, mock_openai_cls
):
    """Episode with no transcript URLs falls back to OpenAI web search recall."""
    ep = _make_taddy_episode("ep0", transcript_urls=[])
    mock_post.return_value = _taddy_search_response([ep])
    mock_completion.return_value = _mock_litellm_response('{"relevant_indices": [0]}')
    mock_openai_cls.return_value = _mock_openai_responses_client(_candidate_json(1))

    scout = _make_scout(
        freeflow_search_terms=["global macro"],
        max_freeflow_queries=1,
        use_transcripts=True,
    )
    cards = scout.run(_make_context(RunMode.LIVE))

    assert len(cards) == 1
    mock_openai_cls.assert_called_once()  # web search path was used
    assert mock_openai_cls.call_args.kwargs["timeout"] == 180
    mock_get.assert_not_called()          # no transcript fetch attempted


@patch("app.discovery.scouts.podcast.OpenAI")
@patch("app.discovery.scouts.podcast.litellm.completion")
@patch("app.discovery.scouts.podcast.requests.post")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_uses_long_timeout_for_transcript_extraction(
    mock_get, mock_post, mock_completion, mock_openai_cls
):
    """Transcript extraction should use the longer scout timeout budget."""
    transcript_url = {"url": "https://transcripts.example.com/ep0.txt", "type": "text/plain",
                      "isTaddyExclusive": False, "language": "en"}
    ep = _make_taddy_episode("ep0", transcript_urls=[transcript_url])
    mock_post.return_value = _taddy_search_response([ep])

    mock_completion.side_effect = [
        _mock_litellm_response('{"relevant_indices": [0]}'),
        _mock_litellm_response(_candidate_json(1)),
    ]
    transcript_resp = MagicMock()
    transcript_resp.raise_for_status = MagicMock()
    transcript_resp.text = "Fed raised rates. Gold is relevant to our book."
    mock_get.return_value = transcript_resp

    scout = _make_scout(
        freeflow_search_terms=["global macro"],
        max_freeflow_queries=1,
        use_transcripts=True,
    )
    cards = scout.run(_make_context(RunMode.LIVE))

    assert len(cards) == 1
    assert mock_completion.call_count == 2
    assert mock_openai_cls.call_count == 0


@patch("app.discovery.scouts.podcast.requests.post")
@patch("app.discovery.scouts.podcast.requests.get")
def test_podcast_scout_skips_exclusive_transcripts(mock_get, mock_post):
    """isTaddyExclusive=True transcripts are not fetched."""
    exclusive_url = {"url": "https://taddy.org/exclusive/ep0.txt", "type": "text/plain",
                     "isTaddyExclusive": True, "language": "en"}
    ep = _make_taddy_episode("ep0", transcript_urls=[exclusive_url])

    result = _fetch_plain_transcript(ep)

    assert result is None
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# PodcastScout helpers — unit tests
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello  world"


def test_strip_html_passthrough_plain():
    assert _strip_html("plain text") == "plain text"


def test_is_fresh_episode_accepts_recent():
    now_ts = int(datetime.now(timezone.utc).timestamp())
    ep = {"datePublished": now_ts - 3600}  # 1 hour ago
    assert _is_fresh_episode(ep, now_ts - 86400) is True


def test_is_fresh_episode_rejects_stale():
    now_ts = int(datetime.now(timezone.utc).timestamp())
    ep = {"datePublished": now_ts - 999999}
    assert _is_fresh_episode(ep, now_ts - 86400) is False


def test_is_fresh_episode_handles_missing_date():
    assert _is_fresh_episode({}, 0) is False


# ---------------------------------------------------------------------------
# build_scouts — Taddy credential checks
# ---------------------------------------------------------------------------


def test_build_scouts_live_skips_podcast_without_taddy_key():
    settings = MagicMock()
    settings.sources = {
        "scouts": {"podcast": {"enabled": True}, "news": {"enabled": False},
                   "central_bank": {"enabled": False}, "research": {"enabled": False},
                   "x": {"enabled": False}},
        "llm": {"scout_model": "openai/gpt-5.4", "temperature": 0.2},
    }
    settings.creds.openai_api_key = "sk-test"
    settings.creds.xai_api_key = None
    settings.creds.taddy_api_key = None  # missing → podcast skipped
    settings.creds.taddy_user_id = None
    scouts = build_scouts(settings, RunMode.LIVE)
    assert all(s.name != "podcast" for s in scouts)


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
