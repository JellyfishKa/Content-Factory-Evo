"""Read-only web viewer for content-factory-lite artifacts (T6.2).

FastAPI app: runs -> scenarios -> artifacts, with STYLE check status shown
in color (pass green / fail red + detail). Reads the DB only via plain
SELECTs / db.get_connection - never touches the pipeline itself.
"""
from __future__ import annotations

import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from db import get_connection

app = FastAPI(title="content-factory-lite viewer")


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }}
a {{ text-decoration: none; color: #0645ad; }}
.pass {{ color: #1a7f37; font-weight: bold; }}
.fail {{ color: #c00; font-weight: bold; }}
pre {{ white-space: pre-wrap; background: #f6f6f6; padding: 1rem; }}
</style>
</head>
<body>
<p><a href="/">runs</a></p>
{body}
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def list_runs() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, source_type, source_ref, status, created_at FROM runs ORDER BY id DESC")
            runs = cur.fetchall()
    finally:
        conn.close()

    rows = "".join(
        f"<tr><td><a href='/runs/{r['id']}'>{r['id']}</a></td>"
        f"<td>{html.escape(r['source_type'])}</td>"
        f"<td>{html.escape(r['source_ref'])}</td>"
        f"<td>{html.escape(r['status'])}</td>"
        f"<td>{r['created_at']}</td></tr>"
        for r in runs
    )
    body = (
        "<h1>Runs</h1>"
        "<table><tr><th>id</th><th>source_type</th><th>source_ref</th><th>status</th><th>created_at</th></tr>"
        f"{rows}</table>"
    )
    return _page("Runs", body)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def list_scenarios(run_id: int) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, idx, topic, angle, brief_hint, status FROM scenarios WHERE run_id = %s ORDER BY idx",
                (run_id,),
            )
            scenarios = cur.fetchall()
    finally:
        conn.close()

    rows = "".join(
        f"<tr><td><a href='/scenarios/{s['id']}'>{s['idx']}</a></td>"
        f"<td>{html.escape(s['topic'])}</td>"
        f"<td>{html.escape(s['angle'])}</td>"
        f"<td>{html.escape(s['brief_hint'])}</td>"
        f"<td>{html.escape(s['status'])}</td></tr>"
        for s in scenarios
    )
    body = (
        f"<h1>Run {run_id} — scenarios</h1>"
        "<table><tr><th>idx</th><th>topic</th><th>angle</th><th>brief_hint</th><th>status</th></tr>"
        f"{rows}</table>"
    )
    return _page(f"Run {run_id}", body)


@app.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
def list_artifacts(scenario_id: int) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, kind, version, created_at FROM artifacts WHERE scenario_id = %s ORDER BY kind, version",
                (scenario_id,),
            )
            artifacts = cur.fetchall()
    finally:
        conn.close()

    rows = "".join(
        f"<tr><td><a href='/artifacts/{a['id']}'>{html.escape(a['kind'])}</a></td>"
        f"<td>{a['version']}</td><td>{a['created_at']}</td></tr>"
        for a in artifacts
    )
    body = (
        f"<h1>Scenario {scenario_id} — artifacts</h1>"
        "<table><tr><th>kind</th><th>version</th><th>created_at</th></tr>"
        f"{rows}</table>"
    )
    return _page(f"Scenario {scenario_id}", body)


@app.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def show_artifact(artifact_id: int) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, scenario_id, kind, content, version, created_at FROM artifacts WHERE id = %s",
                (artifact_id,),
            )
            artifact = cur.fetchone()
            cur.execute(
                "SELECT rule, passed, detail FROM checks WHERE artifact_id = %s ORDER BY id",
                (artifact_id,),
            )
            checks = cur.fetchall()
    finally:
        conn.close()

    if artifact is None:
        return _page("Not found", "<p>Artifact not found.</p>")

    checks_rows = "".join(
        f"<tr><td>{html.escape(c['rule'])}</td>"
        f"<td class='{'pass' if c['passed'] else 'fail'}'>{'PASS' if c['passed'] else 'FAIL'}</td>"
        f"<td>{html.escape(c['detail'] or '')}</td></tr>"
        for c in checks
    )
    checks_block = (
        "<h2>STYLE checks</h2>"
        "<table><tr><th>rule</th><th>status</th><th>detail</th></tr>"
        f"{checks_rows}</table>"
        if checks
        else "<p>No checks recorded for this artifact.</p>"
    )

    body = (
        f"<h1>Artifact {artifact_id} — {html.escape(artifact['kind'])}</h1>"
        f"<p>scenario: <a href='/scenarios/{artifact['scenario_id']}'>{artifact['scenario_id']}</a> | "
        f"version: {artifact['version']} | created_at: {artifact['created_at']}</p>"
        f"{checks_block}"
        f"<h2>Content</h2><pre>{html.escape(artifact['content'] or '')}</pre>"
    )
    return _page(f"Artifact {artifact_id}", body)
