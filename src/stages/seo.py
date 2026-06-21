"""SEO stage: final text -> Meta.

Drives the SEO agent from prompts/seo.md via llm.complete_json to generate
title, description, keywords, slug, and open graph tags based on the final text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.db import save_artifact
from src.schemas import Final, Meta


def _fallback_slugify(title: str) -> str:
    """Fallback slug generator in case LLM fails to provide a valid one."""
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def run_seo(final: Final, cfg: dict, llm, conn=None, scenario_id: int | None = None) -> Meta:
    """Runs the SEO stage using LLM to generate metadata from Final text."""

    prompt_path = Path("prompts/seo.md")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    system_prompt = prompt_path.read_text(encoding="utf-8")

    final_text_content = getattr(final, "body", getattr(final, "text", getattr(final, "content", "")))

    user_prompt = (
        f"ГОТОВЫЙ ТЕКСТ СТАТЬИ ДЛЯ АНАЛИЗА:\n"
        f"Заголовок: {final.title}\n\n"
        f"Текст:\n{final_text_content}\n"
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

    generated_slug = parsed_data.get("slug", "")
    if not generated_slug or " " in generated_slug:
        generated_slug = _fallback_slugify(parsed_data.get("title", final.title))

    meta = Meta(
        title=parsed_data.get("title", final.title),
        description=parsed_data.get("description", ""),
        keywords=parsed_data.get("keywords", []),
        slug=generated_slug,
        og_title=parsed_data.get("og_title", parsed_data.get("title", final.title)),
        og_description=parsed_data.get("og_description", parsed_data.get("description", "")),
        tags=parsed_data.get("tags", [])
    )

    if conn is not None and scenario_id is not None:
        save_artifact(
            conn,
            scenario_id=scenario_id,
            kind="meta",
            content=meta.model_dump_json()
        )

    return meta
