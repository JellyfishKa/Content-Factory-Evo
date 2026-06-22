"""Markdown rendering and parse-back for artifacts (Brief, Draft, Final, Meta).

The DB keeps the JSON (model_dump_json) as the lossless source of truth;
this module is purely about presenting/editing artifacts as Markdown in the
Streamlit panel and writing them to disk under runs/ (see artifacts.py).

to_md(kind, obj) renders one of:
  - brief                       -> Факты / Подтверждено / Не подтверждено / Цитаты
  - draft_article, draft_post   -> "# title\n\nbody"
  - final_article, final_post   -> "# title\n\nbody"
  - meta                        -> YAML frontmatter + short body

from_md(kind, md, prev=None) parses Markdown back into the matching Pydantic
model. For draft/final kinds this is a best-effort split on the first "# "
heading line; for Final, style_passed/kind are preserved from `prev`. For
brief/meta it parses by section headers / YAML frontmatter.
"""
from __future__ import annotations

import yaml

from schemas import Brief, Draft, Final, Meta

DRAFT_KINDS = {"draft_article", "draft_post"}
FINAL_KINDS = {"final_article", "final_post"}


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "_(нет данных)_"
    return "\n".join(f"- {item}" for item in items)


def _parse_bullets(block: str) -> list[str]:
    items = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.startswith("_(нет данных)_"):
            continue
        else:
            items.append(line)
    return items


def _brief_to_md(brief: Brief) -> str:
    return (
        f"## Факты\n{_bullet_list(brief.facts)}\n\n"
        f"## Подтверждено\n{_bullet_list(brief.confirmed)}\n\n"
        f"## Не подтверждено\n{_bullet_list(brief.not_confirmed)}\n\n"
        f"## Цитаты\n{_bullet_list(brief.quotes)}\n"
    )


def _md_to_brief(md: str) -> Brief:
    sections = _split_sections(md)
    return Brief(
        facts=_parse_bullets(sections.get("факты", "")),
        confirmed=_parse_bullets(sections.get("подтверждено", "")),
        not_confirmed=_parse_bullets(sections.get("не подтверждено", "")),
        quotes=_parse_bullets(sections.get("цитаты", "")),
    )


def _split_sections(md: str) -> dict[str, str]:
    """Splits a markdown doc on "## " headings into {lowercased title: body}."""
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = line[3:].strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    if current_title is not None:
        sections[current_title] = "\n".join(current_lines).strip()
    return sections


def _prose_to_md(title: str, body: str) -> str:
    return f"# {title}\n\n{body}\n"


def _md_to_title_body(md: str) -> tuple[str, str]:
    lines = md.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip("\n")
    return title, body


def _meta_to_md(meta: Meta) -> str:
    frontmatter = {
        "title": meta.title,
        "description": meta.description,
        "slug": meta.slug,
        "keywords": meta.keywords,
        "og_title": meta.og_title,
        "og_description": meta.og_description,
        "tags": meta.tags,
    }
    yaml_block = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    return (
        f"---\n{yaml_block}\n---\n\n"
        f"# {meta.title}\n\n"
        f"{meta.description}\n"
    )


def _md_to_meta(md: str) -> Meta:
    frontmatter, _ = _split_frontmatter(md)
    return Meta(
        title=frontmatter.get("title", ""),
        description=frontmatter.get("description", ""),
        keywords=frontmatter.get("keywords") or [],
        slug=frontmatter.get("slug", ""),
        og_title=frontmatter.get("og_title", frontmatter.get("title", "")),
        og_description=frontmatter.get("og_description", frontmatter.get("description", "")),
        tags=frontmatter.get("tags") or [],
    )


def _split_frontmatter(md: str) -> tuple[dict, str]:
    stripped = md.lstrip("\n")
    if not stripped.startswith("---"):
        return {}, md
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, md
    _, frontmatter_raw, rest = parts
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, rest.strip("\n")


def to_md(kind: str, obj) -> str:
    """Renders an artifact object as Markdown for display/editing.

    kind is one of: brief, draft_article, draft_post, final_article,
    final_post, meta.
    """
    if kind == "brief":
        return _brief_to_md(obj)
    if kind in DRAFT_KINDS or kind in FINAL_KINDS:
        return _prose_to_md(obj.title, obj.body)
    if kind == "meta":
        return _meta_to_md(obj)
    raise ValueError(f"unknown artifact kind for to_md: {kind!r}")


def md_to_brief(md: str) -> Brief:
    return _md_to_brief(md)


def md_to_draft(md: str, kind: str) -> Draft:
    """kind is 'draft_article' or 'draft_post'."""
    draft_kind = "article" if kind == "draft_article" else "post"
    title, body = _md_to_title_body(md)
    return Draft(kind=draft_kind, title=title, body=body)


def md_to_final(md: str, kind: str, prev_final: Final | None = None) -> Final:
    """kind is 'final_article' or 'final_post'. Preserves prev_final.style_passed."""
    final_kind = "article" if kind == "final_article" else "post"
    title, body = _md_to_title_body(md)
    style_passed = prev_final.style_passed if prev_final is not None else False
    return Final(kind=final_kind, title=title, body=body, style_passed=style_passed)


def md_to_meta(md: str) -> Meta:
    return _md_to_meta(md)


def from_md(kind: str, md: str, prev=None):
    """Generic parse-back dispatcher mirroring to_md's kind argument."""
    if kind == "brief":
        return md_to_brief(md)
    if kind in DRAFT_KINDS:
        return md_to_draft(md, kind)
    if kind in FINAL_KINDS:
        return md_to_final(md, kind, prev_final=prev)
    if kind == "meta":
        return md_to_meta(md)
    raise ValueError(f"unknown artifact kind for from_md: {kind!r}")
