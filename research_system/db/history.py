"""SQLite-backed research history."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import config
from ..core.schemas import ResearchPlan, ResearchReport, SubagentFinding


def _connect() -> sqlite3.Connection:
    Path(config.HISTORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            query TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            report_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_research(
    query: str,
    plan: ResearchPlan,
    findings: list[SubagentFinding],
    report: ResearchReport,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO research (created_at, query, plan_json, findings_json, report_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                query,
                plan.model_dump_json(),
                json.dumps([f.model_dump() for f in findings]),
                report.model_dump_json(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_history(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, query FROM research ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_research(rid: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM research WHERE id = ?", (rid,)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "query": row["query"],
        "plan": ResearchPlan.model_validate_json(row["plan_json"]),
        "findings": [
            SubagentFinding.model_validate(f)
            for f in json.loads(row["findings_json"])
        ],
        "report": ResearchReport.model_validate_json(row["report_json"]),
    }


def delete_research(rid: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM research WHERE id = ?", (rid,))
        conn.commit()
