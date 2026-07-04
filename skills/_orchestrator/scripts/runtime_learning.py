#!/usr/bin/env python3
"""
runtime_learning.py — Runtime Skill Performance Tracker (v2.0)

Logs every skill invocation and produces weights that the planner uses to
pick the best provider for a capability. Implements point #10 from the
architecture review: "Runtime Learning → через месяц планировщик станет
намного лучше".

Schema (SQLite at .context/runtime_learning.db):
    invocations(id, timestamp, skill_name, capability, query_hash,
                status, confidence, duration_ms, success_score, error)
    weights(skill_name, capability, weight, samples, last_updated)

Success score is derived:
    +1.0 if status=ok and confidence >= 0.8
    +0.5 if status=partial
     0.0 if status=skipped
    -1.0 if status=error

Weight = exponential moving average of success_score (default alpha=0.2).
Higher weight = more likely to be picked by planner next time.

API:
    tracker = RuntimeLearning()
    tracker.log(skill="pdf-ocr", capability="extract_text",
                status="ok", confidence=0.9, duration_ms=1200)
    tracker.weight("pdf-ocr", "extract_text")  # → 0.85
    tracker.top_providers("extract_text")      # → [{skill, weight, samples}]
"""
from __future__ import annotations

import json
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS invocations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT DEFAULT (datetime('now')),
    skill_name     TEXT NOT NULL,
    capability     TEXT,
    query_hash     TEXT,
    status         TEXT,
    confidence     REAL,
    duration_ms    INTEGER,
    success_score  REAL,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS weights (
    skill_name     TEXT NOT NULL,
    capability     TEXT NOT NULL,
    weight         REAL DEFAULT 0.5,
    samples        INTEGER DEFAULT 0,
    last_updated   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (skill_name, capability)
);

CREATE INDEX IF NOT EXISTS idx_inv_skill ON invocations(skill_name);
CREATE INDEX IF NOT EXISTS idx_inv_cap ON invocations(capability);
CREATE INDEX IF NOT EXISTS idx_inv_ts ON invocations(timestamp);
"""


class RuntimeLearning:
    """Tracks skill performance over time and produces planner weights."""

    ALPHA = 0.2  # EMA smoothing factor

    def __init__(self, db_path: Path | str = ".context/runtime_learning.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA_SQL)

    @staticmethod
    def _score(status: str, confidence: float) -> float:
        if status == "ok" and confidence >= 0.8:
            return 1.0
        if status == "ok":
            return 0.7
        if status == "partial":
            return 0.5
        if status == "skipped":
            return 0.0
        return -1.0  # error

    @staticmethod
    def _hash_query(query: str) -> str:
        return hashlib.md5(query.encode("utf-8")).hexdigest()[:10]

    # ── Write ──────────────────────────────────────────────────────────

    def log(
        self,
        skill: str,
        capability: str = "",
        query: str = "",
        status: str = "ok",
        confidence: float = 0.0,
        duration_ms: int = 0,
        error: str = "",
    ) -> int:
        """Log an invocation and update the EMA weight."""
        score = self._score(status, confidence)
        qhash = self._hash_query(query) if query else ""

        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO invocations
                   (skill_name, capability, query_hash, status, confidence, duration_ms, success_score, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill, capability, qhash, status, confidence, duration_ms, score, error),
            )

            # Update EMA weight
            row = c.execute(
                "SELECT weight, samples FROM weights WHERE skill_name = ? AND capability = ?",
                (skill, capability),
            ).fetchone()
            if row:
                new_weight = self.ALPHA * score + (1 - self.ALPHA) * row["weight"]
                c.execute(
                    "UPDATE weights SET weight = ?, samples = samples + 1, last_updated = datetime('now') WHERE skill_name = ? AND capability = ?",
                    (new_weight, skill, capability),
                )
            else:
                new_weight = 0.5 + self.ALPHA * (score - 0.5)  # start from 0.5 prior
                c.execute(
                    "INSERT INTO weights (skill_name, capability, weight, samples) VALUES (?, ?, ?, 1)",
                    (skill, capability, new_weight),
                )
            return cur.lastrowid

    # ── Read ───────────────────────────────────────────────────────────

    def weight(self, skill: str, capability: str) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT weight FROM weights WHERE skill_name = ? AND capability = ?",
                (skill, capability),
            ).fetchone()
            return row["weight"] if row else 0.5

    def top_providers(self, capability: str, limit: int = 5) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT skill_name, weight, samples, last_updated
                   FROM weights WHERE capability = ?
                   ORDER BY weight DESC, samples DESC LIMIT ?""",
                (capability, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
            by_status = dict(c.execute(
                "SELECT status, COUNT(*) FROM invocations GROUP BY status"
            ).fetchall())
            by_skill = dict(c.execute(
                "SELECT skill_name, COUNT(*) FROM invocations GROUP BY skill_name ORDER BY COUNT(*) DESC LIMIT 10"
            ).fetchall())
            return {
                "total_invocations": total,
                "by_status": by_status,
                "top_skills": by_skill,
            }

    def recent_errors(self, limit: int = 10) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT timestamp, skill_name, capability, error
                   FROM invocations WHERE status = 'error' AND error != ''
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Export for planner ─────────────────────────────────────────────

    def weights_table(self) -> dict:
        """Return all weights for the planner to consult."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT skill_name, capability, weight, samples FROM weights"
            ).fetchall()
        return {
            f"{r['skill_name']}::{r['capability']}": {
                "weight": r["weight"],
                "samples": r["samples"],
            }
            for r in rows
        }


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    tracker = RuntimeLearning()

    if len(sys.argv) < 2:
        # Demo
        tracker.log("pdf-ocr", "extract_text", "doc1", "ok", 0.92, 1200)
        tracker.log("pdf-ocr", "extract_text", "doc2", "ok", 0.88, 1100)
        tracker.log("pdf-ocr", "extract_text", "doc3", "ok", 0.85, 1350)
        tracker.log("pdf-ocr", "extract_text", "doc4", "error", 0.0, 500, "parse fail")
        tracker.log("vlm", "extract_text", "img1", "ok", 0.78, 800)
        tracker.log("vlm", "extract_text", "img2", "ok", 0.81, 850)

        print("Stats:", json.dumps(tracker.stats(), indent=2))
        print("\nTop providers for 'extract_text':")
        print(json.dumps(tracker.top_providers("extract_text"), indent=2))
        print("\nRecent errors:")
        print(json.dumps(tracker.recent_errors(), indent=2, default=str))
    elif sys.argv[1] == "stats":
        print(json.dumps(tracker.stats(), indent=2))
    elif sys.argv[1] == "weights":
        print(json.dumps(tracker.weights_table(), indent=2))
    elif sys.argv[1] == "errors":
        print(json.dumps(tracker.recent_errors(), indent=2, default=str))
    else:
        print(f"Unknown: {sys.argv[1]}")
