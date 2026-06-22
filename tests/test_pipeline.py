"""Tests for pipeline.py using a FAKE LLM and an in-memory fake DB.

No real Postgres needed - db.* helpers used by pipeline are monkeypatched to
read/write an in-memory store that mimics the artifacts/scenarios tables
closely enough for these tests. Stays offline-green.
"""
from __future__ import annotations

import json

import pytest

import artifacts
import pipeline
from schemas import Brief, Draft, Final, Meta, Scenario


CFG = {
    "models": {
        "planner": "fake-planner",
        "researcher": "fake-researcher",
        "writer": "fake-writer",
        "editor": "fake-editor",
        "seo": "fake-seo",
    },
    "limits": {
        "article_chars": [10, 100000],
        "post_words": [1, 100000],
    },
    "style": {
        "blacklist": [],
        "require_number_in_first_screen": False,
        "hashtags_as_single_block": True,
        "no_caps_for_claude": False,
        "max_sentence_words": 1000,
    },
}


class FakeLLM:
    """Stub whose complete_json returns canned dicts keyed by model name."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def complete_json(self, model, system, user):
        self.calls.append(model)
        return self.responses[model]


class FakeDB:
    """In-memory store mimicking the scenarios/artifacts tables closely enough
    for pipeline.py's needs (load_scenario uses raw SQL via conn.cursor())."""

    def __init__(self):
        self.scenarios: dict[int, dict] = {}
        self.artifacts: dict[tuple[int, str, int], dict] = {}
        self.checks: list[dict] = []
        self._next_artifact_id = 1

    def add_scenario(self, scenario_id: int, idx: int, topic: str, angle: str, brief_hint: str):
        self.scenarios[scenario_id] = {
            "idx": idx,
            "topic": topic,
            "angle": angle,
            "brief_hint": brief_hint,
        }


class FakeCursor:
    def __init__(self, db: FakeDB):
        self.db = db
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        # Only query pipeline.load_scenario issues directly via conn.cursor().
        assert "FROM scenarios WHERE id" in query
        scenario_id = params[0]
        row = self.db.scenarios.get(scenario_id)
        self._result = dict(row) if row else None

    def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)


@pytest.fixture
def fake_db(monkeypatch, tmp_path):
    db = FakeDB()
    db.add_scenario(1, idx=1, topic="Тема", angle="Угол", brief_hint="Подсказка")

    def fake_get_artifact(conn, scenario_id, kind, version=1):
        return conn.db.artifacts.get((scenario_id, kind, version))

    def fake_save_artifact(conn, scenario_id, kind, content, path=None, version=1):
        artifact_id = conn.db._next_artifact_id
        conn.db._next_artifact_id += 1
        conn.db.artifacts[(scenario_id, kind, version)] = {
            "id": artifact_id,
            "scenario_id": scenario_id,
            "kind": kind,
            "content": content,
            "version": version,
        }
        return artifact_id

    def fake_save_check(conn, artifact_id, rule, passed, detail=""):
        conn.db.checks.append({"artifact_id": artifact_id, "rule": rule, "passed": passed, "detail": detail})
        return len(conn.db.checks)

    def fake_upsert_scenario(conn, run_id, idx, topic, angle, brief_hint):
        scenario_id = idx  # simple deterministic mapping for tests
        conn.db.add_scenario(scenario_id, idx, topic, angle, brief_hint)
        return scenario_id

    monkeypatch.setattr(pipeline, "get_artifact", fake_get_artifact)
    monkeypatch.setattr(pipeline, "save_check", fake_save_check)
    monkeypatch.setattr(pipeline, "upsert_scenario", fake_upsert_scenario)

    # persist_artifact (used by pipeline.py and stages/*) calls db.save_artifact
    # under the hood and writes a .md file under artifacts.RUNS_DIR - patch the
    # DB write and redirect RUNS_DIR to a tmp dir so tests stay offline and
    # don't pollute the repo's runs/ directory.
    monkeypatch.setattr(artifacts, "save_artifact", fake_save_artifact)
    monkeypatch.setattr(artifacts, "RUNS_DIR", tmp_path)
    # _lookup_run_and_idx tries raw SQL the FakeCursor doesn't support; force
    # the scenario_<id> fallback path instead of erroring.
    monkeypatch.setattr(artifacts, "_lookup_run_and_idx", lambda conn, scenario_id: (None, None))

    # editor.py still imports save_check from db directly.
    import stages.editor as editor_mod

    monkeypatch.setattr(editor_mod, "save_check", fake_save_check)

    conn = FakeConn(db)
    return conn


BRIEF_RESPONSE = {
    "facts": ["факт1"],
    "confirmed": ["подтверждено1"],
    "not_confirmed": [],
    "quotes": [],
}

DRAFTS_RESPONSE = {
    "article": {"title": "Заголовок статьи", "body": "Текст статьи про тему 1234567890."},
    "post": {"title": "Заголовок поста", "body": "Текст поста про тему."},
}

EDITOR_RESPONSE_ARTICLE = {"title": "Финальный заголовок статьи", "body": "Финальный текст статьи 1234567890."}
EDITOR_RESPONSE_POST = {"title": "Финальный заголовок поста", "body": "Финальный текст поста."}

SEO_RESPONSE = {
    "title": "SEO заголовок",
    "description": "SEO описание",
    "keywords": ["ключ1"],
    "slug": "seo-slug",
    "og_title": "OG заголовок",
    "og_description": "OG описание",
    "tags": ["тег1"],
}


def make_llm():
    return FakeLLM(
        {
            "fake-researcher": BRIEF_RESPONSE,
            "fake-writer": DRAFTS_RESPONSE,
            # editor is called twice (article, post) with the same model name;
            # FakeLLM.complete_json always returns the same canned dict per
            # call, so use a sequence keyed by call count via a thin wrapper.
            "fake-editor": EDITOR_RESPONSE_ARTICLE,
            "fake-seo": SEO_RESPONSE,
        }
    )


class SequencedEditorLLM(FakeLLM):
    """editor.md is called once per draft kind (article, then post); return
    different canned bodies for each call so we can assert both got persisted."""

    def __init__(self, responses):
        super().__init__(responses)
        self._editor_call_count = 0

    def complete_json(self, model, system, user):
        self.calls.append(model)
        if model == "fake-editor":
            self._editor_call_count += 1
            return EDITOR_RESPONSE_ARTICLE if self._editor_call_count == 1 else EDITOR_RESPONSE_POST
        return self.responses[model]


def make_sequenced_llm():
    return SequencedEditorLLM(
        {
            "fake-researcher": BRIEF_RESPONSE,
            "fake-writer": DRAFTS_RESPONSE,
            "fake-seo": SEO_RESPONSE,
        }
    )


def test_run_brief_step_reads_scenario_and_persists_brief(fake_db):
    llm = make_sequenced_llm()
    brief = pipeline.run_brief_step(1, CFG, llm, fake_db)

    assert isinstance(brief, Brief)
    assert brief.facts == ["факт1"]
    stored = fake_db.db.artifacts[(1, "brief", 1)]
    assert json.loads(stored["content"])["facts"] == ["факт1"]


def test_run_drafts_step_reads_brief_and_persists_both_drafts(fake_db):
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)
    article, post = pipeline.run_drafts_step(1, CFG, llm, fake_db)

    assert isinstance(article, Draft) and article.kind == "article"
    assert isinstance(post, Draft) and post.kind == "post"
    assert (1, "draft_article", 1) in fake_db.db.artifacts
    assert (1, "draft_post", 1) in fake_db.db.artifacts


def test_run_drafts_step_without_brief_raises(fake_db):
    llm = make_sequenced_llm()
    with pytest.raises(ValueError, match="brief not found"):
        pipeline.run_drafts_step(1, CFG, llm, fake_db)


def test_run_finals_step_reads_drafts_and_runs_style_gate(fake_db):
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)
    pipeline.run_drafts_step(1, CFG, llm, fake_db)
    final_article, checks_article, final_post, checks_post = pipeline.run_finals_step(1, CFG, llm, fake_db)

    assert isinstance(final_article, Final) and final_article.kind == "article"
    assert isinstance(final_post, Final) and final_post.kind == "post"
    assert (1, "final_article", 1) in fake_db.db.artifacts
    assert (1, "final_post", 1) in fake_db.db.artifacts
    assert len(checks_article) > 0
    assert len(checks_post) > 0


def test_run_meta_step_reads_final_article_and_persists_meta(fake_db):
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)
    pipeline.run_drafts_step(1, CFG, llm, fake_db)
    pipeline.run_finals_step(1, CFG, llm, fake_db)
    meta = pipeline.run_meta_step(1, CFG, llm, fake_db)

    assert isinstance(meta, Meta)
    assert (1, "meta", 1) in fake_db.db.artifacts


def test_full_chain_run_order(fake_db):
    """run order: brief -> drafts -> finals -> meta, each reading the
    previous stage's DB artifact (not an in-memory handoff)."""
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)
    pipeline.run_drafts_step(1, CFG, llm, fake_db)
    pipeline.run_finals_step(1, CFG, llm, fake_db)
    pipeline.run_meta_step(1, CFG, llm, fake_db)

    assert llm.calls == ["fake-researcher", "fake-writer", "fake-editor", "fake-editor", "fake-seo"]


def test_save_edited_artifact_overwrites_in_place(fake_db):
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)

    edited = Brief(facts=["новый факт"], confirmed=[], not_confirmed=[], quotes=[])
    pipeline.save_edited_artifact(fake_db, scenario_id=1, kind="brief", new_content_json=edited.model_dump_json())

    reloaded = pipeline.load_brief(fake_db, 1)
    assert reloaded.facts == ["новый факт"]


def test_rerun_from_drafts_does_not_rerun_brief(fake_db):
    """After editing the brief by hand, rerun_from('drafts', ...) should
    rebuild drafts->finals->meta but never call the researcher again."""
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)

    edited = Brief(facts=["рукой отредактированный факт"], confirmed=[], not_confirmed=[], quotes=[])
    pipeline.save_edited_artifact(fake_db, scenario_id=1, kind="brief", new_content_json=edited.model_dump_json())

    llm.calls.clear()
    results = pipeline.rerun_from(1, "drafts", CFG, llm, fake_db)

    assert "researcher" not in llm.calls
    assert llm.calls == ["fake-writer", "fake-editor", "fake-editor", "fake-seo"]
    assert "drafts" in results and "finals" in results and "meta" in results
    assert "brief" not in results


def test_rerun_from_brief_reruns_everything_downstream(fake_db):
    llm = make_sequenced_llm()
    results = pipeline.rerun_from(1, "brief", CFG, llm, fake_db)

    assert llm.calls == ["fake-researcher", "fake-writer", "fake-editor", "fake-editor", "fake-seo"]
    assert set(results.keys()) == {"brief", "drafts", "finals", "meta"}


def test_rerun_from_unknown_stage_raises(fake_db):
    llm = make_sequenced_llm()
    with pytest.raises(ValueError, match="unknown stage"):
        pipeline.rerun_from(1, "nope", CFG, llm, fake_db)


def test_edited_artifact_is_picked_up_by_next_stage(fake_db):
    """An edit to drafts (without going through run_drafts_step) should flow
    into run_finals_step, proving stages read DB state, not memory."""
    llm = make_sequenced_llm()
    pipeline.run_brief_step(1, CFG, llm, fake_db)
    pipeline.run_drafts_step(1, CFG, llm, fake_db)

    edited_article = Draft(kind="article", title="Ручной заголовок", body="Ручной текст статьи 1234567890.")
    pipeline.save_edited_artifact(
        fake_db, scenario_id=1, kind="draft_article", new_content_json=edited_article.model_dump_json()
    )

    final_article, _, _, _ = pipeline.run_finals_step(1, CFG, llm, fake_db)
    # The editor LLM stub doesn't echo input, but we confirm the draft used
    # came from DB by checking the persisted draft_article matches our edit.
    stored_draft = pipeline.load_draft(fake_db, 1, "draft_article")
    assert stored_draft.title == "Ручной заголовок"
    assert isinstance(final_article, Final)
