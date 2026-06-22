"""Writes artifacts to disk as Markdown alongside the DB JSON record.

The DB keeps content as JSON (lossless source of truth, via db.save_artifact).
persist_artifact additionally renders the object as Markdown (markdownio.to_md)
and writes it under runs/, then stores that path on the artifact row so the
panel/viewer can read the same file the pipeline wrote.

Layout: runs/run_<run_id>/scenario_<idx>/<kind>.md when run_id/idx are known,
else runs/scenario_<scenario_id>/<kind>.md as a fallback.
"""
from __future__ import annotations

from pathlib import Path

from db import save_artifact
from markdownio import to_md

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


def _artifact_path(scenario_id: int, kind: str, run_id: int | None, idx: int | None) -> Path:
    if run_id is not None and idx is not None:
        return RUNS_DIR / f"run_{run_id}" / f"scenario_{idx}" / f"{kind}.md"
    return RUNS_DIR / f"scenario_{scenario_id}" / f"{kind}.md"


def _lookup_run_and_idx(conn, scenario_id: int) -> tuple[int | None, int | None]:
    """Best-effort lookup of (run_id, idx) for a scenario; None, None if conn lacks raw SQL access."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT run_id, idx FROM scenarios WHERE id = %s",
                (scenario_id,),
            )
            row = cur.fetchone()
    except Exception:  # noqa: BLE001 - fall back gracefully (e.g. fake conns in tests)
        return None, None
    if row is None:
        return None, None
    return row["run_id"], row["idx"]


def persist_artifact(
    conn,
    scenario_id: int,
    kind: str,
    obj,
    run_id: int | None = None,
    idx: int | None = None,
    version: int = 1,
) -> int:
    """Renders `obj` to Markdown, writes it to disk, and saves the DB row.

    Returns the artifact id. DB content stays JSON (obj.model_dump_json());
    `path` points at the .md file written alongside it.
    """
    if run_id is None or idx is None:
        looked_up_run_id, looked_up_idx = _lookup_run_and_idx(conn, scenario_id)
        run_id = run_id if run_id is not None else looked_up_run_id
        idx = idx if idx is not None else looked_up_idx

    path = _artifact_path(scenario_id, kind, run_id, idx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_md(kind, obj), encoding="utf-8")

    return save_artifact(
        conn,
        scenario_id=scenario_id,
        kind=kind,
        content=obj.model_dump_json(),
        path=str(path),
        version=version,
    )
