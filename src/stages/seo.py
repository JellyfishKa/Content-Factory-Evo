"""SEO stage STUB (T2.4 scaffold for teammate).

Final signature: run_seo(final, cfg, llm) -> Meta
Real implementation (prompts/seo.md + LLM call) is the teammate's task.
This stub returns a valid placeholder Meta and saves the meta artifact.
"""
from __future__ import annotations

import re

from db import save_artifact
from schemas import Final, Meta


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def run_seo(final: Final, cfg: dict, llm, conn=None, scenario_id: int | None = None) -> Meta:
    """STUB: returns placeholder Meta without calling the LLM.

    Teammate replaces the body with a real prompts/seo.md-driven
    llm.complete_json call, keeping this signature.
    """
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
