"""Tests for kind-aware editor voice — article vs post must read differently.

The editor runs once per draft kind. Before, both kinds got the identical
system+user prompt, so finals converged to the same register. These tests
pin that run_editor now injects a distinct voice directive per kind.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import stages.editor as editor_mod  # noqa: E402
from schemas import Draft  # noqa: E402


class _RecordingLLM:
    def __init__(self):
        self.last_user = None
    def complete_json(self, model, system, user):
        self.last_user = user
        return {"title": "T", "body": "В 2024 рынок вырос на 18 процентов."}


_CFG = {"models": {"editor": "m"}, "limits": {"article_chars": [1, 100000], "post_words": [1, 100000]}, "style": {}}


def test_editor_injects_article_voice_for_article(monkeypatch):
    llm = _RecordingLLM()
    draft = Draft(kind="article", title="t", body="b")

    editor_mod.run_editor(draft, _CFG, llm, refs=[])

    assert editor_mod.ARTICLE_VOICE in llm.last_user
    assert editor_mod.POST_VOICE not in llm.last_user


def test_editor_injects_post_voice_for_post(monkeypatch):
    llm = _RecordingLLM()
    draft = Draft(kind="post", title="t", body="b")

    editor_mod.run_editor(draft, _CFG, llm, refs=[])

    assert editor_mod.POST_VOICE in llm.last_user
    assert editor_mod.ARTICLE_VOICE not in llm.last_user


def test_editor_voice_can_be_overridden_from_config():
    llm = _RecordingLLM()
    cfg = dict(_CFG)
    cfg["style"] = {"voice": {"post": "СВОЙ ГОЛОС ПОСТА"}}
    draft = Draft(kind="post", title="t", body="b")

    editor_mod.run_editor(draft, cfg, llm, refs=[])

    assert "СВОЙ ГОЛОС ПОСТА" in llm.last_user
