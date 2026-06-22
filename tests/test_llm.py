"""Tests for src/llm.py model-fallback observability (the on_event hook).

The hook lets the Streamlit panel surface which model actually answered and
when a free model fell over and the next one was tried — without parsing
stdout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

import llm as llm_mod  # noqa: E402


def test_on_event_reports_fallback_when_first_model_fails(monkeypatch):
    client = llm_mod.LLM(cfg={"model_fallbacks": ["model-b"]})
    events = []
    client.on_event = events.append

    calls = []

    def fake_complete_one(self, model, system, user, *, json_mode=False):
        calls.append(model)
        if model == "model-a":
            raise httpx.TransportError("down")
        return "ok"

    monkeypatch.setattr(llm_mod.LLM, "_complete_one", fake_complete_one)

    result = client.complete("model-a", "sys", "usr")

    assert result == "ok"
    assert calls == ["model-a", "model-b"]
    joined = " ".join(events)
    assert "model-a" in joined and "model-b" in joined


def test_on_event_absent_is_safe(monkeypatch):
    client = llm_mod.LLM(cfg={"model_fallbacks": []})

    def fake_complete_one(self, model, system, user, *, json_mode=False):
        return "ok"

    monkeypatch.setattr(llm_mod.LLM, "_complete_one", fake_complete_one)

    # No on_event set — must not raise.
    assert client.complete("model-a", "sys", "usr") == "ok"
