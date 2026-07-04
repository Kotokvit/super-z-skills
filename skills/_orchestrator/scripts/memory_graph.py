#!/usr/bin/env python3
"""
memory_graph.py — Persistent Knowledge Graph (v2.0)

Replaces "history as messages" with "history as entities + relations + timeline".

Storage: SQLite (single file at .context/memory_graph.db)
Schema:
    entities(id, name, type, aliases_json, properties_json, confidence, origin, created_at, updated_at)
    relations(id, subject_id, predicate, object_id, confidence, origin, created_at)
    timeline(id, entity_id, event, timestamp, source)
    facts(id, subject_id, predicate, object_id, value, confidence, source, created_at)

API:
    graph = MemoryGraph()
    graph.add_entity(name="OpenAI", type="organization", origin="web-search")
    graph.add_entity(name="GPT-4", type="model", origin="web-search")
    graph.add_relation(subject="OpenAI", predicate="released", object="GPT-4")
    graph.query_entities(name="OpenAI")
    graph.query_relations(subject="OpenAI")
    graph.context_for(topic="OpenAI")  # returns summary for LLM
    graph.export_cyto()                # for visualization

This is the substrate that lets the orchestrator remember things across
sessions. Every skill that extracts entities writes them here; the LLM
reads from here before composing an answer.
"""
from __future__ import annotations

import json
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Iterable


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    type          TEXT DEFAULT 'unknown',
    aliases_json  TEXT DEFAULT '[]',
    properties_json TEXT DEFAULT '{}',
    confidence    REAL DEFAULT 1.0,
    origin        TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(name, type)
);

CREATE TABLE IF NOT EXISTS relations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL,
    predicate     TEXT NOT NULL,
    object_id     INTEGER NOT NULL,
    confidence    REAL DEFAULT 1.0,
    origin        TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (subject_id) REFERENCES entities(id),
    FOREIGN KEY (object_id)  REFERENCES entities(id),
    UNIQUE(subject_id, predicate, object_id)
);

CREATE TABLE IF NOT EXISTS timeline (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER,
    event         TEXT NOT NULL,
    timestamp     TEXT,
    source        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER,
    predicate     TEXT,
    object_id     INTEGER,
    value         TEXT,
    confidence    REAL DEFAULT 1.0,
    source        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (subject_id) REFERENCES entities(id),
    FOREIGN KEY (object_id)  REFERENCES entities(id)
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_timeline_entity ON timeline(entity_id);
"""


class MemoryGraph:
    """SQLite-backed entity/relation store."""

    def __init__(self, db_path: Path | str = ".context/memory_graph.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Internal ───────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA_SQL)

    @staticmethod
    def _hash_id(s: str) -> int:
        return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:12], 16)

    # ── Write API ──────────────────────────────────────────────────────

    def add_entity(
        self,
        name: str,
        type: str = "unknown",
        aliases: list[str] | None = None,
        properties: dict | None = None,
        confidence: float = 1.0,
        origin: str = "",
    ) -> int:
        """Insert or update entity. Returns entity id."""
        with self._conn() as c:
            cur = c.execute(
                "SELECT id FROM entities WHERE name = ? AND type = ?",
                (name, type),
            )
            row = cur.fetchone()
            if row:
                # Merge: update aliases/properties, bump confidence if higher
                existing = c.execute("SELECT aliases_json, properties_json, confidence FROM entities WHERE id = ?", (row["id"],)).fetchone()
                old_aliases = json.loads(existing["aliases_json"])
                old_props = json.loads(existing["properties_json"])
                new_aliases = list(set(old_aliases + (aliases or [])))
                new_props = {**old_props, **(properties or {})}
                new_conf = max(existing["confidence"], confidence)
                c.execute(
                    "UPDATE entities SET aliases_json = ?, properties_json = ?, confidence = ?, origin = ?, updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(new_aliases, ensure_ascii=False), json.dumps(new_props, ensure_ascii=False), new_conf, origin, row["id"]),
                )
                return row["id"]
            cur = c.execute(
                "INSERT INTO entities (name, type, aliases_json, properties_json, confidence, origin) VALUES (?, ?, ?, ?, ?, ?)",
                (name, type, json.dumps(aliases or [], ensure_ascii=False), json.dumps(properties or {}, ensure_ascii=False), confidence, origin),
            )
            return cur.lastrowid

    def add_relation(
        self,
        subject: str,
        predicate: str,
        object: str,
        subject_type: str = "unknown",
        object_type: str = "unknown",
        confidence: float = 1.0,
        origin: str = "",
    ) -> int:
        """Add a directed edge. Creates entities if missing. Returns relation id."""
        s_id = self.add_entity(subject, subject_type, origin=origin)
        o_id = self.add_entity(object, object_type, origin=origin)
        with self._conn() as c:
            cur = c.execute(
                "SELECT id FROM relations WHERE subject_id = ? AND predicate = ? AND object_id = ?",
                (s_id, predicate, o_id),
            )
            row = cur.fetchone()
            if row:
                # bump confidence if higher
                c.execute(
                    "UPDATE relations SET confidence = MAX(confidence, ?), origin = ? WHERE id = ?",
                    (confidence, origin, row["id"]),
                )
                return row["id"]
            cur = c.execute(
                "INSERT INTO relations (subject_id, predicate, object_id, confidence, origin) VALUES (?, ?, ?, ?, ?)",
                (s_id, predicate, o_id, confidence, origin),
            )
            return cur.lastrowid

    def add_timeline_event(
        self,
        entity_name: str,
        event: str,
        timestamp: Optional[str] = None,
        source: str = "",
        entity_type: str = "unknown",
    ):
        entity_id = self.add_entity(entity_name, entity_type, origin=source)
        with self._conn() as c:
            c.execute(
                "INSERT INTO timeline (entity_id, event, timestamp, source) VALUES (?, ?, ?, ?)",
                (entity_id, event, timestamp or time.strftime("%Y-%m-%dT%H:%M:%S%z"), source),
            )

    def add_fact(
        self,
        subject: str,
        predicate: str,
        object: str = "",
        value: str = "",
        confidence: float = 1.0,
        source: str = "",
    ) -> int:
        """A fact is a (subject, predicate, object|value) triple with provenance."""
        s_id = self.add_entity(subject, origin=source)
        o_id = self.add_entity(object, origin=source) if object else None
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO facts (subject_id, predicate, object_id, value, confidence, source) VALUES (?, ?, ?, ?, ?, ?)",
                (s_id, predicate, o_id, value, confidence, source),
            )
            return cur.lastrowid

    # ── Bulk import from SkillOutput ───────────────────────────────────

    def ingest_skill_output(self, output: dict, skill_name: str = ""):
        """Ingest entities + relations from a SkillOutput dict."""
        origin = skill_name or output.get("skill_name", "unknown")
        for ent in output.get("entities", []):
            self.add_entity(
                name=ent.get("name", ""),
                type=ent.get("type", "unknown"),
                aliases=ent.get("aliases", []),
                properties=ent.get("properties", {}),
                confidence=ent.get("confidence", 1.0),
                origin=origin,
            )
        for rel in output.get("relations", []):
            self.add_relation(
                subject=rel.get("subject", ""),
                predicate=rel.get("predicate", ""),
                object=rel.get("object", ""),
                confidence=rel.get("confidence", 1.0),
                origin=origin,
            )

    # ── Read API ───────────────────────────────────────────────────────

    def query_entities(self, name: str = "", type: str = "", limit: int = 50) -> list[dict]:
        with self._conn() as c:
            sql = "SELECT * FROM entities WHERE 1=1"
            args = []
            if name:
                sql += " AND name LIKE ?"
                args.append(f"%{name}%")
            if type:
                sql += " AND type = ?"
                args.append(type)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def query_relations(self, subject: str = "", predicate: str = "", object: str = "", limit: int = 50) -> list[dict]:
        with self._conn() as c:
            sql = """
                SELECT r.*, s.name AS subject_name, o.name AS object_name
                FROM relations r
                JOIN entities s ON r.subject_id = s.id
                JOIN entities o ON r.object_id = o.id
                WHERE 1=1
            """
            args = []
            if subject:
                sql += " AND s.name LIKE ?"
                args.append(f"%{subject}%")
            if predicate:
                sql += " AND r.predicate LIKE ?"
                args.append(f"%{predicate}%")
            if object:
                sql += " AND o.name LIKE ?"
                args.append(f"%{object}%")
            sql += " ORDER BY r.confidence DESC, r.created_at DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def context_for(self, topic: str, max_entities: int = 10, max_relations: int = 20) -> dict:
        """
        Build a context brief for the LLM about a given topic.
        Returns: {entities, relations, timeline, summary}
        """
        entities = self.query_entities(name=topic, limit=max_entities)
        relations = self.query_relations(subject=topic, limit=max_relations)
        # If no direct hits, also search by relation object
        if not entities:
            relations += self.query_relations(object=topic, limit=max_relations)

        summary_parts = []
        for e in entities[:5]:
            summary_parts.append(f"{e['name']} ({e['type']})")
        for r in relations[:5]:
            summary_parts.append(f"{r['subject_name']} —{r['predicate']}→ {r['object_name']}")

        return {
            "topic": topic,
            "entities": entities,
            "relations": relations,
            "summary": "; ".join(summary_parts) if summary_parts else f"no prior knowledge about '{topic}'",
        }

    def stats(self) -> dict:
        with self._conn() as c:
            return {
                "entities": c.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": c.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "timeline_events": c.execute("SELECT COUNT(*) FROM timeline").fetchone()[0],
                "facts": c.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            }

    # ── Export ─────────────────────────────────────────────────────────

    def export_json(self) -> str:
        with self._conn() as c:
            entities = [dict(r) for r in c.execute("SELECT * FROM entities ORDER BY name").fetchall()]
            relations = [dict(r) for r in c.execute("""
                SELECT r.id, s.name AS subject, r.predicate, o.name AS object, r.confidence, r.origin, r.created_at
                FROM relations r
                JOIN entities s ON r.subject_id = s.id
                JOIN entities o ON r.object_id = o.id
                ORDER BY r.created_at DESC
            """).fetchall()]
        return json.dumps({"entities": entities, "relations": relations}, ensure_ascii=False, indent=2)

    def export_cyto(self) -> str:
        """Cytoscape.js format for visualization."""
        with self._conn() as c:
            entities = c.execute("SELECT id, name, type FROM entities").fetchall()
            relations = c.execute("""
                SELECT r.id, s.name AS source, o.name AS target, r.predicate
                FROM relations r
                JOIN entities s ON r.subject_id = s.id
                JOIN entities o ON r.object_id = o.id
            """).fetchall()
        nodes = [{"data": {"id": str(e["id"]), "label": e["name"], "type": e["type"]}} for e in entities]
        edges = [{"data": {"id": f"e{r['id']}", "source": str(r["source"]), "target": str(r["target"]), "label": r["predicate"]}} for r in relations]
        return json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    graph = MemoryGraph()

    if len(sys.argv) < 2:
        print("Usage: memory_graph.py [stats|add|query|export]")
        print(graph.stats())
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "stats":
        print(json.dumps(graph.stats(), indent=2))
    elif cmd == "add":
        # add <subject> <predicate> <object>
        s, p, o = sys.argv[2:5]
        rid = graph.add_relation(s, p, o, origin="cli")
        print(f"Added relation #{rid}: {s} —{p}→ {o}")
    elif cmd == "query":
        topic = sys.argv[2]
        print(json.dumps(graph.context_for(topic), ensure_ascii=False, indent=2, default=str))
    elif cmd == "export":
        print(graph.export_json())
    elif cmd == "demo":
        # Seed with demo data
        graph.add_relation("OpenAI", "released", "GPT-4", "organization", "model", origin="demo")
        graph.add_relation("OpenAI", "headquartered_in", "San Francisco", "organization", "location", origin="demo")
        graph.add_relation("GPT-4", "released_in", "2023", "model", "date", origin="demo")
        graph.add_timeline_event("GPT-4", "Public launch", "2023-03-14", source="demo")
        print("Demo data added. Stats:")
        print(json.dumps(graph.stats(), indent=2))
        print("\nContext for 'OpenAI':")
        print(json.dumps(graph.context_for("OpenAI"), ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
