#!/usr/bin/env python3
"""
migrate_v2_0_to_v2_1.py — One-shot migration: Super-Z memory_graph.db v2.0 → v2.1

What changes:
    - entities table: gains `layer TEXT DEFAULT 'fact'` and `world TEXT DEFAULT ''`
    - relations table: gains `layer TEXT DEFAULT 'fact'` and `world TEXT DEFAULT ''`
    - facts table: gains `layer TEXT DEFAULT 'fact'` and `world TEXT DEFAULT ''`
    - UNIQUE constraints updated:
        entities:   UNIQUE(name, type) → UNIQUE(name, type, layer, world)
        relations:  UNIQUE(subject_id, predicate, object_id)
                  → UNIQUE(subject_id, predicate, object_id, layer, world)
    - New table: worlds(id, name UNIQUE, description, created_at)

Migration strategy:
    1. Backup current DB to memory_graph.db.v2.0.bak
    2. Verify target DB doesn't already have v2.1 columns (idempotent)
    3. Create new tables with v2.1 schema under names entities_new, relations_new, facts_new
    4. Copy all existing rows with layer='fact', world=''
    5. Verify row counts match (entities vs entities_new, etc.)
    6. Atomic swap: drop old, rename new
    7. Create `worlds` table
    8. Create `schema_version` table, set version=2
    9. Print summary

Idempotent: if DB already at v2.1, exits with success message.

Usage:
    python3 migrate_v2_0_to_v2_1.py [db_path]
    python3 migrate_v2_0_to_v2_1.py                    # uses default .context/memory_graph.db
    python3 migrate_v2_0_to_v2_1.py /path/to/custom.db
"""
from __future__ import annotations

import sys
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime


DEFAULT_DB = Path(".context/memory_graph.db")
BACKUP_SUFFIX = ".v2.0.bak"

# v2.1 schema (only the new/changed parts)
V21_SCHEMA_SQL = """
-- New table: worlds
CREATE TABLE IF NOT EXISTS worlds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER PRIMARY KEY,
    applied_at   TEXT DEFAULT (datetime('now')),
    description  TEXT
);

-- v2.1 entities table (with layer + world)
CREATE TABLE IF NOT EXISTS entities_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    type          TEXT DEFAULT 'unknown',
    aliases_json  TEXT DEFAULT '[]',
    properties_json TEXT DEFAULT '{}',
    confidence    REAL DEFAULT 1.0,
    origin        TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    layer         TEXT DEFAULT 'fact',
    world         TEXT DEFAULT '',
    UNIQUE(name, type, layer, world)
);

-- v2.1 relations table (with layer + world)
CREATE TABLE IF NOT EXISTS relations_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL,
    predicate     TEXT NOT NULL,
    object_id     INTEGER NOT NULL,
    confidence    REAL DEFAULT 1.0,
    origin        TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    layer         TEXT DEFAULT 'fact',
    world         TEXT DEFAULT '',
    FOREIGN KEY (subject_id) REFERENCES entities_new(id),
    FOREIGN KEY (object_id)  REFERENCES entities_new(id),
    UNIQUE(subject_id, predicate, object_id, layer, world)
);

-- v2.1 facts table (with layer + world)
CREATE TABLE IF NOT EXISTS facts_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER,
    predicate     TEXT,
    object_id     INTEGER,
    value         TEXT,
    confidence    REAL DEFAULT 1.0,
    source        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    layer         TEXT DEFAULT 'fact',
    world         TEXT DEFAULT '',
    FOREIGN KEY (subject_id) REFERENCES entities_new(id),
    FOREIGN KEY (object_id)  REFERENCES entities_new(id)
);

-- Indexes for v2.1 (composite to leverage layer/world filtering)
CREATE INDEX IF NOT EXISTS idx_entities_name_new ON entities_new(name);
CREATE INDEX IF NOT EXISTS idx_entities_type_new ON entities_new(type);
CREATE INDEX IF NOT EXISTS idx_entities_layer_world ON entities_new(layer, world);
CREATE INDEX IF NOT EXISTS idx_relations_subject_new ON relations_new(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object_new ON relations_new(object_id);
CREATE INDEX IF NOT EXISTS idx_relations_layer_world ON relations_new(layer, world);
CREATE INDEX IF NOT EXISTS idx_timeline_entity ON timeline(entity_id);
"""


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return current schema version. 0 = pre-v2.1 (no version table)."""
    try:
        cur = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet
        return 0


def has_v21_columns(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table already has 'layer' column (v2.1 marker)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cur.fetchall()}
    return "layer" in columns


def backup_db(db_path: Path) -> Path:
    """Backup the existing DB. Returns backup path."""
    backup_path = db_path.with_suffix(db_path.suffix + BACKUP_SUFFIX)
    if backup_path.exists():
        # Don't overwrite an existing backup
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path.with_suffix(f"{db_path.suffix}.{stamp}{BACKUP_SUFFIX}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def migrate(db_path: Path) -> dict:
    """
    Run the v2.0 → v2.1 migration.

    Returns dict with stats: {backup_path, entities_migrated, relations_migrated,
                              facts_migrated, schema_version}
    """
    if not db_path.exists():
        # No DB yet — fresh install, just create v2.1 schema directly
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(V21_SCHEMA_SQL)
            # Rename *_new tables to canonical names
            for table in ("entities", "relations", "facts"):
                conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (2, "v2.1 initial — binary observer architecture"),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "backup_path": None,
            "entities_migrated": 0,
            "relations_migrated": 0,
            "facts_migrated": 0,
            "schema_version": 2,
            "fresh_install": True,
        }

    # Existing DB — full migration
    backup_path = backup_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Idempotency check
        current_version = get_schema_version(conn)
        if current_version >= 2:
            return {
                "backup_path": None,
                "entities_migrated": 0,
                "relations_migrated": 0,
                "facts_migrated": 0,
                "schema_version": current_version,
                "already_migrated": True,
            }

        # Pre-check: are v2.0 tables present?
        v20_entities_count = count_rows(conn, "entities") if has_table(conn, "entities") else 0
        v20_relations_count = count_rows(conn, "relations") if has_table(conn, "relations") else 0
        v20_facts_count = count_rows(conn, "facts") if has_table(conn, "facts") else 0

        # Step 1: create new tables
        conn.executescript(V21_SCHEMA_SQL)

        # Step 2: copy data from old tables to new (with layer='fact', world='')
        if has_table(conn, "entities"):
            conn.execute("""
                INSERT INTO entities_new
                    (id, name, type, aliases_json, properties_json, confidence,
                     origin, created_at, updated_at, layer, world)
                SELECT id, name, type, aliases_json, properties_json, confidence,
                       origin, created_at, updated_at, 'fact', ''
                FROM entities
            """)

        if has_table(conn, "relations"):
            conn.execute("""
                INSERT INTO relations_new
                    (id, subject_id, predicate, object_id, confidence,
                     origin, created_at, layer, world)
                SELECT id, subject_id, predicate, object_id, confidence,
                       origin, created_at, 'fact', ''
                FROM relations
            """)

        if has_table(conn, "facts"):
            conn.execute("""
                INSERT INTO facts_new
                    (id, subject_id, predicate, object_id, value, confidence,
                     source, created_at, layer, world)
                SELECT id, subject_id, predicate, object_id, value, confidence,
                       source, created_at, 'fact', ''
                FROM facts
            """)

        # Step 3: verify row counts
        v21_entities_count = count_rows(conn, "entities_new")
        v21_relations_count = count_rows(conn, "relations_new")
        v21_facts_count = count_rows(conn, "facts_new")

        assert v21_entities_count == v20_entities_count, (
            f"Entity count mismatch: v2.0={v20_entities_count}, v2.1={v21_entities_count}"
        )
        assert v21_relations_count == v20_relations_count, (
            f"Relation count mismatch: v2.0={v20_relations_count}, v2.1={v21_relations_count}"
        )
        assert v21_facts_count == v20_facts_count, (
            f"Fact count mismatch: v2.0={v20_facts_count}, v2.1={v21_facts_count}"
        )

        # Step 4: atomic swap — drop old, rename new
        # Disable foreign keys during swap (sqlite limitation)
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("entities", "relations", "facts"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.execute("PRAGMA foreign_keys = ON")

        # Step 5: record schema version
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (2, "v2.1 migration — added layer/world columns, worlds table"),
        )

        conn.commit()

        return {
            "backup_path": str(backup_path),
            "entities_migrated": v21_entities_count,
            "relations_migrated": v21_relations_count,
            "facts_migrated": v21_facts_count,
            "schema_version": 2,
            "fresh_install": False,
            "already_migrated": False,
        }
    finally:
        conn.close()


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    print(f"» Migrating: {db_path}")

    result = migrate(db_path)

    if result.get("already_migrated"):
        print(f"✓ Already at v2.1 (schema_version={result['schema_version']}). Nothing to do.")
        return 0

    if result.get("fresh_install"):
        print(f"✓ Fresh install — created v2.1 schema directly at {db_path}")
        print(f"  Schema version: {result['schema_version']}")
        return 0

    print(f"✓ Migration complete.")
    print(f"  Backup:         {result['backup_path']}")
    print(f"  Entities:       {result['entities_migrated']} rows migrated")
    print(f"  Relations:      {result['relations_migrated']} rows migrated")
    print(f"  Facts:          {result['facts_migrated']} rows migrated")
    print(f"  Schema version: {result['schema_version']}")
    print()
    print("  All existing data assigned to layer='fact', world='' (default).")
    print("  Worlds table created. Ready for v2.1 binary observer architecture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
