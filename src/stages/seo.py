"""SEO stage: final text -> Meta.

Placeholder body returns a valid Meta and saves the meta artifact.
TODO: drive it from prompts/seo.md via llm.complete_json, keeping the
signature.
"""
from __future__ import annotations

import re

from db import save_artifact
from schemas import Final, Meta


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def run_seo(final: Final, cfg: dict, llm, conn=None, scenario_id: int | None = None) -> Meta:
    """Returns placeholder Meta without calling the LLM."""
    meta = Meta(
        title=final.title,
        description=f"[stub] описание для '{final.title}'",
        keywords=["stub"],
        slug=_slugify(final.title),
        og_title=final.title,
        og_description=f"[stub] og-описание для '{final.title}'",
        tags=["stub"],
    )
    if conn is not None and scenario_id is not None:
        save_artifact(conn, scenario_id=scenario_id, kind="meta", content=meta.model_dump_json())
    return meta
