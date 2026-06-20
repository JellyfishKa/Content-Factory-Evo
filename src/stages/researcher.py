"""Researcher stage: scenario -> Brief.

Placeholder body returns a valid Brief and saves the artifact so the
pipeline runs end-to-end. TODO: drive it from prompts/researcher.md via
llm.complete_json, keeping the signature.
"""
from __future__ import annotations

from db import save_artifact
from schemas import Brief, Scenario


def run_researcher(scenario: Scenario, cfg: dict, llm, conn=None) -> Brief:
    """Returns a placeholder Brief without calling the LLM."""
    brief = Brief(
        facts=[f"[stub] факт по теме: {scenario.topic}"],
        confirmed=[],
        not_confirmed=[f"[stub] нет реальных данных для сценария {scenario.idx}"],
        quotes=[],
    )
    if conn is not None:
        save_artifact(conn, scenario_id=scenario.idx, kind="brief", content=brief.model_dump_json())
    return brief
