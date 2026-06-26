"""Streamlit human-in-the-loop control panel for content-factory-lite.

Sidebar: start a run (ready source or bare topic) via run_planner_step.
Main: per-scenario expanders with the full stage ladder (brief -> drafts ->
finals + STYLE -> meta), each artifact rendered as Markdown with an
expandable Markdown editor and a download button, plus the per-stage
"Перегенерировать" / "Сохранить правку" / "Запустить дальше" controls.

Reads/writes only through db.py + pipeline.py + markdownio.py - no stage
logic lives here.
"""
from __future__ import annotations

import streamlit as st

from db import create_run, get_connection, init_db
from enrich import enrich_article
from llm import LLM, LLMContractError
from markdownio import from_md, to_md
from orchestrator import load_config
from pipeline import (
    load_brief,
    load_draft,
    load_final,
    load_meta,
    rerun_from,
    run_brief_step,
    run_drafts_step,
    run_finals_step,
    run_meta_step,
    run_planner_step,
    save_edited_artifact,
)
from schemas import Brief, Draft, Final, Meta, Source

st.set_page_config(page_title="content-factory-lite panel", layout="wide")


def _get_conn():
    if "conn" not in st.session_state:
        init_db()
        st.session_state["conn"] = get_connection()
    return st.session_state["conn"]


def _get_llm(cfg: dict) -> LLM:
    if "llm" not in st.session_state:
        st.session_state["llm"] = LLM(cfg=cfg)
    return st.session_state["llm"]


def _list_scenarios(conn, run_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, idx, topic, angle, brief_hint FROM scenarios WHERE run_id = %s ORDER BY idx",
            (run_id,),
        )
        return cur.fetchall()


def _get_run_info(conn, run_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, source_type, source_ref, status, created_at FROM runs WHERE id = %s",
            (run_id,),
        )
        return cur.fetchone()


def _render_checks(conn, scenario_id: int, kind: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.rule, c.passed, c.detail
            FROM checks c
            JOIN artifacts a ON a.id = c.artifact_id
            WHERE a.scenario_id = %s AND a.kind = %s
            ORDER BY c.id
            """,
            (scenario_id, kind),
        )
        checks = cur.fetchall()
    if not checks:
        return
    badges = []
    for check in checks:
        if check["passed"]:
            badges.append(f":green[✅ {check['rule']}]")
        else:
            badges.append(f":red[❌ {check['rule']}: {check['detail']}]")
    st.markdown(" &nbsp; ".join(badges))


def _build_scenario(conn, llm, cfg: dict, scenario_id: int, label: str, status=None) -> list[str]:
    """Runs brief -> drafts -> finals -> meta for one scenario via rerun_from.

    Per-stage errors don't raise: rerun_from runs stage functions in sequence,
    so on failure we catch here, report which stage failed, and stop this
    scenario's chain (consistent with the manual per-stage buttons, which
    also stop the chain on the first LLMContractError). Returns the list of
    stage names that completed successfully.
    """
    stage_labels = {"brief": "researcher", "drafts": "writer", "finals": "editor", "meta": "seo"}
    succeeded: list[str] = []
    # Surface model/fallback events into the status box so it's clear which
    # free model actually answered at each stage.
    if status is not None:
        llm.on_event = status.write
    try:
        if status is not None:
            status.update(label=f"{label}: researcher...")
        run_brief_step(scenario_id, cfg, llm, conn)
        succeeded.append("brief")

        if status is not None:
            status.update(label=f"{label}: writer...")
        run_drafts_step(scenario_id, cfg, llm, conn)
        succeeded.append("drafts")

        if status is not None:
            status.update(label=f"{label}: editor...")
        run_finals_step(scenario_id, cfg, llm, conn)
        succeeded.append("finals")

        if status is not None:
            status.update(label=f"{label}: seo...")
        run_meta_step(scenario_id, cfg, llm, conn)
        succeeded.append("meta")
    except LLMContractError as exc:
        next_stage = next((s for s in ["brief", "drafts", "finals", "meta"] if s not in succeeded), "meta")
        st.warning(f"{label}: стадия '{stage_labels[next_stage]}' упала — {exc}")
    except Exception as exc:  # noqa: BLE001 - never abort the whole build on one scenario's error
        st.warning(f"{label}: ошибка — {exc}")
    finally:
        llm.on_event = None
    return succeeded


def _auto_build_parallel(cfg: dict, db_scenarios: list[dict]) -> None:
    """Builds all scenarios concurrently, same method as the headless runner.

    Reuses orchestrator.process_scenario (each worker opens its own DB
    connection — psycopg connections aren't thread-safe — and never touches
    st.*). Worker threads share one read-only LLM with no on_event callback so
    no Streamlit calls happen off the main thread; the UI is updated here, in
    the main thread, as futures complete.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from orchestrator import process_scenario

    total = len(db_scenarios)
    n_workers = cfg.get("run", {}).get("workers", 3)
    worker_llm = LLM(cfg=cfg)  # read-only across threads; no on_event => no st.* off-thread
    no_force = lambda _stage: False  # noqa: E731 - fresh build never force-reruns

    with st.status(
        f"Параллельная сборка {total} сценариев ({n_workers} воркеров)...", expanded=True
    ) as status:
        done = 0
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {
                executor.submit(process_scenario, db_sc, db_sc["id"], cfg, worker_llm, no_force): db_sc
                for db_sc in db_scenarios
            }
            for future in as_completed(futures):
                db_sc = futures[future]
                built, failed, _style = future.result()
                done += 1
                st.progress(done / total)
                if failed:
                    st.write(f"Сценарий #{db_sc['idx']}: ошибки — {', '.join(failed)}")
                else:
                    st.write(f"Сценарий #{db_sc['idx']}: готово (brief, drafts, finals, meta).")
        status.update(label="Сборка завершена", state="complete")


def enrich_panel(cfg: dict) -> None:
    """Sidebar form: back a finished text with facts pulled from links.

    Lets the user paste a ready article + a list of URLs, pick whether to
    weave facts into the prose or append a sources block, and runs
    enrich.enrich_article with a live progress log (fetch + model events).
    """
    st.sidebar.caption("Вставь готовый текст и ссылки — подкреплю фактами из них.")
    text = st.sidebar.text_area("Готовый текст", height=220, key="enrich_text")
    links_raw = st.sidebar.text_area("Ссылки (по одной на строку)", height=120, key="enrich_links")
    mode_label = st.sidebar.radio(
        "Что сделать",
        ["Вплести факты в текст", "Дописать блок «Источники»"],
        key="enrich_mode",
    )
    mode = "weave" if mode_label.startswith("Вплести") else "append"

    if not st.sidebar.button("Подкрепить", type="primary", key="enrich_go"):
        return

    if not text or not text.strip():
        st.sidebar.error("Вставьте готовый текст.")
        return
    links = [ln.strip() for ln in links_raw.splitlines() if ln.strip()]
    if not links:
        st.sidebar.error("Добавьте хотя бы одну ссылку.")
        return

    llm = _get_llm(cfg)
    with st.status("Подкрепляю статью...", expanded=True) as status:
        events: list[str] = []

        def on_event(msg: str) -> None:
            events.append(msg)
            status.write(msg)

        llm.on_event = on_event  # surface model/fallback events live
        try:
            result = enrich_article(text, links, mode, cfg, llm, on_event=on_event)
        except Exception as exc:  # noqa: BLE001 - never crash the page
            status.update(label="Ошибка", state="error")
            st.error(f"Не удалось подкрепить: {exc}")
            return
        finally:
            llm.on_event = None
        status.update(label="Готово", state="complete")

    st.session_state["enrich_result"] = result
    st.sidebar.success("Готово — результат справа.")


def sidebar(cfg: dict) -> None:
    st.sidebar.header("Панель управления")
    mode = st.sidebar.radio("Режим", ["Готовый текст", "Тема", "Подкрепить статью"])

    if mode == "Подкрепить статью":
        enrich_panel(cfg)
        return

    if mode == "Готовый текст":
        text = st.sidebar.text_area("Текст источника (транскрипт/статья)", height=200)
        source_type = st.sidebar.selectbox("Тип источника", ["transcript", "article"])
    else:
        text = st.sidebar.text_input("Тема")
        source_type = "topic"

    n_scenarios = st.sidebar.number_input(
        "Число сценариев", min_value=1, max_value=20, value=cfg["run"]["scenarios"]
    )

    auto_build = st.sidebar.checkbox("Авто-сборка после планнера", value=True)

    if st.sidebar.button("Старт", type="primary"):
        if not text or not text.strip():
            st.sidebar.error("Введите текст источника или тему.")
            return

        conn = _get_conn()
        llm = _get_llm(cfg)
        source = Source(type=source_type, ref="ui" if source_type != "topic" else "topic", text=text)

        try:
            with st.spinner("Планировщик строит сценарии..."):
                run_id = create_run(conn, source_type=source.type, source_ref=source.ref)
                scenarios = run_planner_step(source, cfg, llm, conn, n=int(n_scenarios), run_id=run_id)
        except LLMContractError as exc:
            st.sidebar.error(f"Планировщик не справился: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface any stage error, never crash the page
            st.sidebar.error(f"Ошибка запуска: {exc}")
            return

        st.session_state["run_id"] = run_id
        st.session_state["scenario_count"] = len(scenarios)
        st.sidebar.success(f"Запуск {run_id}: {len(scenarios)} сценариев")

        if auto_build:
            # run_planner_step only returns Scenario objects (idx, not DB id);
            # re-read the persisted rows to get the actual scenarios.id values
            # that the stage steps expect.
            db_scenarios = _list_scenarios(conn, run_id)
            total = len(db_scenarios)
            _auto_build_parallel(cfg, db_scenarios)

    if st.session_state.get("run_id"):
        st.sidebar.caption(f"Текущий запуск: {st.session_state['run_id']}")


def _render_artifact(label: str, kind: str, model_cls, value, scenario_id: int, conn, prev=None) -> None:
    """Renders one artifact as Markdown (view), with an edit expander and a
    download button. `prev` is passed through for Final (preserves style_passed)."""
    if value is None:
        st.info(f"{label}: пока нет.")
        return

    md_text = to_md(kind, value)

    st.markdown(md_text)

    col_dl, _ = st.columns([1, 5])
    with col_dl:
        st.download_button(
            "⬇️ Скачать .md",
            data=md_text,
            file_name=f"{kind}_{scenario_id}.md",
            mime="text/markdown",
            key=f"dl_{scenario_id}_{kind}",
        )

    with st.expander("✏️ Редактировать (Markdown)"):
        text_key = f"text_{scenario_id}_{kind}"
        edited_md = st.text_area(label, value=md_text, height=240, key=text_key)

        if st.button("Сохранить правку", key=f"save_{scenario_id}_{kind}"):
            try:
                parsed = from_md(kind, edited_md, prev=prev if prev is not None else value)
                model_cls.model_validate(parsed.model_dump())  # validate shape before persisting
            except Exception as exc:  # noqa: BLE001
                st.error(f"Не удалось сохранить: не получилось разобрать Markdown — {exc}")
            else:
                save_edited_artifact(conn, scenario_id=scenario_id, kind=kind, new_content_json=parsed.model_dump_json())
                st.success("Сохранено.")
                st.rerun()


def _render_meta_table(meta: Meta) -> None:
    st.table(
        {
            "Поле": ["title", "description", "slug", "keywords", "og_title", "og_description", "tags"],
            "Значение": [
                meta.title,
                meta.description,
                meta.slug,
                ", ".join(meta.keywords),
                meta.og_title,
                meta.og_description,
                ", ".join(meta.tags),
            ],
        }
    )


def render_scenario(conn, llm, cfg: dict, scenario: dict) -> None:
    scenario_id = scenario["id"]
    with st.expander(f"#{scenario['idx']} — {scenario['topic']}", expanded=False):
        st.markdown(f"**Ракурс:** {scenario['angle']}  \n**Подсказка для брифа:** {scenario['brief_hint']}")

        if st.button("🚀 Собрать сценарий (brief -> drafts -> finals -> meta)", key=f"build_all_{scenario_id}"):
            with st.status(f"Сборка сценария #{scenario['idx']}...", expanded=True) as status:
                succeeded = _build_scenario(conn, llm, cfg, scenario_id, f"Сценарий #{scenario['idx']}", status=status)
                status.update(label="Сборка завершена", state="complete")
            if len(succeeded) == 4:
                st.success("Готово: brief, drafts, finals, meta.")
            else:
                st.warning(f"Выполнено стадий: {', '.join(succeeded) if succeeded else 'нет'}.")
            st.rerun()

        # --- Бриф ---
        st.subheader("📋 Бриф")
        brief = load_brief(conn, scenario_id)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Перегенерировать бриф", key=f"regen_brief_{scenario_id}"):
                try:
                    with st.spinner("Researcher..."):
                        brief = run_brief_step(scenario_id, cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Researcher упал: {exc}")
        with col2:
            if st.button("Запустить дальше от брифа", key=f"runfrom_brief_{scenario_id}"):
                try:
                    with st.spinner("Пересчитываем drafts -> finals -> meta..."):
                        rerun_from(scenario_id, "brief", cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Стадия упала: {exc}")
        _render_artifact("Бриф (Markdown)", "brief", Brief, brief, scenario_id, conn)

        # --- Черновики ---
        st.subheader("📝 Черновики")
        draft_article = load_draft(conn, scenario_id, "draft_article")
        draft_post = load_draft(conn, scenario_id, "draft_post")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Перегенерировать черновики", key=f"regen_drafts_{scenario_id}"):
                try:
                    with st.spinner("Writer..."):
                        draft_article, draft_post = run_drafts_step(scenario_id, cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Writer упал: {exc}")
        with col2:
            if st.button("Запустить дальше от черновиков", key=f"runfrom_drafts_{scenario_id}"):
                try:
                    with st.spinner("Пересчитываем finals -> meta..."):
                        rerun_from(scenario_id, "drafts", cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Стадия упала: {exc}")
        st.markdown("##### Черновик статьи")
        _render_artifact("Черновик статьи (Markdown)", "draft_article", Draft, draft_article, scenario_id, conn)
        st.markdown("##### Черновик поста")
        _render_artifact("Черновик поста (Markdown)", "draft_post", Draft, draft_post, scenario_id, conn)

        # --- Финалы + STYLE ---
        st.subheader("✅ Финалы + STYLE")
        final_article = load_final(conn, scenario_id, "final_article")
        final_post = load_final(conn, scenario_id, "final_post")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Перегенерировать финалы", key=f"regen_finals_{scenario_id}"):
                try:
                    with st.spinner("Editor + STYLE gate..."):
                        final_article, _, final_post, _ = run_finals_step(scenario_id, cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Editor упал: {exc}")
        with col2:
            if st.button("Запустить дальше от финалов", key=f"runfrom_finals_{scenario_id}"):
                try:
                    with st.spinner("Пересчитываем meta..."):
                        rerun_from(scenario_id, "finals", cfg, llm, conn)
                    st.rerun()
                except LLMContractError as exc:
                    st.error(f"Стадия упала: {exc}")
        st.markdown("##### Финал статьи")
        if final_article is not None:
            _render_checks(conn, scenario_id, "final_article")
        _render_artifact("Финал статьи (Markdown)", "final_article", Final, final_article, scenario_id, conn, prev=final_article)
        st.markdown("##### Финал поста")
        if final_post is not None:
            _render_checks(conn, scenario_id, "final_post")
        _render_artifact("Финал поста (Markdown)", "final_post", Final, final_post, scenario_id, conn, prev=final_post)

        # --- Мета ---
        st.subheader("🔖 Мета")
        meta = load_meta(conn, scenario_id)
        if st.button("Перегенерировать мету", key=f"regen_meta_{scenario_id}"):
            try:
                with st.spinner("SEO..."):
                    meta = run_meta_step(scenario_id, cfg, llm, conn)
                st.rerun()
            except LLMContractError as exc:
                st.error(f"SEO упал: {exc}")
        if meta is not None:
            _render_meta_table(meta)
        _render_artifact("Мета (Markdown + frontmatter)", "meta", Meta, meta, scenario_id, conn)


def main() -> None:
    cfg = load_config()
    st.title("content-factory-lite — панель управления")

    sidebar(cfg)

    enrich_result = st.session_state.get("enrich_result")
    if enrich_result is not None:
        st.subheader("🔗 Подкреплённый текст")
        used = ", ".join(f"{u} ({s})" for u, s in enrich_result["links_used"]) or "нет"
        st.caption(f"Режим: {enrich_result['mode']} | ссылки: {used}")
        if "style_passed" in enrich_result:
            badge = "✅ STYLE пройден" if enrich_result["style_passed"] else "❌ STYLE провален"
            st.markdown(f":{'green' if enrich_result['style_passed'] else 'red'}[{badge}]")
        st.markdown(enrich_result["markdown"])
        st.download_button(
            "⬇️ Скачать .md",
            data=enrich_result["markdown"],
            file_name="enriched.md",
            mime="text/markdown",
            key="dl_enriched",
        )
        if st.button("Очистить результат", key="clear_enriched"):
            del st.session_state["enrich_result"]
            st.rerun()
        st.divider()

    run_id = st.session_state.get("run_id")
    if not run_id:
        st.write("Запустите конвейер через панель слева, чтобы увидеть сценарии.")
        return

    conn = _get_conn()
    llm = _get_llm(cfg)

    run_info = _get_run_info(conn, run_id)
    if run_info is not None:
        st.caption(
            f"Запуск #{run_id} | тип источника: {run_info['source_type']} | "
            f"источник: {run_info['source_ref']} | статус: {run_info['status']}"
        )

    scenarios = _list_scenarios(conn, run_id)
    if not scenarios:
        st.write("В этом запуске пока нет сценариев.")
        return

    for scenario in scenarios:
        render_scenario(conn, llm, cfg, scenario)


if __name__ == "__main__":
    main()
