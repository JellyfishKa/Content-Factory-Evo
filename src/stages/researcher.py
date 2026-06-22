"""Researcher stage: scenario -> Brief.

Generates a research Brief based on a specific Scenario and source text,
strictly avoiding hallucinations. Uses prompts/researcher.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.db import save_artifact
from src.schemas import Brief, Scenario

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "researcher.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(scenario: Scenario) -> str:
    return (
        f"Тема: {scenario.topic}\n"
        f"Ракурс: {scenario.angle}\n"
        f"Подсказка для брифа: {scenario.brief_hint}"
    )


def run_researcher(scenario: Scenario, cfg: dict, llm, conn=None) -> Brief:
    """Runs the researcher stage using LLM to generate a Brief from a Scenario."""

    prompt_path = Path("prompts/researcher.md")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    system_prompt = prompt_path.read_text(encoding="utf-8")

    source_text = cfg.get("source_text", "")

    user_prompt = (
        f"Текст источника:\n{source_text}\n\n"
        f"--- \n"
        f"Сценарий для брифа:\n"
        f"Тема (topic): {scenario.topic}\n"
        f"Ракурс (angle): {scenario.angle}\n"
        f"Подсказка (brief_hint): {scenario.brief_hint}\n"
    )

    raw_response = llm.complete_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt
    )

    if isinstance(raw_response, str):
        try:
            parsed_data = json.loads(raw_response)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM returned invalid JSON: {raw_response}") from e
    else:
        parsed_data = raw_response

    brief = Brief(**parsed_data)

    if conn is not None:
        save_artifact(
            conn,
            scenario_id=scenario.idx,
            kind="brief",
            content=brief.model_dump_json()
        )

    return brief
