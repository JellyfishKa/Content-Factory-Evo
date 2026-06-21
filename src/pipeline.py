"""Granular, DB-backed pipeline API for human-in-the-loop control (Streamlit panel).

Unlike orchestrator.process_scenario (which threads Pydantic objects through
an in-memory chain), every function here reads its upstream input straight
from the DB via db.get_artifact and persists its own output. That means a
single stage can be re-run standalone after a human edits an artifact by
hand, without re-running the whole chain.

Stage order for rerun_from: planner -> brief -> drafts -> finals -> meta.
"""
from __future__ import annotations

import json

from db import get_artifact, save_artifact, save_check, upsert_scenario
from llm import LLM
from schemas import Brief, Draft, Final, Meta, Scenario, Source
from stages.editor import run_editor
from stages.planner import run_planner
from stages.researcher import run_researcher
from stages.seo import run_seo
from stages.writer import run_writer

STAGE_ORDER = ["planner", "brief", "drafts", "finals", "meta"]


def _stage_index(name: str) -> int:
    return STAGE_ORDER.index(name)


# --- artifact (de)serialization -------------------------------------------------

def load_brief(conn, scenario_id: int) -> Brief | None:
    row = get_artifact(conn, scenario_id=scenario_id, kind="brief")
    if row is None:
        return None
    return Brief.model_validate(json.loads(row["content"]))


def load_draft(conn, scenario_id: int, kind: str) -> Draft | None:
    """kind is 'draft_article' or 'draft_post'."""
    row = get_artifact(conn, scenario_id=scenario_id, kind=kind)
    if row is None:
        return None
    return Draft.model_validate(json.loads(row["content"]))


def load_final(conn, scenario_id: int, kind: str) -> Final | None:
    """kind is 'final_article' or 'final_post'."""
    row = get_artifact(conn, scenario_id=scenario_id, kind=kind)
    if row is None:
        return None
    return Final.model_validate(json.loads(row["content"]))


def load_meta(conn, scenario_id: int) -> Meta | None:
    row = get_artifact(conn, scenario_id=scenario_id, kind="meta")
    if row is None:
        return None
    return Meta.model_validate(json.loads(row["content"]))


def load_scenario(conn, scenario_id: int) -> Scenario | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT idx, topic, angle, brief_hint FROM scenarios WHERE id = %s",
            (scenario_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return Scenario(idx=row["idx"], topic=row["topic"], angle=row["angle"], brief_hint=row["brief_hint"])


# --- per-stage step functions -----------------------------------------------------

def run_planner_step(source: Source, cfg: dict, llm: LLM, conn, n: int | None = None, run_id: int | None = None) -> list[Scenario]:
    """Runs the planner and persists scenarios (requires run_id to upsert)."""
    scenarios = run_planner(source, cfg, llm, n=n)
    if run_id is not None:
        for sc in scenarios:
            upsert_scenario(
                conn,
                run_id=run_id,
                idx=sc.idx,
                topic=sc.topic,
                angle=sc.angle,
                brief_hint=sc.brief_hint,
            )
    return scenarios


def run_brief_step(scenario_id: int, cfg: dict, llm: LLM, conn) -> Brief:
    """Reads the Scenario from DB, runs the researcher, persists the brief."""
    scenario = load_scenario(conn, scenario_id)
    if scenario is None:
        raise ValueError(f"scenario {scenario_id} not found")
    return run_researcher(scenario, cfg, llm, conn=conn, scenario_id=scenario_id)


def run_drafts_step(scenario_id: int, cfg: dict, llm: LLM, conn) -> tuple[Draft, Draft]:
    """Reads Scenario + Brief from DB, runs the writer, persists both drafts."""
    scenario = load_scenario(conn, scenario_id)
    if scenario is None:
        raise ValueError(f"scenario {scenario_id} not found")
    brief = load_brief(conn, scenario_id)
    if brief is None:
        raise ValueError(f"brief not found for scenario {scenario_id}")
    article, post = run_writer(scenario, brief, cfg, llm, conn=None)
    save_artifact(conn, scenario_id=scenario_id, kind="draft_article", content=article.model_dump_json())
    save_artifact(conn, scenario_id=scenario_id, kind="draft_post", content=post.model_dump_json())
    return article, post


def run_finals_step(scenario_id: int, cfg: dict, llm: LLM, conn):
    """Reads drafts from DB, runs the editor (STYLE gate), persists finals + checks."""
    draft_article = load_draft(conn, scenario_id, "draft_article")
    draft_post = load_draft(conn, scenario_id, "draft_post")
    if draft_article is None or draft_post is None:
        raise ValueError(f"drafts not found for scenario {scenario_id}")
    final_article, checks_article = run_editor(draft_article, cfg, llm, conn=conn, scenario_id=scenario_id)
    final_post, checks_post = run_editor(draft_post, cfg, llm, conn=conn, scenario_id=scenario_id)
    return final_article, checks_article, final_post, checks_post


def run_meta_step(scenario_id: int, cfg: dict, llm: LLM, conn) -> Meta:
    """Reads final_article from DB, runs seo, persists meta."""
    final_article = load_final(conn, scenario_id, "final_article")
    if final_article is None:
        raise ValueError(f"final_article not found for scenario {scenario_id}")
    return run_seo(final_article, cfg, llm, conn=conn, scenario_id=scenario_id)


# --- editing & re-run ---------------------------------------------------------------

def save_edited_artifact(conn, scenario_id: int, kind: str, new_content_json: str, version: int = 1) -> int:
    """Overwrites an artifact in place (idempotent on (scenario_id, kind, version))."""
    return save_artifact(conn, scenario_id=scenario_id, kind=kind, content=new_content_json, version=version)


def rerun_from(scenario_id: int, stage_name: str, cfg: dict, llm: LLM, conn) -> dict:
    """Re-runs `stage_name` and everything downstream, reading DB artifacts.

    Returns a dict of the stage results actually (re)run, keyed by stage name.
    """
    if stage_name not in STAGE_ORDER:
        raise ValueError(f"unknown stage '{stage_name}', expected one of {STAGE_ORDER}")

    start = _stage_index(stage_name)
    results: dict = {}

    if start <= _stage_index("brief"):
        results["brief"] = run_brief_step(scenario_id, cfg, llm, conn)
    if start <= _stage_index("drafts"):
        results["drafts"] = run_drafts_step(scenario_id, cfg, llm, conn)
    if start <= _stage_index("finals"):
        results["finals"] = run_finals_step(scenario_id, cfg, llm, conn)
    if start <= _stage_index("meta"):
        results["meta"] = run_meta_step(scenario_id, cfg, llm, conn)

    return results
