"""Postgres storage layer (raw psycopg3, no ORM).

Schema (Appendix C):
  runs(id PK, source_type, source_ref, status, created_at)
  scenarios(id PK, run_id FK, idx, topic, angle, brief_hint, status)
  artifacts(id PK, scenario_id FK, kind, path, content, version, created_at)
      kind in {brief, draft_article, draft_post, final_article, final_post, meta}
  checks(id PK, artifact_id FK, rule, passed, detail)

Connect via DATABASE_URL env var.
"""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scenarios (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    topic TEXT NOT NULL,
    angle TEXT NOT NULL,
    brief_hint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (run_id, idx)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id SERIAL PRIMARY KEY,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id),
    kind TEXT NOT NULL,
    path TEXT,
    content TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scenario_id, kind, version)
);

CREATE TABLE IF NOT EXISTS checks (
    id SERIAL PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
    rule TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    detail TEXT
);
"""


def get_connection() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=True)


def init_db(conn: psycopg.Connection | None = None) -> None:
    """Creates schema if it does not exist yet."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    finally:
        if own_conn:
            conn.close()


def create_run(conn: psycopg.Connection, source_type: str, source_ref: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (source_type, source_ref) VALUES (%s, %s) RETURNING id",
            (source_type, source_ref),
        )
        row = cur.fetchone()
        return row["id"]


def upsert_scenario(
    conn: psycopg.Connection,
    run_id: int,
    idx: int,
    topic: str,
    angle: str,
    brief_hint: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scenarios (run_id, idx, topic, angle, brief_hint)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (run_id, idx) DO UPDATE SET
                topic = EXCLUDED.topic,
                angle = EXCLUDED.angle,
                brief_hint = EXCLUDED.brief_hint
            RETURNING id
            """,
            (run_id, idx, topic, angle, brief_hint),
        )
        row = cur.fetchone()
        return row["id"]


def save_artifact(
    conn: psycopg.Connection,
    scenario_id: int,
    kind: str,
    content: str,
    path: str | None = None,
    version: int = 1,
) -> int:
    """Idempotent on (scenario_id, kind, version) via ON CONFLICT DO UPDATE."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO artifacts (scenario_id, kind, path, content, version)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (scenario_id, kind, version) DO UPDATE SET
                path = EXCLUDED.path,
                content = EXCLUDED.content
            RETURNING id
            """,
            (scenario_id, kind, path, content, version),
        )
        row = cur.fetchone()
        return row["id"]


def save_check(
    conn: psycopg.Connection,
    artifact_id: int,
    rule: str,
    passed: bool,
    detail: str = "",
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO checks (artifact_id, rule, passed, detail) VALUES (%s, %s, %s, %s) RETURNING id",
            (artifact_id, rule, passed, detail),
        )
        row = cur.fetchone()
        return row["id"]


def get_artifact(
    conn: psycopg.Connection,
    scenario_id: int | None,
    kind: str,
    version: int = 1,
) -> dict | None:
    if scenario_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM artifacts WHERE scenario_id = %s AND kind = %s AND version = %s",
            (scenario_id, kind, version),
        )
        return cur.fetchone()
