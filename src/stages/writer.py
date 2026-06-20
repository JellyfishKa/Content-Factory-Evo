"""Writer stage STUB (T2.2 scaffold for teammate).

Final signature: run_writer(scenario, brief, cfg, llm) -> tuple[Draft, Draft]
Real implementation (prompts/writer.md + LLM call, length limits from
config) is the teammate's task. This stub returns valid placeholder
Draft objects (article + post) and saves both artifacts.
"""
from __future__ import annotations

from db import save_artifact
from schemas import Brief, Draft, Scenario


def run_writer(scenario: Scenario, brief: Brief, cfg: dict, llm, conn=None) -> tuple[Draft, Draft]:
    """STUB: returns placeholder article + post drafts without calling the LLM.

    Teammate replaces the body with a real prompts/writer.md-driven
    llm.complete_json call, keeping this signature. Length limits come
    from cfg["limits"]["article_chars"] / cfg["limits"]["post_words"].
    """
    article = Draft(
        kind="article",
        title=f"[stub] {scenario.topic}",
        body=f"[stub] черновик статьи по теме '{scenario.topic}', угол: {scenario.angle}. "
        + " ".join(brief.facts),
    )
    post = Draft(
        kind="post",
        title=f"[stub] {scenario.topic}",
        body=f"[stub] черновик поста по теме '{scenario.topic}'.",
    )
    if conn is not None:
        save_artifact(conn, scenario_id=scenario.idx, kind="draft_article", content=article.model_dump_json())
        save_artifact(conn, scenario_id=scenario.idx, kind="draft_post", content=post.model_dump_json())
    return article, post
