"""Round-trip tests for markdownio.to_md / from_md.

No DB/LLM needed - builds Pydantic objects directly and checks that
rendering to Markdown then parsing back preserves the key fields, and
that prose kinds never leak raw JSON braces.
"""
from __future__ import annotations

import markdownio
from schemas import Brief, Draft, Final, Meta


def test_draft_round_trip():
    draft = Draft(kind="article", title="Заголовок статьи", body="Текст статьи про тему.")
    md = markdownio.to_md("draft_article", draft)

    assert "{" not in md and "}" not in md
    assert md.startswith("# Заголовок статьи")

    parsed = markdownio.md_to_draft(md, "draft_article")
    assert parsed.kind == "article"
    assert parsed.title == draft.title
    assert parsed.body == draft.body


def test_draft_post_round_trip():
    draft = Draft(kind="post", title="Заголовок поста", body="Текст поста.")
    md = markdownio.to_md("draft_post", draft)
    parsed = markdownio.md_to_draft(md, "draft_post")
    assert parsed.kind == "post"
    assert parsed.title == draft.title
    assert parsed.body == draft.body


def test_final_round_trip_preserves_style_passed():
    final = Final(kind="article", title="Финальный заголовок", body="Финальный текст.", style_passed=True)
    md = markdownio.to_md("final_article", final)

    assert "{" not in md and "}" not in md

    parsed = markdownio.md_to_final(md, "final_article", prev_final=final)
    assert parsed.title == final.title
    assert parsed.body == final.body
    assert parsed.style_passed is True
    assert parsed.kind == "article"


def test_final_round_trip_without_prev_defaults_style_passed_false():
    final = Final(kind="post", title="Заголовок", body="Текст.", style_passed=True)
    md = markdownio.to_md("final_post", final)
    parsed = markdownio.md_to_final(md, "final_post")
    assert parsed.style_passed is False


def test_brief_round_trip():
    brief = Brief(
        facts=["факт1", "факт2"],
        confirmed=["подтверждено1"],
        not_confirmed=["не подтверждено1"],
        quotes=["цитата1"],
    )
    md = markdownio.to_md("brief", brief)

    assert "## Факты" in md
    assert "## Подтверждено" in md
    assert "## Не подтверждено" in md
    assert "## Цитаты" in md

    parsed = markdownio.md_to_brief(md)
    assert parsed.facts == brief.facts
    assert parsed.confirmed == brief.confirmed
    assert parsed.not_confirmed == brief.not_confirmed
    assert parsed.quotes == brief.quotes


def test_brief_round_trip_with_empty_sections():
    brief = Brief(facts=["факт1"], confirmed=[], not_confirmed=[], quotes=[])
    md = markdownio.to_md("brief", brief)
    parsed = markdownio.md_to_brief(md)
    assert parsed.facts == ["факт1"]
    assert parsed.confirmed == []
    assert parsed.not_confirmed == []
    assert parsed.quotes == []


def test_meta_round_trip():
    meta = Meta(
        title="SEO заголовок",
        description="SEO описание",
        keywords=["ключ1", "ключ2"],
        slug="seo-slug",
        og_title="OG заголовок",
        og_description="OG описание",
        tags=["тег1"],
    )
    md = markdownio.to_md("meta", meta)

    assert md.startswith("---\n")
    assert "title:" in md
    assert "slug: seo-slug" in md

    parsed = markdownio.md_to_meta(md)
    assert parsed.title == meta.title
    assert parsed.description == meta.description
    assert parsed.keywords == meta.keywords
    assert parsed.slug == meta.slug
    assert parsed.og_title == meta.og_title
    assert parsed.og_description == meta.og_description
    assert parsed.tags == meta.tags


def test_from_md_dispatches_by_kind():
    draft = Draft(kind="article", title="T", body="B")
    md = markdownio.to_md("draft_article", draft)
    parsed = markdownio.from_md("draft_article", md)
    assert isinstance(parsed, Draft)

    brief = Brief(facts=["f"], confirmed=[], not_confirmed=[], quotes=[])
    md = markdownio.to_md("brief", brief)
    parsed = markdownio.from_md("brief", md)
    assert isinstance(parsed, Brief)
