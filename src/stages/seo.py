"""SEO stage: final text -> Meta.

Calls the LLM with prompts/seo.md, parses the strict JSON response into a
Meta object, and saves the meta artifact.
"""
from __future__ import annotations

import re
from pathlib import Path

from artifacts import persist_artifact
from llm import LLM, LLMContractError
from schemas import Final, Meta

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "seo.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_message(final: Final) -> str:
    return f"Заголовок: {final.title}\n\nТекст:\n{final.body}"


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def _parse_meta(raw: dict, final: Final) -> Meta:
    if not isinstance(raw, dict) or "title" not in raw or "description" not in raw:
        raise ValueError(f"expected a JSON object with seo fields, got {raw!r}")

    slug = raw.get("slug") or _slugify(raw["title"])
    return Meta(
        title=raw["title"],
        description=raw["description"],
        keywords=raw.get("keywords", []),
        slug=slug,
        og_title=raw.get("og_title", raw["title"]),
        og_description=raw.get("og_description", raw["description"]),
        tags=raw.get("tags", []),
    )


def run_seo(final: Final, cfg: dict, llm: LLM, conn=None, scenario_id: int | None = None) -> Meta:
    """Builds SEO metadata for one finalized piece via the seo LLM.

    If `conn` and `scenario_id` are given, saves the meta artifact.
    """
    system = _load_prompt()
    user = _build_user_message(final)
    model = cfg["models"]["seo"]

    try:
        raw = llm.complete_json(model, system, user)
        meta = _parse_meta(raw, final)
    except (LLMContractError, ValueError, KeyError, TypeError) as exc:
        raise LLMContractError(f"seo failed to produce valid metadata for '{final.title}': {exc}") from exc

    if conn is not None and scenario_id is not None:
        persist_artifact(conn, scenario_id=scenario_id, kind="meta", obj=meta)

    return meta
