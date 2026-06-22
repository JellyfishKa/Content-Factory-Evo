"""Back a finished article with facts pulled from user-supplied links.

The user pastes a ready text plus a list of URLs. For each URL we gather
context two ways and auto-pick whichever yields more usable text:

  1. direct fetch  — httpx GET + a crude HTML->text strip (no extra deps),
  2. search fallback — the free research providers (DuckDuckGo / Wikipedia)
     queried with terms derived from the URL.

Then enrich_article either weaves those facts into the prose (re-running the
STYLE gate) or appends a separate "Источники" block, leaving the original
text untouched. All network failures degrade gracefully — a link that yields
nothing is simply marked source="none".
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import httpx

import validators
from research import get_provider
from schemas import Final

# Below this many characters a fetch/search result is treated as "thin" and
# the alternative source is preferred.
_MIN_USEFUL = 200
# Cap per-link text so a huge page can't blow up the LLM token budget.
_MAX_LINK_CHARS = 3000

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def fetch_url_text(url: str, timeout: float = 10.0, max_chars: int = _MAX_LINK_CHARS) -> str:
    """GETs `url` and returns its visible text, or "" on any failure.

    Strips <script>/<style> blocks and all tags, unescapes entities, and
    collapses whitespace. Deliberately dependency-free (no bs4): a crude
    strip is enough to feed an LLM as grounding context.
    """
    try:
        # Force IPv4 like llm.py — containers often lack an IPv6 route.
        transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (content-factory)"})
            resp.raise_for_status()
            raw_html = resp.text
    except Exception:  # noqa: BLE001 - any failure degrades to no context
        return ""

    return _html_to_text(raw_html)[:max_chars]


def _html_to_text(raw_html: str) -> str:
    no_blocks = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    no_tags = _TAG_RE.sub(" ", no_blocks)
    unescaped = html.unescape(no_tags).replace("\xa0", " ")
    collapsed = _WS_RE.sub(" ", unescaped)
    collapsed = _BLANK_LINES_RE.sub("\n", collapsed)
    return collapsed.strip()


def _query_from_url(url: str) -> str:
    """Derives a human-ish search query from a URL: host + path words."""
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path_words = re.split(r"[/_\-.]+", parsed.path)
    words = [w for w in path_words if w and not w.isdigit()]
    return " ".join([host] + words).strip() or url


def _search_text(url: str, cfg: dict) -> str:
    """Queries the free research providers for a URL and flattens hits to text."""
    provider = get_provider(cfg)
    query = _query_from_url(url)
    hits = provider.search(query, (cfg or {}).get("research", {}).get("max_results", 5))
    parts = []
    for hit in hits:
        title = _html_to_text(hit.get("title", ""))
        snippet = _html_to_text(hit.get("snippet", ""))
        parts.append(f"{title}: {snippet}".strip(": ").strip())
    return "\n".join(p for p in parts if p)[:_MAX_LINK_CHARS]


def gather_link_context(links: list[str], cfg: dict, on_event=None) -> list[dict]:
    """For each link returns {"url", "text", "source"} where source is
    "direct" | "search" | "none". Auto-picks whichever of the direct fetch
    and the provider search produced more text.
    """
    def emit(msg: str) -> None:
        if on_event is not None:
            on_event(msg)

    out: list[dict] = []
    for url in links:
        url = url.strip()
        if not url:
            continue
        emit(f"Ссылка {url}: качаю...")
        direct = fetch_url_text(url)

        # Only spend a search call when the direct fetch is thin.
        searched = ""
        if len(direct) < _MIN_USEFUL:
            emit(f"Ссылка {url}: прямой фетч скудный, ищу через провайдеров...")
            searched = _search_text(url, cfg)

        if len(direct) >= len(searched) and direct:
            best, source = direct, "direct"
        elif searched:
            best, source = searched, "search"
        else:
            best, source = "", "none"

        emit(f"Ссылка {url}: источник={source}, символов={len(best)}")
        out.append({"url": url, "text": best, "source": source})
    return out


def _format_context_block(contexts: list[dict]) -> str:
    lines = ["Материалы по ссылкам (опирайся только на них, не выдумывай):"]
    for ctx in contexts:
        if ctx["text"]:
            lines.append(f"- {ctx['url']}\n{ctx['text']}")
    return "\n\n".join(lines)


_WEAVE_SYSTEM = (
    "Ты — редактор. На входе готовый текст и материалы из внешних ссылок. "
    "Перепиши текст, аккуратно ВПЛЕТАЯ факты и цифры из материалов в прозу. "
    "Не выдумывай ничего сверх материалов и исходного текста. Сохрани смысл, "
    "тон и примерную длину. Верни строго JSON: {\"title\": str, \"body\": str}."
)

_APPEND_SYSTEM = (
    "Ты — фактчекер. На входе материалы из внешних ссылок. Выпиши из них "
    "короткие проверяемые факты, которые подкрепляют статью. Не выдумывай "
    "ничего сверх материалов. Верни строго JSON: {\"facts\": [str, ...]}."
)


def enrich_article(text: str, links: list[str], mode: str, cfg: dict, llm, on_event=None) -> dict:
    """Backs `text` with facts from `links`.

    mode="weave"  -> LLM rewrites the text weaving facts in; STYLE gate runs.
    mode="append" -> original text untouched, a "Источники" block is appended.

    Returns {"mode", "markdown", "links_used", and (weave only) "style_passed"}.
    """
    if mode not in ("weave", "append"):
        raise ValueError(f"unknown mode '{mode}', expected 'weave' or 'append'")

    contexts = gather_link_context(links, cfg, on_event=on_event)
    used = [(c["url"], c["source"]) for c in contexts]
    context_block = _format_context_block(contexts)

    if mode == "weave":
        if on_event is not None:
            on_event("Вплетаю факты в текст (editor)...")
        user = f"Готовый текст:\n{text}\n\n{context_block}"
        raw = llm.complete_json(cfg["models"]["editor"], _WEAVE_SYSTEM, user)
        if not isinstance(raw, dict) or "body" not in raw:
            raise ValueError(f"weave: model returned malformed contract: {raw!r}")
        body = raw["body"]
        final = Final(kind="article", title=raw.get("title", ""), body=body, style_passed=False)
        checks = validators.run_all(final, cfg)
        final.style_passed = all(c.passed for c in checks)
        if on_event is not None:
            on_event(f"STYLE-гейт: {'пройден' if final.style_passed else 'провален'}")
        return {
            "mode": "weave",
            "markdown": body,
            "style_passed": final.style_passed,
            "links_used": used,
        }

    # mode == "append"
    if on_event is not None:
        on_event("Извлекаю факты для блока источников (researcher)...")
    raw = llm.complete_json(cfg["models"]["researcher"], _APPEND_SYSTEM, context_block)
    facts = raw.get("facts", []) if isinstance(raw, dict) else []

    block_lines = ["", "## Источники", ""]
    for fact in facts:
        block_lines.append(f"- {fact}")
    if facts:
        block_lines.append("")
    for url, source in used:
        if source != "none":
            block_lines.append(f"- {url}")
    block = "\n".join(block_lines)

    return {
        "mode": "append",
        "markdown": f"{text}\n{block}",
        "links_used": used,
    }
