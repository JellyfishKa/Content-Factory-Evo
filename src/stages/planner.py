"""Planner stage: splits a Source into exactly N Scenario objects.

The planner prompt (prompts/planner.md) deliberately says nothing about
style, length, or SEO — those rules live in later stages and in config.yaml.
This is the anti-overfitting guard for the planner itself.
"""
from __future__ import annotations

from pathlib import Path

from llm import LLM, LLMContractError
from schemas import Scenario, Source

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "planner.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(source: Source, n: int) -> str:
    if source.type == "topic":
        mode_line = f"Разверни ТЕМУ в ровно {n} самостоятельных сценариев."
    else:
        mode_line = f"Разбей ИСТОЧНИК на ровно {n} сценариев."
    return (
        f"N = {n}\n\n"
        f"Тип источника: {source.type}\n\n"
        f"{mode_line}\n\n"
        f"Текст источника:\n{source.text}"
    )


def _parse_scenarios(raw: list | dict, n: int) -> list[Scenario]:
    if isinstance(raw, dict):
        # Some models wrap the array under a key like {"scenarios": [...]}.
        for value in raw.values():
            if isinstance(value, list):
                raw = value
                break

    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON array, got {type(raw)}")

    if len(raw) != n:
        raise ValueError(f"expected exactly {n} scenarios, got {len(raw)}")

    scenarios = []
    for i, item in enumerate(raw, start=1):
        scenarios.append(
            Scenario(
                idx=i,
                topic=item["topic"],
                angle=item["angle"],
                brief_hint=item["brief_hint"],
            )
        )
    return scenarios


def run_planner(source: Source, cfg: dict, llm: LLM, n: int | None = None) -> list[Scenario]:
    """Splits `source` into exactly `n` scenarios using the planner LLM.

    Validates len(list) == n; on failure does 1 retry, then raises
    LLMContractError. Caller is responsible for persisting scenarios to DB.
    """
    n = n or cfg["run"]["scenarios"]
    system = _load_prompt()
    user = _build_user_message(source, n)
    model = cfg["models"]["planner"]

    try:
        raw = llm.complete_json(model, system, user)
        return _parse_scenarios(raw, n)
    except (LLMContractError, ValueError, KeyError) as exc:
        # 1 retry with an explicit reminder of the count requirement.
        retry_user = user + f"\n\nНапоминание: верни ровно {n} объектов в JSON-массиве."
        try:
            raw_retry = llm.complete_json(model, system, retry_user)
            return _parse_scenarios(raw_retry, n)
        except (LLMContractError, ValueError, KeyError) as exc_retry:
            raise LLMContractError(
                f"planner failed to produce {n} valid scenarios after 1 retry: {exc_retry}"
            ) from exc
