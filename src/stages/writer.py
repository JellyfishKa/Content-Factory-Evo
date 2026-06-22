"""Writer stage: scenario + brief -> article draft + post draft.

Calls the LLM with prompts/writer.md, injecting length limits from
cfg["limits"] into the prompt template (never hardcoded in the prompt
file), then parses the response into two Draft objects and saves both
artifacts. Hard rule from the prompt: never add facts beyond the brief.
"""
from __future__ import annotations

from pathlib import Path

from artifacts import persist_artifact
from llm import LLM, LLMContractError
from schemas import Brief, Draft, Scenario

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "writer.md"


def _load_prompt(cfg: dict) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    limits = cfg.get("limits", {})
    article_lo, article_hi = limits.get("article_chars", [800, 1100])
    post_lo, post_hi = limits.get("post_words", [100, 300])
    # .replace (not .format): the prompt contains literal JSON braces ({...})
    # that str.format would choke on.
    replacements = {
        "{article_min}": str(article_lo),
        "{article_max}": str(article_hi),
        "{post_min}": str(post_lo),
        "{post_max}": str(post_hi),
    }
    for ph, val in replacements.items():
        template = template.replace(ph, val)
    return template


def _build_user_message(scenario: Scenario, brief: Brief) -> str:
    return (
        f"Тема: {scenario.topic}\n"
        f"Ракурс: {scenario.angle}\n"
        f"Подсказка: {scenario.brief_hint}\n\n"
        f"Бриф:\n"
        f"Факты: {brief.facts}\n"
        f"Подтверждено: {brief.confirmed}\n"
        f"Не подтверждено: {brief.not_confirmed}\n"
        f"Цитаты: {brief.quotes}"
    )


def _parse_drafts(raw: dict) -> tuple[Draft, Draft]:
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object with 'article' and 'post', got {raw!r}")

    article_raw = raw.get("article")
    post_raw = raw.get("post")
    # Some models nest "post" inside "article" instead of at the top level.
    if isinstance(article_raw, dict) and post_raw is None and isinstance(article_raw.get("post"), dict):
        post_raw = article_raw["post"]
        article_raw = {k: v for k, v in article_raw.items() if k != "post"}

    if not isinstance(article_raw, dict) or not isinstance(post_raw, dict):
        raise ValueError(f"expected 'article' and 'post' objects, got {raw!r}")

    article = Draft(kind="article", title=article_raw["title"], body=article_raw["body"])
    post = Draft(kind="post", title=post_raw["title"], body=post_raw["body"])
    return article, post


def run_writer(
    scenario: Scenario, brief: Brief, cfg: dict, llm: LLM, conn=None
) -> tuple[Draft, Draft]:
    """Builds article + post drafts for one scenario via the writer LLM.

    If `conn` is given, saves both artifacts (kind="draft_article",
    kind="draft_post") keyed by scenario.idx, matching the current
    orchestrator call site.
    """
    system = _load_prompt(cfg)
    user = _build_user_message(scenario, brief)
    model = cfg["models"]["writer"]

    try:
        raw = llm.complete_json(model, system, user)
        article, post = _parse_drafts(raw)
    except (LLMContractError, ValueError, KeyError, TypeError) as exc:
        raise LLMContractError(f"writer failed to produce valid drafts for '{scenario.topic}': {exc}") from exc

    if conn is not None:
        persist_artifact(conn, scenario_id=scenario.idx, kind="draft_article", obj=article)
        persist_artifact(conn, scenario_id=scenario.idx, kind="draft_post", obj=post)

    return article, post
