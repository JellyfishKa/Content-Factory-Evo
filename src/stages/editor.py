"""Editor stage: draft -> final, gated by validators.run_all.

Generates the final text via LLM (prompts/editor.md, consulting refs from
references/), then runs the STYLE gate. A failed check never gets silently
fixed: style_passed is set to False, checks are persisted, and a clear
message is logged.
"""
from __future__ import annotations

from pathlib import Path

import validators
from db import save_artifact, save_check
from llm import LLM, LLMContractError
from schemas import CheckResult, Draft, Final

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "editor.md"
REFERENCES_DIR = Path(__file__).resolve().parent.parent.parent / "references"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_references() -> list[str]:
    """Reads all .md files from references/, creating the dir + a sample if missing."""
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    md_files = sorted(REFERENCES_DIR.glob("*.md"))
    if not md_files:
        sample_path = REFERENCES_DIR / "sample.md"
        sample_path.write_text(
            "# Референс тона: короткие абзацы, цифра в начале\n\n"
            "В 2024 году рынок отгрузок выросло на 18%. Дальше — конкретика, "
            "без вступлений и предисловий.\n",
            encoding="utf-8",
        )
        md_files = [sample_path]
    return [p.read_text(encoding="utf-8") for p in md_files]


def _build_user_message(draft: Draft, refs: list[str]) -> str:
    refs_block = "\n\n---\n\n".join(refs) if refs else "(референсы отсутствуют)"
    return (
        f"Черновик ({draft.kind}):\n"
        f"Заголовок: {draft.title}\n"
        f"Текст: {draft.body}\n\n"
        f"Референсы тона/структуры:\n{refs_block}"
    )


def run_editor(
    draft: Draft,
    cfg: dict,
    llm: LLM,
    refs: list[str] | None = None,
    conn=None,
    scenario_id: int | None = None,
) -> tuple[Final, list[CheckResult]]:
    """Generates the final via LLM, then runs the STYLE gate.

    On a failed check: final.style_passed=False, checks saved to DB if
    conn/scenario_id given, and a clear log line - never auto-fixed.
    """
    refs = refs if refs is not None else load_references()
    system = _load_prompt()
    user = _build_user_message(draft, refs)
    model = cfg["models"]["editor"]

    try:
        raw = llm.complete_json(model, system, user)
    except LLMContractError as exc:
        raise LLMContractError(f"editor failed to produce a valid final for '{draft.title}': {exc}") from exc

    if not isinstance(raw, dict) or "title" not in raw or "body" not in raw:
        raise LLMContractError(
            f"editor returned a malformed contract for '{draft.title}': expected a JSON object "
            f"with 'title' and 'body', got {raw!r}"
        )

    title = raw["title"]
    body = raw["body"]

    final = Final(kind=draft.kind, title=title, body=body, style_passed=False)
    checks = validators.run_all(final, cfg)
    final.style_passed = all(c.passed for c in checks)

    if not final.style_passed:
        failed_rules = [c.rule for c in checks if not c.passed]
        print(f"[editor] STYLE gate FAILED for '{final.title}' ({draft.kind}): {', '.join(failed_rules)}")

    if conn is not None and scenario_id is not None:
        kind = "final_article" if draft.kind == "article" else "final_post"
        artifact_id = save_artifact(conn, scenario_id=scenario_id, kind=kind, content=final.model_dump_json())
        for check in checks:
            save_check(conn, artifact_id=artifact_id, rule=check.rule, passed=check.passed, detail=check.detail)

    return final, checks
