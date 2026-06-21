"""Streamlit human-in-the-loop control panel for content-factory-lite.

Sidebar: start a run (ready source or bare topic) via run_planner_step.
Main: per-scenario expanders with the full stage ladder (brief -> drafts ->
finals + STYLE -> meta), each artifact shown as an editable text area with
per-stage "Перегенерировать" / "Сохранить правку" / "Запустить дальше"
controls.

Reads/writes only through db.py + pipeline.py - no stage logic lives here.
"""
from __future__ import annotations

import json

import streamlit as st

from db import create_run, get_connection, init_db
from llm import LLM, LLMContractError
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
    for check in checks:
        if check["passed"]:
            st.success(f"{check['rule']}: PASS", icon="✅")
        else:
            st.error(f"{check['rule']}: FAIL — {check['detail']}", icon="❌")


def sidebar(cfg: dict) -> None:
    st.sidebar.header("Панель управления")
    mode = st.sidebar.radio("Режим", ["Готовый текст", "Тема"])

    if mode == "Готовый текст":
        text = st.sidebar.text_area("Текст источника (транскрипт/статья)", height=200)
        source_type = st.sidebar.selectbox("Тип источника", ["transcript", "article"])
    else:
        text = st.sidebar.text_input("Тема")
        source_type = "topic"

    n_scenarios = st.sidebar.number_input(
        "Число сценариев", min_value=1, max_value=20, value=cfg["run"]["scenarios"]
    )

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

    if st.session_state.get("run_id"):
        st.sidebar.caption(f"Текущий запуск: {st.session_state['run_id']}")


def _editable_artifact(label: str, model_cls, value, scenario_id: int, kind: str, conn) -> None:
    """Renders one artifact as an editable JSON text area with a save button."""
    raw_json = value.model_dump_json(indent=2) if value is not None else ""
    text_key = f"text_{scenario_id}_{kind}"
    edited = st.text_area(label, value=raw_json, height=180, key=text_key)

    if st.button("Сохранить правку", key=f"save_{scenario_id}_{kind}"):
        try:
            parsed = json.loads(edited)
            model_cls.model_validate(parsed)  # validate shape before persisting
        except Exception as exc:  # noqa: BLE001
            st.error(f"Не удалось сохранить: невалидный JSON/схема — {exc}")
        else:
            save_edited_artifact(conn, scenario_id=scenario_id, kind=kind, new_content_json=edited)
            st.success("Сохранено.")
            st.rerun()


def render_scenario(conn, llm, cfg: dict, scenario: dict) -> None:
    scenario_id = scenario["id"]
    with st.expander(f"#{scenario['idx']} — {scenario['topic']}", expanded=False):
        st.caption(f"Ракурс: {scenario['angle']} | Подсказка: {scenario['brief_hint']}")

        # --- Бриф ---
        st.subheader("Бриф")
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
        if brief is not None:
            _editable_artifact("Бриф (JSON)", Brief, brief, scenario_id, "brief", conn)
        else:
            st.info("Брифа пока нет.")

        # --- Черновики ---
        st.subheader("Черновики")
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
        if draft_article is not None:
            _editable_artifact("Черновик статьи (JSON)", Draft, draft_article, scenario_id, "draft_article", conn)
        else:
            st.info("Черновика статьи пока нет.")
        if draft_post is not None:
            _editable_artifact("Черновик поста (JSON)", Draft, draft_post, scenario_id, "draft_post", conn)
        else:
            st.info("Черновика поста пока нет.")

        # --- Финалы + STYLE ---
        st.subheader("Финалы + STYLE")
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
        if final_article is not None:
            _editable_artifact("Финал статьи (JSON)", Final, final_article, scenario_id, "final_article", conn)
            st.caption("STYLE — статья")
            _render_checks(conn, scenario_id, "final_article")
        else:
            st.info("Финала статьи пока нет.")
        if final_post is not None:
            _editable_artifact("Финал поста (JSON)", Final, final_post, scenario_id, "final_post", conn)
            st.caption("STYLE — пост")
            _render_checks(conn, scenario_id, "final_post")
        else:
            st.info("Финала поста пока нет.")

        # --- Мета ---
        st.subheader("Мета")
        meta = load_meta(conn, scenario_id)
        if st.button("Перегенерировать мету", key=f"regen_meta_{scenario_id}"):
            try:
                with st.spinner("SEO..."):
                    meta = run_meta_step(scenario_id, cfg, llm, conn)
                st.rerun()
            except LLMContractError as exc:
                st.error(f"SEO упал: {exc}")
        if meta is not None:
            _editable_artifact("Мета (JSON)", Meta, meta, scenario_id, "meta", conn)
        else:
            st.info("Меты пока нет.")


def main() -> None:
    cfg = load_config()
    st.title("content-factory-lite — панель управления")

    sidebar(cfg)

    run_id = st.session_state.get("run_id")
    if not run_id:
        st.write("Запустите конвейер через панель слева, чтобы увидеть сценарии.")
        return

    conn = _get_conn()
    llm = _get_llm(cfg)
    scenarios = _list_scenarios(conn, run_id)
    if not scenarios:
        st.write("В этом запуске пока нет сценариев.")
        return

    for scenario in scenarios:
        render_scenario(conn, llm, cfg, scenario)


if __name__ == "__main__":
    main()
