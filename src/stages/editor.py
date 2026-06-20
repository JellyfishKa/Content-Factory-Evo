"""Editor stage STUB (T2.3 scaffold, real gate blocked on T3.1 validators).

Final signature: run_editor(draft, refs, cfg, llm) -> tuple[Final, list[CheckResult]]
Real implementation reads references/ and runs validators.run_all(final, cfg)
as the STYLE gate (T3.3). This stub skips the gate entirely
(style_passed=True, no validator calls) and saves the final_<kind> artifact.
"""
from __future__ import annotations

from db import save_artifact
from schemas import CheckResult, Draft, Final


def run_editor(draft: Draft, cfg: dict, llm, refs: list[str] | None = None, conn=None, scenario_id: int | None = None) -> tuple[Final, list[CheckResult]]:
    """STUB: passes the draft through unchanged, style_passed=True, no gate.

    Real implementation (blocked on validators.py T3.1 + writer.py T2.2):
    calls llm to polish the draft using refs, then validators.run_all(final, cfg)
    to populate checks and set style_passed accordingly.
    """
    final = Final(
        kind=draft.kind,
        title=draft.title,
        body=draft.body,
        style_passed=True,
    )
    checks: list[CheckResult] = []

    if conn is not None and scenario_id is not None:
        kind = "final_article" if draft.kind == "article" else "final_post"
        save_artifact(conn, scenario_id=scenario_id, kind=kind, content=final.model_dump_json())

    return final, checks
