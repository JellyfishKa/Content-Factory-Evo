"""Researcher stage STUB (T2.1 scaffold for teammate).

Final signature: run_researcher(scenario, cfg, llm) -> Brief
Real implementation (prompts/researcher.md + LLM call) is the teammate's
task. This stub returns a valid placeholder Brief and saves the artifact
so the orchestrator can run end-to-end before the teammate's work lands.
"""
from __future__ import annotations

from db import save_artifact
from schemas import Brief, Scenario


def run_researcher(scenario: Scenario, cfg: dict, llm, conn=None) -> Brief:
    """STUB: returns a placeholder Brief without calling the LLM.

    Teammate replaces the body with a real prompts/researcher.md-driven
    llm.complete_json call, keeping this signature.
    """
    brief = Brief(
        facts=[f"[stub] факт по теме: {scenario.topic}"],
        confirmed=[],
        not_confirmed=[f"[stub] нет реальных данных для сценария {scenario.idx}"],
        quotes=[],
    )
    if conn is not None:
        save_artifact(conn, scenario_id=scenario.idx, kind="brief", content=brief.model_dump_json())
    return brief
