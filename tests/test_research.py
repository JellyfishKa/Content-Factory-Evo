"""Tests for research.py (provider selection, NullSearch) and the
researcher stage's web-search injection into the user prompt.

Stays offline: DuckDuckGoSearch itself is never exercised against the real
network here (NullSearch / a fake provider stand in for it).
"""
from __future__ import annotations

import research
from schemas import Scenario
from stages import researcher as researcher_mod


def test_get_provider_returns_null_search_for_none():
    provider = research.get_provider({"research": {"provider": "none"}})
    assert isinstance(provider, research.NullSearch)


def test_get_provider_returns_duckduckgo_search_for_duckduckgo():
    provider = research.get_provider({"research": {"provider": "duckduckgo"}})
    assert isinstance(provider, research.DuckDuckGoSearch)


def test_get_provider_returns_wikipedia_search_for_wikipedia():
    provider = research.get_provider({"research": {"provider": "wikipedia"}})
    assert isinstance(provider, research.WikipediaSearch)


def test_get_provider_returns_chain_search_for_auto():
    provider = research.get_provider({"research": {"provider": "auto"}})
    assert isinstance(provider, research.ChainSearch)
    assert isinstance(provider.providers[0], research.DuckDuckGoSearch)
    assert isinstance(provider.providers[1], research.WikipediaSearch)


def test_get_provider_defaults_to_null_search_for_missing_config():
    provider = research.get_provider({})
    assert isinstance(provider, research.NullSearch)


def test_get_provider_defaults_to_null_search_for_unknown_value():
    provider = research.get_provider({"research": {"provider": "bogus"}})
    assert isinstance(provider, research.NullSearch)


def test_null_search_returns_empty_list():
    provider = research.NullSearch()
    assert provider.search("anything", 5) == []


class _FakeProviderForChain:
    def __init__(self, results):
        self.results = results
        self.calls = 0

    def search(self, query, max_results):
        self.calls += 1
        return self.results


def test_chain_search_returns_first_providers_results_when_non_empty():
    first = _FakeProviderForChain([{"title": "a", "snippet": "b", "url": "c"}])
    second = _FakeProviderForChain([{"title": "x", "snippet": "y", "url": "z"}])
    chain = research.ChainSearch([first, second])

    results = chain.search("query", 5)

    assert results == first.results
    assert first.calls == 1
    assert second.calls == 0


def test_chain_search_falls_through_to_second_provider_when_first_is_empty():
    first = _FakeProviderForChain([])
    second = _FakeProviderForChain([{"title": "x", "snippet": "y", "url": "z"}])
    chain = research.ChainSearch([first, second])

    results = chain.search("query", 5)

    assert results == second.results
    assert first.calls == 1
    assert second.calls == 1


def test_chain_search_returns_empty_when_all_providers_empty():
    first = _FakeProviderForChain([])
    second = _FakeProviderForChain([])
    chain = research.ChainSearch([first, second])

    assert chain.search("query", 5) == []


def test_wikipedia_search_degrades_to_empty_on_network_failure(monkeypatch):
    import httpx

    class BoomClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            raise httpx.TransportError("network down")

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: BoomClient())

    provider = research.WikipediaSearch()
    assert provider.search("anything", 5) == []


def test_duckduckgo_search_degrades_to_empty_when_ddgs_raises(monkeypatch):
    import sys
    import types

    fake_ddgs_module = types.ModuleType("ddgs")

    class BoomDDGS:
        def __enter__(self):
            raise RuntimeError("rate limited")

        def __exit__(self, *args):
            return False

    fake_ddgs_module.DDGS = BoomDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_ddgs_module)
    monkeypatch.setattr(research.time, "sleep", lambda *_: None)

    provider = research.DuckDuckGoSearch()
    assert provider.search("anything", 5) == []


class FakeProvider:
    """Records the query it was called with and returns canned results."""

    def __init__(self, results):
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, query, max_results):
        self.calls.append((query, max_results))
        return self.results


class FakeLLM:
    """Records the user message passed to complete_json."""

    def __init__(self, response):
        self.response = response
        self.last_user = None

    def complete_json(self, model, system, user):
        self.last_user = user
        return self.response


BRIEF_RESPONSE = {
    "facts": ["факт"],
    "confirmed": ["подтверждено"],
    "not_confirmed": [],
    "quotes": [],
}


def test_researcher_embeds_search_results_into_user_prompt(monkeypatch):
    fake_results = [
        {"title": "Заголовок 1", "snippet": "Краткое описание 1", "url": "https://example.com/1"},
        {"title": "Заголовок 2", "snippet": "Краткое описание 2", "url": "https://example.com/2"},
    ]
    fake_provider = FakeProvider(fake_results)
    monkeypatch.setattr(researcher_mod, "get_provider", lambda cfg: fake_provider)

    scenario = Scenario(idx=1, topic="Тема", angle="Ракурс", brief_hint="Подсказка")
    cfg = {
        "models": {"researcher": "fake-researcher"},
        "research": {"web_search": True, "provider": "duckduckgo", "max_results": 5},
    }
    llm = FakeLLM(BRIEF_RESPONSE)

    researcher_mod.run_researcher(scenario, cfg, llm)

    assert "Источники из интернета" in llm.last_user
    assert "https://example.com/1" in llm.last_user
    assert "Заголовок 2" in llm.last_user
    assert fake_provider.calls  # provider was queried


def test_researcher_falls_back_to_llm_only_when_no_search_results(monkeypatch):
    fake_provider = FakeProvider([])
    monkeypatch.setattr(researcher_mod, "get_provider", lambda cfg: fake_provider)

    scenario = Scenario(idx=1, topic="Тема", angle="Ракурс", brief_hint="Подсказка")
    cfg = {
        "models": {"researcher": "fake-researcher"},
        "research": {"web_search": True, "provider": "duckduckgo", "max_results": 5},
    }
    llm = FakeLLM(BRIEF_RESPONSE)

    researcher_mod.run_researcher(scenario, cfg, llm)

    assert "Источники из интернета" not in llm.last_user


def test_researcher_skips_search_when_web_search_disabled(monkeypatch):
    fake_provider = FakeProvider([{"title": "x", "snippet": "y", "url": "z"}])
    monkeypatch.setattr(researcher_mod, "get_provider", lambda cfg: fake_provider)

    scenario = Scenario(idx=1, topic="Тема", angle="Ракурс", brief_hint="Подсказка")
    cfg = {
        "models": {"researcher": "fake-researcher"},
        "research": {"web_search": False, "provider": "duckduckgo", "max_results": 5},
    }
    llm = FakeLLM(BRIEF_RESPONSE)

    researcher_mod.run_researcher(scenario, cfg, llm)

    assert "Источники из интернета" not in llm.last_user
    assert not fake_provider.calls
