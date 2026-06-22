"""Tests for src/enrich.py — backing a finished article with facts from links.

Covers: HTML->text fetch, graceful degradation, auto-pick between a direct
URL fetch and a research-provider search, and the two enrich modes
(weave facts into the prose vs. append a sources block).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import enrich  # noqa: E402


# --- fetch_url_text -----------------------------------------------------------

def test_fetch_url_text_strips_html_to_plain_text(monkeypatch):
    html = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var a=1;</script></head><body>"
        "<h1>Заголовок</h1><p>Первый&nbsp;абзац с фактом: 42%.</p>"
        "<p>Второй абзац.</p></body></html>"
    )

    class FakeResp:
        text = html
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(enrich.httpx, "Client", FakeClient)

    text = enrich.fetch_url_text("https://example.com/a")

    assert "Заголовок" in text
    assert "Первый абзац с фактом: 42%." in text
    assert "Второй абзац." in text
    assert "<" not in text and ">" not in text
    assert "var a=1" not in text and "color:red" not in text


def test_fetch_url_text_degrades_to_empty_on_network_error(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **k):
            raise enrich.httpx.TransportError("down")

    monkeypatch.setattr(enrich.httpx, "Client", BoomClient)

    assert enrich.fetch_url_text("https://example.com/a") == ""


# --- gather_link_context (auto-pick direct vs search) -------------------------

class _FakeProvider:
    def __init__(self, results):
        self.results = results
    def search(self, query, max_results):
        return self.results


def test_gather_prefers_direct_fetch_when_it_returns_more_text(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_url_text", lambda url, **k: "A" * 500)
    monkeypatch.setattr(
        enrich, "get_provider",
        lambda cfg: _FakeProvider([{"title": "t", "snippet": "short", "url": "u"}]),
    )

    ctx = enrich.gather_link_context(["https://example.com/x"], cfg={})

    assert len(ctx) == 1
    assert ctx[0]["source"] == "direct"
    assert ctx[0]["text"] == "A" * 500
    assert ctx[0]["url"] == "https://example.com/x"


def test_gather_falls_back_to_search_when_direct_is_thin(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_url_text", lambda url, **k: "tiny")
    big_snippet = "B" * 500
    monkeypatch.setattr(
        enrich, "get_provider",
        lambda cfg: _FakeProvider([{"title": "t", "snippet": big_snippet, "url": "u"}]),
    )

    ctx = enrich.gather_link_context(["https://example.com/x"], cfg={})

    assert ctx[0]["source"] == "search"
    assert big_snippet in ctx[0]["text"]


def test_gather_marks_link_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_url_text", lambda url, **k: "")
    monkeypatch.setattr(enrich, "get_provider", lambda cfg: _FakeProvider([]))

    ctx = enrich.gather_link_context(["https://example.com/x"], cfg={})

    assert ctx[0]["source"] == "none"
    assert ctx[0]["text"] == ""


def test_gather_emits_progress_events(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_url_text", lambda url, **k: "A" * 500)
    monkeypatch.setattr(enrich, "get_provider", lambda cfg: _FakeProvider([]))
    events = []

    enrich.gather_link_context(
        ["https://example.com/x"], cfg={}, on_event=events.append
    )

    assert any("example.com" in e for e in events)


# --- enrich_article -----------------------------------------------------------

class _FakeLLM:
    """Returns a canned JSON contract; records the user prompt it saw."""
    def __init__(self, payload):
        self.payload = payload
        self.last_user = None
    def complete_json(self, model, system, user):
        self.last_user = user
        return self.payload


_CFG = {
    "models": {"editor": "m", "researcher": "m"},
    "limits": {"article_chars": [1, 100000]},
    "style": {},
}


def test_enrich_weave_returns_woven_body_and_runs_style_gate(monkeypatch):
    monkeypatch.setattr(
        enrich, "gather_link_context",
        lambda links, cfg, on_event=None: [
            {"url": "https://e.com", "text": "Рынок вырос на 18% в 2024.", "source": "direct"}
        ],
    )
    llm = _FakeLLM({"title": "Заголовок", "body": "В 2024 рынок вырос на 18%. " * 3})

    result = enrich.enrich_article(
        text="Старый текст про рынок.",
        links=["https://e.com"],
        mode="weave",
        cfg=_CFG,
        llm=llm,
    )

    assert result["mode"] == "weave"
    assert "18%" in result["markdown"]
    assert isinstance(result["style_passed"], bool)
    # the fetched fact must reach the model
    assert "18%" in llm.last_user
    assert result["links_used"] == [("https://e.com", "direct")]


def test_enrich_append_keeps_original_text_and_adds_sources_block(monkeypatch):
    monkeypatch.setattr(
        enrich, "gather_link_context",
        lambda links, cfg, on_event=None: [
            {"url": "https://e.com", "text": "Рынок вырос на 18%.", "source": "search"}
        ],
    )
    llm = _FakeLLM({"facts": ["Рынок вырос на 18% в 2024 году."]})
    original = "Мой готовый текст, трогать нельзя."

    result = enrich.enrich_article(
        text=original,
        links=["https://e.com"],
        mode="append",
        cfg=_CFG,
        llm=llm,
    )

    assert result["mode"] == "append"
    assert result["markdown"].startswith(original)
    assert "18%" in result["markdown"]
    assert "https://e.com" in result["markdown"]


def test_enrich_rejects_unknown_mode():
    try:
        enrich.enrich_article(text="t", links=[], mode="bogus", cfg=_CFG, llm=_FakeLLM({}))
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown mode")
