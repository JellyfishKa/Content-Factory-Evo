"""Pluggable web research providers for the researcher stage.

A provider exposes `search(query, max_results) -> list[dict]`, each dict
shaped {"title": str, "snippet": str, "url": str}. Network failures (no
internet, rate limiting, package not installed) are swallowed and degrade
to an empty result list, so the pipeline always falls back to LLM-only
behavior rather than crashing.

All providers here are free / no API key required.
"""
from __future__ import annotations

import time


class SearchProvider:
    """Base interface. Subclasses implement `search`."""

    def search(self, query: str, max_results: int) -> list[dict]:
        raise NotImplementedError


class NullSearch(SearchProvider):
    """No-op provider: always returns no results (LLM-only behavior)."""

    def search(self, query: str, max_results: int) -> list[dict]:
        return []


class DuckDuckGoSearch(SearchProvider):
    """Uses the `ddgs` package for text search. Degrades to [] on any error
    (missing package, no network, rate limit) so callers never need to
    handle exceptions from this provider.

    DuckDuckGo rate-limits transient bursts, so a failed/empty attempt is
    retried a couple of times with a short backoff before giving up.
    """

    def search(self, query: str, max_results: int) -> list[dict]:
        attempts = 3
        backoff = [1, 2]
        raw_results: list[dict] = []
        for attempt in range(attempts):
            try:
                from ddgs import DDGS

                with DDGS() as ddgs:
                    raw_results = list(ddgs.text(query, max_results=max_results))
            except Exception:  # noqa: BLE001 - any failure degrades to LLM-only
                raw_results = []

            if raw_results:
                break
            if attempt < attempts - 1:
                time.sleep(backoff[attempt])

        results: list[dict] = []
        for item in raw_results:
            results.append(
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("body") or item.get("snippet", ""),
                    "url": item.get("href") or item.get("url", ""),
                }
            )
        return results


class WikipediaSearch(SearchProvider):
    """Free, no-key search against the public Wikipedia API
    (action=query&list=search). Tries ru.wikipedia.org first, falls back
    to en.wikipedia.org when the Russian search returns no hits. Degrades
    to [] on any network failure.
    """

    def search(self, query: str, max_results: int) -> list[dict]:
        results = self._search_lang(query, max_results, "ru")
        if results:
            return results
        return self._search_lang(query, max_results, "en")

    @staticmethod
    def _search_lang(query: str, max_results: int, lang: str) -> list[dict]:
        try:
            import httpx

            url = f"https://{lang}.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": max_results,
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # noqa: BLE001 - any failure degrades to LLM-only
            return []

        hits = (data or {}).get("query", {}).get("search", [])
        results: list[dict] = []
        for item in hits:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            page_url = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            results.append({"title": title, "snippet": snippet, "url": page_url})
        return results


class ChainSearch(SearchProvider):
    """Tries each provider in order, returning the first non-empty result
    list. Lets a primary provider (e.g. DuckDuckGo) be backed up by a more
    reliable free fallback (e.g. Wikipedia) without callers needing to know.
    """

    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers

    def search(self, query: str, max_results: int) -> list[dict]:
        for provider in self.providers:
            results = provider.search(query, max_results)
            if results:
                return results
        return []


def get_provider(cfg: dict) -> SearchProvider:
    """Returns a SearchProvider based on cfg['research']['provider']
    ("none" | "duckduckgo" | "wikipedia" | "auto"). "auto" chains
    DuckDuckGo first, then Wikipedia as a free backup. Defaults to
    NullSearch for any unknown value.
    """
    provider_name = (cfg or {}).get("research", {}).get("provider", "none")
    if provider_name == "duckduckgo":
        return DuckDuckGoSearch()
    if provider_name == "wikipedia":
        return WikipediaSearch()
    if provider_name == "auto":
        return ChainSearch([DuckDuckGoSearch(), WikipediaSearch()])
    return NullSearch()
