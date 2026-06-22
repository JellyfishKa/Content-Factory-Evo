"""Pluggable web research providers for the researcher stage.

A provider exposes `search(query, max_results) -> list[dict]`, each dict
shaped {"title": str, "snippet": str, "url": str}. Network failures (no
internet, rate limiting, package not installed) are swallowed and degrade
to an empty result list, so the pipeline always falls back to LLM-only
behavior rather than crashing.
"""
from __future__ import annotations


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
    """

    def search(self, query: str, max_results: int) -> list[dict]:
        try:
            from ddgs import DDGS

            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
        except Exception:  # noqa: BLE001 - any failure degrades to LLM-only
            return []

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


def get_provider(cfg: dict) -> SearchProvider:
    """Returns a SearchProvider based on cfg['research']['provider']
    ("none" | "duckduckgo"). Defaults to NullSearch for any unknown value.
    """
    provider_name = (cfg or {}).get("research", {}).get("provider", "none")
    if provider_name == "duckduckgo":
        return DuckDuckGoSearch()
    return NullSearch()
