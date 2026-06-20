"""Writer stage: scenario + brief -> article draft + post draft.

Placeholder body returns valid Draft objects and saves both artifacts.
TODO: drive it from prompts/writer.md via llm.complete_json with length
limits from cfg["limits"], keeping the signature.
"""
from __future__ import annotations

from db import save_artifact
from schemas import Brief, Draft, Scenario


def run_writer(scenario: Scenario, brief: Brief, cfg: dict, llm, conn=None) -> tuple[Draft, Draft]:
    """Returns placeholder article + post drafts without calling the LLM."""
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
