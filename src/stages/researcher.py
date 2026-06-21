"""Researcher stage: scenario -> Brief.

Calls the LLM with prompts/researcher.md, parses the response into a
Brief, and saves it as artifact kind="brief". Hard rule from the prompt:
never invent facts — anything uncertain goes into not_confirmed.
"""
from __future__ import annotations

from pathlib import Path

from db import save_artifact
from llm import LLM, LLMContractError
from schemas import Brief, Scenario

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "researcher.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(scenario: Scenario) -> str:
    return (
        f"Тема: {scenario.topic}\n"
        f"Ракурс: {scenario.angle}\n"
        f"Подсказка для брифа: {scenario.brief_hint}"
    )


def _parse_brief(raw: dict) -> Brief:
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw)}")
    return Brief(
        facts=raw.get("facts", []),
        confirmed=raw.get("confirmed", []),
        not_confirmed=raw.get("not_confirmed", []),
        quotes=raw.get("quotes", []),
    )


def run_researcher(scenario: Scenario, cfg: dict, llm: LLM, conn=None, scenario_id: int | None = None) -> Brief:
    """Builds a Brief for one scenario via the researcher LLM.

    If `conn` is given, saves the brief as artifact kind="brief". Falls back
    to scenario.idx when scenario_id is not given, matching the current
    orchestrator call site (stages/orchestrator wiring is out of scope here).
    """
    system = _load_prompt()
    user = _build_user_message(scenario)
    model = cfg["models"]["researcher"]

    try:
        raw = llm.complete_json(model, system, user)
        brief = _parse_brief(raw)
    except (LLMContractError, ValueError, KeyError) as exc:
        raise LLMContractError(f"researcher failed to produce a valid brief for '{scenario.topic}': {exc}") from exc

    if conn is not None:
        save_artifact(conn, scenario_id=scenario_id or scenario.idx, kind="brief", content=brief.model_dump_json())

    return brief
