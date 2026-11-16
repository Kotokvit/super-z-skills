#!/usr/bin/env python3
"""
worlds.py — World Manager (v2.1)

Thin wrapper around MemoryGraph for world CRUD operations.

Per the binary observer architecture (doc 04):
    This module provides STORAGE ONLY — no decision logic.
    The observer (observer.py) decides WHICH layer/world to assign to each entity.
    This module just stores, lists, archives, imports, and exports worlds.

A "world" is an isolated container for thesis-layer entities.
    - Theses in different worlds are NEVER compared for contradiction.
    - Theses in the same world ARE checked for internal consistency.
    - Facts and hypotheses don't belong to worlds (world='').

API:
    wm = WorldManager(graph)
    wm.create("arelia", description="Lunar magic kingdom")
    wm.list_all()
    wm.get("arelia")
    wm.rename("arelia", "Arelia Prime")
    wm.archive("arelia", path=".context/worlds_archive/arelia_2026-07-04.json")
    wm.delete("arelia", archive_first=True)
    wm.export("arelia")
    wm.import_data({...})
    wm.exists("arelia")
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import sibling module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_graph import MemoryGraph, VALID_LAYERS  # noqa: E402


class WorldManager:
    """Thin CRUD wrapper around MemoryGraph's world operations.

    No decision logic — the observer (observer.py) decides which world to use.
    This class just manages the storage and lifecycle of worlds.
    """

    DEFAULT_ARCHIVE_DIR = Path(".context/worlds_archive")

    def __init__(self, graph: MemoryGraph):
        self.graph = graph

    # ── Validation ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_name(name: str) -> str:
        """World names must be filesystem-safe (for archive paths)."""
        if not name:
            raise ValueError("World name cannot be empty")
        if not re.match(r"^[a-zA-Z0-9_\-\u0400-\u04FF ]+$", name):
            raise ValueError(
                f"World name '{name}' contains invalid characters. "
                "Allowed: letters, digits, spaces, hyphens, underscores."
            )
        if len(name) > 64:
            raise ValueError(f"World name too long ({len(name)} chars, max 64)")
        return name

    # ── CRUD ───────────────────────────────────────────────────────────

    def create(self, name: str, description: str = "") -> dict:
        """Create a new fictional world. Returns world info dict.
        Idempotent: if world exists, updates description and returns existing.
        """
        self._validate_name(name)
        self.graph.create_world(name, description)
        return self.get(name)

    def list_all(self) -> list[dict]:
        """List all fictional worlds, with stats."""
        return self.graph.list_worlds()

    def get(self, name: str) -> Optional[dict]:
        """Get a single world by name. Returns None if not found."""
        return self.graph.get_world(name)

    def exists(self, name: str) -> bool:
        """Check if a world exists."""
        return self.get(name) is not None

    def rename(self, old_name: str, new_name: str) -> dict:
        """Rename a world. Updates all references in entities/relations/facts.
        Returns updated world info.
        """
        self._validate_name(new_name)
        if not self.exists(old_name):
            raise ValueError(f"World '{old_name}' not found")
        if self.exists(new_name) and old_name != new_name:
            raise ValueError(f"World '{new_name}' already exists")

        # Update all references
        with self.graph._conn() as c:
            c.execute("UPDATE entities SET world = ? WHERE world = ?", (new_name, old_name))
            c.execute("UPDATE relations SET world = ? WHERE world = ?", (new_name, old_name))
            c.execute("UPDATE facts SET world = ? WHERE world = ?", (new_name, old_name))
            c.execute("UPDATE worlds SET name = ? WHERE name = ?", (new_name, old_name))

        return self.get(new_name)

    def update_description(self, name: str, description: str) -> dict:
        """Update only the description of a world."""
        if not self.exists(name):
            raise ValueError(f"World '{name}' not found")
        with self.graph._conn() as c:
            c.execute(
                "UPDATE worlds SET description = ? WHERE name = ?", (description, name)
            )
        return self.get(name)

    def delete(
        self,
        name: str,
        archive_first: bool = True,
        archive_path: Optional[Path] = None,
    ) -> dict:
        """Delete a world.
        If archive_first=True (default), exports the world to JSON first.
        Returns dict with archive_path if archived, deleted=True/False.
        """
        if not self.exists(name):
            return {"deleted": False, "archive_path": None, "reason": "not_found"}

        archive_used = None
        if archive_first:
            if archive_path is None:
                archive_path = self.DEFAULT_ARCHIVE_DIR / f"{self._safe_filename(name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            archive_path = Path(archive_path)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            data = self.graph.export_world(name)
            archive_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            archive_used = str(archive_path)

        deleted = self.graph.delete_world(name)
        return {"deleted": deleted, "archive_path": archive_used}

    # ── Export / Import ────────────────────────────────────────────────

    def export(self, name: str) -> dict:
        """Export a world as a dict (JSON-serializable)."""
        if not self.exists(name):
            raise ValueError(f"World '{name}' not found")
        return self.graph.export_world(name)

    def import_data(self, data: dict, overwrite: bool = False) -> dict:
        """Import a world from a dict. Returns import stats."""
        return self.graph.import_world(data, overwrite=overwrite)

    def export_to_file(self, name: str, path: Path | str) -> Path:
        """Export a world to a JSON file. Returns the path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.export(name)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def import_from_file(self, path: Path | str, overwrite: bool = False) -> dict:
        """Import a world from a JSON file. Returns import stats."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"World archive not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return self.import_data(data, overwrite=overwrite)

    # ── Listing helpers ────────────────────────────────────────────────

    def entities_in(self, name: str, limit: int = 100) -> list[dict]:
        """List all entities in a world (thesis layer only)."""
        return self.graph.query_entities(world=name, layer="thesis", limit=limit)

    def relations_in(self, name: str, limit: int = 100) -> list[dict]:
        """List all relations in a world (thesis layer only)."""
        return self.graph.query_relations(world=name, layer="thesis", limit=limit)

    def search_in(
        self,
        name: str,
        query: str,
        limit: int = 20,
    ) -> dict:
        """Search entities + relations by name within a world."""
        return {
            "entities": self.graph.query_entities(name=query, world=name, layer="thesis", limit=limit),
            "relations": self.graph.query_relations(subject=query, world=name, layer="thesis", limit=limit),
        }

    # ── Stats ──────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Aggregate stats across all worlds."""
        worlds = self.list_all()
        return {
            "total_worlds": len(worlds),
            "total_entities": sum(w.get("entity_count", 0) for w in worlds),
            "total_relations": sum(w.get("relation_count", 0) for w in worlds),
            "worlds": [
                {
                    "name": w["name"],
                    "description": w.get("description", ""),
                    "entities": w.get("entity_count", 0),
                    "relations": w.get("relation_count", 0),
                    "created_at": w.get("created_at", ""),
                }
                for w in worlds
            ],
        }

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Convert a world name to a filesystem-safe filename component."""
        # Replace spaces and problematic chars with underscore
        safe = re.sub(r"[^\w\-.]", "_", name)
        return safe or "untitled"


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: worlds.py [list|create|get|delete|export|import|stats] [...]")
        print("  worlds.py list")
        print("  worlds.py create <name> [description]")
        print("  worlds.py get <name>")
        print("  worlds.py delete <name> [--no-archive]")
        print("  worlds.py export <name> [output.json]")
        print("  worlds.py import <input.json> [--overwrite]")
        print("  worlds.py stats")
        sys.exit(0)

    cmd = sys.argv[1]
    graph = MemoryGraph()
    wm = WorldManager(graph)

    if cmd == "list":
        worlds = wm.list_all()
        if not worlds:
            print("(no worlds yet)")
        for w in worlds:
            print(f"  {w['name']:20s}  entities={w.get('entity_count', 0):3d}  "
                  f"relations={w.get('relation_count', 0):3d}  "
                  f"created={w.get('created_at', '?')}")
            if w.get("description"):
                print(f"    {w['description']}")

    elif cmd == "create":
        if len(sys.argv) < 3:
            print("Usage: worlds.py create <name> [description]")
            sys.exit(1)
        name = sys.argv[2]
        desc = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""
        try:
            w = wm.create(name, desc)
            print(f"✓ Created world '{w['name']}': {w.get('description', '')}")
        except ValueError as e:
            print(f"✗ {e}")
            sys.exit(1)

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: worlds.py get <name>")
            sys.exit(1)
        w = wm.get(sys.argv[2])
        if not w:
            print(f"✗ World '{sys.argv[2]}' not found")
            sys.exit(1)
        print(json.dumps(w, indent=2, ensure_ascii=False))
        print("\nEntities (first 20):")
        for e in wm.entities_in(w["name"], limit=20):
            print(f"  {e['name']:25s} ({e['type']})")

    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("Usage: worlds.py delete <name> [--no-archive]")
            sys.exit(1)
        name = sys.argv[2]
        archive_first = "--no-archive" not in sys.argv
        result = wm.delete(name, archive_first=archive_first)
        if result["deleted"]:
            print(f"✓ Deleted world '{name}'")
            if result.get("archive_path"):
                print(f"  Archived to: {result['archive_path']}")
        else:
            print(f"✗ {result.get('reason', 'unknown error')}")

    elif cmd == "export":
        if len(sys.argv) < 3:
            print("Usage: worlds.py export <name> [output.json]")
            sys.exit(1)
        name = sys.argv[2]
        output = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(f"{name}_export.json")
        try:
            path = wm.export_to_file(name, output)
            print(f"✓ Exported '{name}' to {path}")
        except ValueError as e:
            print(f"✗ {e}")
            sys.exit(1)

    elif cmd == "import":
        if len(sys.argv) < 3:
            print("Usage: worlds.py import <input.json> [--overwrite]")
            sys.exit(1)
        path = Path(sys.argv[2])
        overwrite = "--overwrite" in sys.argv
        try:
            result = wm.import_from_file(path, overwrite=overwrite)
            print(f"✓ Imported world '{result['world']}': "
                  f"{result['entities_imported']} entities, "
                  f"{result['relations_imported']} relations")
        except (FileNotFoundError, ValueError) as e:
            print(f"✗ {e}")
            sys.exit(1)

    elif cmd == "stats":
        print(json.dumps(wm.stats(), indent=2, ensure_ascii=False))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
