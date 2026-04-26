"""
Token ledger — SQLite-backed spend tracker for multi-model engagements.
Records every agent's token usage and calculates real-time cost.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

MODEL_PRICING = {
    "claude-haiku-4-5":   {"input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_write": 1.00},
    "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_write": 3.75},
    "claude-opus-4-7":    {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id   TEXT    NOT NULL,
    agent_role      TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    phase           INTEGER DEFAULT 0,
    task_type       TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cache_read      INTEGER DEFAULT 0,
    cache_write     INTEGER DEFAULT 0,
    cost_usd        REAL    DEFAULT 0.0,
    notes           TEXT,
    recorded_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id   TEXT    NOT NULL,
    task_type       TEXT    NOT NULL,
    model_assigned  TEXT    NOT NULL,
    priority_score  REAL    DEFAULT 0.0,
    max_turns       INTEGER DEFAULT 0,
    estimated_cost  REAL    DEFAULT 0.0,
    status          TEXT    DEFAULT 'pending',
    created_at      TEXT    NOT NULL,
    completed_at    TEXT
);
"""


class TokenLedger:
    def __init__(self, engagement_id: str, base_dir: str = "engagements"):
        self.eid = engagement_id
        self.db_path = Path(base_dir) / engagement_id / "ledger.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def record_usage(
        self,
        agent_role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        phase: int = 0,
        task_type: str = "",
        cache_read: int = 0,
        cache_write: int = 0,
        notes: str = "",
    ) -> float:
        cost = self._calculate_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO token_usage
                   (engagement_id, agent_role, model, phase, task_type,
                    input_tokens, output_tokens, cache_read, cache_write, cost_usd, notes, recorded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.eid, agent_role, model, phase, task_type,
                 input_tokens, output_tokens, cache_read, cache_write, cost, notes,
                 datetime.utcnow().isoformat()),
            )
        return cost

    def log_task(
        self,
        task_type: str,
        model_assigned: str,
        priority_score: float,
        max_turns: int,
        estimated_cost: float,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO task_log
                   (engagement_id, task_type, model_assigned, priority_score,
                    max_turns, estimated_cost, status, created_at)
                   VALUES (?,?,?,?,?,?,'running',?)""",
                (self.eid, task_type, model_assigned, priority_score,
                 max_turns, estimated_cost, datetime.utcnow().isoformat()),
            )
            return cur.lastrowid

    def complete_task(self, task_id: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_log SET status='complete', completed_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), task_id),
            )

    def get_total_cost(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM token_usage WHERE engagement_id=?",
                (self.eid,),
            ).fetchone()
            return row[0]

    def get_cost_by_phase(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT phase, COALESCE(SUM(cost_usd),0) FROM token_usage WHERE engagement_id=? GROUP BY phase",
                (self.eid,),
            ).fetchall()
            return {r[0]: round(r[1], 4) for r in rows}

    def get_cost_by_model(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, COALESCE(SUM(cost_usd),0) FROM token_usage WHERE engagement_id=? GROUP BY model",
                (self.eid,),
            ).fetchall()
            return {r[0]: round(r[1], 4) for r in rows}

    def get_token_totals(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                   COALESCE(SUM(input_tokens),0),
                   COALESCE(SUM(output_tokens),0),
                   COALESCE(SUM(cache_read),0),
                   COALESCE(SUM(cache_write),0)
                   FROM token_usage WHERE engagement_id=?""",
                (self.eid,),
            ).fetchone()
            return {
                "input": row[0],
                "output": row[1],
                "cache_read": row[2],
                "cache_write": row[3],
            }

    def get_status(self, total_budget: float) -> dict:
        spent = self.get_total_cost()
        return {
            "total_budget_usd": total_budget,
            "spent_usd": round(spent, 4),
            "remaining_usd": round(total_budget - spent, 4),
            "pct_used": round((spent / total_budget) * 100, 1) if total_budget else 0,
            "by_phase": self.get_cost_by_phase(),
            "by_model": self.get_cost_by_model(),
            "tokens": self.get_token_totals(),
        }

    @staticmethod
    def _calculate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
    ) -> float:
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-sonnet-4-6"])
        cost = (
            input_tokens  * pricing["input"]        / 1_000_000
            + output_tokens * pricing["output"]       / 1_000_000
            + cache_read    * pricing["cache_read"]   / 1_000_000
            + cache_write   * pricing["cache_write"]  / 1_000_000
        )
        return round(cost, 6)
