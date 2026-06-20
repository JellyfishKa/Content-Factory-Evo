"""Resumable orchestrator for content-factory-lite.

Minimal mode (no --input): reads config.yaml, prints key values and "pipeline OK".
Full mode (--input given): runs planner -> per scenario researcher/writer/editor/seo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="content-factory-lite orchestrator")
    parser.add_argument("--input", help="path to source file under inputs/", default=None)
    parser.add_argument("--scenarios", type=int, default=None, help="override number of scenarios")
    parser.add_argument("--from-stage", default=None, help="force re-run starting from this stage")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config()

    print(f"config: scenarios={cfg['run']['scenarios']} language={cfg['run']['language']}")
    print(f"models: {cfg['models']}")

    if not args.input:
        print("pipeline OK")
        return 0

    # Full resumable run is implemented in run_pipeline (see below).
    return run_pipeline(args, cfg)


def run_pipeline(args: argparse.Namespace, cfg: dict) -> int:
    from db import (
        init_db,
        get_connection,
        create_run,
        upsert_scenario,
        get_artifact,
    )
    from schemas import Source
    from llm import LLM
    from stages.planner import run_planner
    from stages.researcher import run_researcher
    from stages.writer import run_writer
    from stages.editor import run_editor
    from stages.seo import run_seo

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}")
        return 1

    n_scenarios = args.scenarios or cfg["run"]["scenarios"]
    from_stage = args.from_stage

    stage_order = ["planner", "researcher", "writer", "editor", "seo"]
    if from_stage and from_stage not in stage_order:
        print(f"ERROR: unknown --from-stage '{from_stage}', expected one of {stage_order}")
        return 1

    def stage_index(name: str) -> int:
        return stage_order.index(name)

    force_from = stage_index(from_stage) if from_stage else None

    def should_force(stage_name: str) -> bool:
        """True if this stage must be (re)run even if an artifact already exists."""
        if force_from is None:
            return False
        return stage_index(stage_name) >= force_from

    init_db()
    conn = get_connection()

    source_text = input_path.read_text(encoding="utf-8")
    source = Source(type="transcript", ref=str(input_path), text=source_text)

    llm = LLM(cfg=cfg)

    run_id = create_run(conn, source_type=source.type, source_ref=source.ref)

    built: list[str] = []
    failed: list[str] = []
    style_results: list[tuple[str, bool]] = []

    # --- planner ---
    try:
        scenarios = run_planner(source, cfg, llm, n=n_scenarios)
        scenario_ids: dict[int, int] = {}
        for sc in scenarios:
            scenario_ids[sc.idx] = upsert_scenario(
                conn,
                run_id=run_id,
                idx=sc.idx,
                topic=sc.topic,
                angle=sc.angle,
                brief_hint=sc.brief_hint,
            )
        built.append(f"planner: {len(scenarios)} scenarios")
    except Exception as exc:  # LLMContractError or validation error
        print(f"ERROR in stage 'planner': {exc}")
        failed.append("planner")
        _print_summary(built, failed, style_results)
        return 1

    for scenario in scenarios:
        scenario_id = scenario_ids[scenario.idx]

        try:
            existing = get_artifact(conn, scenario_id=scenario_id, kind="brief")
            if existing and not should_force("researcher"):
                built.append(f"scenario {scenario_id}: brief (skipped, exists)")
                brief = run_researcher(scenario, cfg, llm)
            else:
                brief = run_researcher(scenario, cfg, llm, conn=conn)
                built.append(f"scenario {scenario_id}: brief")
        except Exception as exc:
            print(f"ERROR in stage 'researcher' (scenario {scenario_id}): {exc}")
            failed.append(f"scenario {scenario_id}: researcher")
            continue

        try:
            existing_article = get_artifact(conn, scenario_id=scenario_id, kind="draft_article")
            existing_post = get_artifact(conn, scenario_id=scenario_id, kind="draft_post")
            if existing_article and existing_post and not should_force("writer"):
                built.append(f"scenario {scenario_id}: draft_article, draft_post (skipped, exists)")
                draft_article, draft_post = run_writer(scenario, brief, cfg, llm)
            else:
                draft_article, draft_post = run_writer(scenario, brief, cfg, llm, conn=conn)
                built.append(f"scenario {scenario_id}: draft_article, draft_post")
        except Exception as exc:
            print(f"ERROR in stage 'writer' (scenario {scenario_id}): {exc}")
            failed.append(f"scenario {scenario_id}: writer")
            continue

        try:
            existing_final_article = get_artifact(conn, scenario_id=scenario_id, kind="final_article")
            existing_final_post = get_artifact(conn, scenario_id=scenario_id, kind="final_post")
            if existing_final_article and existing_final_post and not should_force("editor"):
                built.append(f"scenario {scenario_id}: final_article, final_post (skipped, exists)")
                final_article, checks_article = run_editor(draft_article, cfg, llm)
                final_post, checks_post = run_editor(draft_post, cfg, llm)
            else:
                final_article, checks_article = run_editor(draft_article, cfg, llm, conn=conn, scenario_id=scenario_id)
                final_post, checks_post = run_editor(draft_post, cfg, llm, conn=conn, scenario_id=scenario_id)
                built.append(f"scenario {scenario_id}: final_article, final_post")
            style_results.append((f"scenario {scenario_id}: article", final_article.style_passed))
            style_results.append((f"scenario {scenario_id}: post", final_post.style_passed))
        except Exception as exc:
            print(f"ERROR in stage 'editor' (scenario {scenario_id}): {exc}")
            failed.append(f"scenario {scenario_id}: editor")
            continue

        try:
            existing_meta = get_artifact(conn, scenario_id=scenario_id, kind="meta")
            if existing_meta and not should_force("seo"):
                built.append(f"scenario {scenario_id}: meta (skipped, exists)")
                meta = run_seo(final_article, cfg, llm)
            else:
                meta = run_seo(final_article, cfg, llm, conn=conn, scenario_id=scenario_id)
                built.append(f"scenario {scenario_id}: meta")
        except Exception as exc:
            print(f"ERROR in stage 'seo' (scenario {scenario_id}): {exc}")
            failed.append(f"scenario {scenario_id}: seo")
            continue

    _print_summary(built, failed, style_results)
    return 0 if not failed else 1


def _print_summary(built: list[str], failed: list[str], style_results: list[tuple[str, bool]]) -> None:
    print("\n=== SUMMARY ===")
    print(f"Built ({len(built)}):")
    for item in built:
        print(f"  - {item}")
    if failed:
        print(f"Failed ({len(failed)}):")
        for item in failed:
            print(f"  - {item}")
    if style_results:
        passed = sum(1 for _, ok in style_results if ok)
        total = len(style_results)
        failed_names = [name for name, ok in style_results if not ok]
        failed_str = ", ".join(failed_names) if failed_names else "none"
        print(f"{passed}/{total} passed STYLE, failed: {failed_str}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
