#!/usr/bin/env python3
"""
test_v21_worldbuilding.py — Tests for v2.1 Worldbuilding Mode

Tests:
    1. Layer/world schema is properly applied
    2. Thesis-layer entities require a world
    3. Cross-layer contradiction check is forbidden
    4. Cross-world contradiction check is forbidden
    5. Same-world contradiction IS detected
    6. Backwards compat: skills without layer field default to 'fact'
    7. World CRUD: create, list, get, rename, delete
    8. World export/import roundtrip
    9. World name validation
"""
import sys
import os
import tempfile
from pathlib import Path

# Add scripts to path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "_orchestrator" / "scripts"))
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "_shared"))

from memory_graph import MemoryGraph, VALID_LAYERS
from worlds import WorldManager
from context_builder import ContextBuilder, SkillResult
import time


def test_layer_world_schema():
    """Test 1: Schema includes layer and world columns."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        g = MemoryGraph(db)
        with g._conn() as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(entities)").fetchall()]
            assert "layer" in cols, "entities.layer missing"
            assert "world" in cols, "entities.world missing"
            cols_r = [r[1] for r in c.execute("PRAGMA table_info(relations)").fetchall()]
            assert "layer" in cols_r, "relations.layer missing"
            assert "world" in cols_r, "relations.world missing"
            # Worlds table
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            assert "worlds" in tables, "worlds table missing"
            assert "schema_version" in tables, "schema_version table missing"
        print("✓ test_layer_world_schema")


def test_thesis_requires_world():
    """Test 2: Thesis-layer entities must have a non-empty world."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        try:
            g.add_entity("elves", "species", layer="thesis", world="")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "world" in str(e).lower()
        # Valid: thesis with world
        eid = g.add_entity("elves", "species", layer="thesis", world="arelia")
        assert eid > 0
        print("✓ test_thesis_requires_world")


def test_invalid_layer_rejected():
    """Test 3: Invalid layer values are rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        for bad in ("invalid", "facts", "thes", "hypo"):
            try:
                g.add_entity("x", "y", layer=bad)
                assert False, f"Should have raised for layer={bad!r}"
            except ValueError:
                pass
        print("✓ test_invalid_layer_rejected")


def test_cross_layer_no_contradiction():
    """Test 4: Same entity in fact vs thesis layer = NO contradiction."""
    with tempfile.TemporaryDirectory() as tmp:
        cb = ContextBuilder(Path(tmp) / "test.db")
        sa = SkillResult(
            skill_name="web-search",
            output={
                "status": "ok", "confidence": 0.9, "summary": "Magic is cultural",
                "entities": [
                    {"name": "magic", "type": "concept", "layer": "fact",
                     "properties": {"nature": "cultural"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        sb = SkillResult(
            skill_name="blog-writer",
            output={
                "status": "ok", "confidence": 0.88, "summary": "Magic is physics in Arelia",
                "entities": [
                    {"name": "magic", "type": "concept", "layer": "thesis", "world": "arelia",
                     "properties": {"nature": "physical"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        contradictions = cb._detect_contradictions([sa, sb])
        assert len(contradictions) == 0, f"Expected 0, got {len(contradictions)}"
        print("✓ test_cross_layer_no_contradiction")


def test_cross_world_no_contradiction():
    """Test 5: Same entity in different worlds = NO contradiction."""
    with tempfile.TemporaryDirectory() as tmp:
        cb = ContextBuilder(Path(tmp) / "test.db")
        sa = SkillResult(
            skill_name="blog-writer",
            output={
                "status": "ok", "confidence": 0.85, "summary": "Arelia elves are tall",
                "entities": [
                    {"name": "elves", "type": "species", "layer": "thesis", "world": "arelia",
                     "properties": {"height": "tall"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        sb = SkillResult(
            skill_name="seo-content-writer",
            output={
                "status": "ok", "confidence": 0.80, "summary": "Vordania elves are short",
                "entities": [
                    {"name": "elves", "type": "species", "layer": "thesis", "world": "vordania",
                     "properties": {"height": "short"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        contradictions = cb._detect_contradictions([sa, sb])
        assert len(contradictions) == 0, f"Expected 0, got {len(contradictions)}"
        print("✓ test_cross_world_no_contradiction")


def test_same_world_contradiction_detected():
    """Test 6: Same entity in same world with conflicting properties = contradiction."""
    with tempfile.TemporaryDirectory() as tmp:
        cb = ContextBuilder(Path(tmp) / "test.db")
        sa = SkillResult(
            skill_name="blog-writer",
            output={
                "status": "ok", "confidence": 0.85, "summary": "Arelia elves are tall",
                "entities": [
                    {"name": "elves", "type": "species", "layer": "thesis", "world": "arelia",
                     "properties": {"height": "tall"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        sb = SkillResult(
            skill_name="contentanalysis",
            output={
                "status": "ok", "confidence": 0.82, "summary": "Arelia elves are short",
                "entities": [
                    {"name": "elves", "type": "species", "layer": "thesis", "world": "arelia",
                     "properties": {"height": "short"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        contradictions = cb._detect_contradictions([sa, sb])
        assert len(contradictions) == 1, f"Expected 1, got {len(contradictions)}"
        c = contradictions[0]
        assert c["layer"] == "thesis"
        assert c["world"] == "arelia"
        assert c["property"] == "height"
        print("✓ test_same_world_contradiction_detected")


def test_backwards_compat_no_layer():
    """Test 7: Skills without layer field default to 'fact' and world=''."""
    with tempfile.TemporaryDirectory() as tmp:
        cb = ContextBuilder(Path(tmp) / "test.db")
        sa = SkillResult(
            skill_name="web-search",
            output={
                "status": "ok", "confidence": 0.9, "summary": "GPT-4 released 2023",
                "entities": [
                    {"name": "GPT-4", "type": "model",
                     "properties": {"released": "2023"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        sb = SkillResult(
            skill_name="blog-writer",
            output={
                "status": "ok", "confidence": 0.85, "summary": "GPT-4 released 2024",
                "entities": [
                    {"name": "GPT-4", "type": "model",
                     "properties": {"released": "2024"}},
                ],
                "relations": [], "sources": [], "warnings": [], "artifacts": [], "metrics": {},
            },
            started_at=time.time() - 1, finished_at=time.time(),
        )
        contradictions = cb._detect_contradictions([sa, sb])
        # Both default to fact/''  → 1 contradiction
        assert len(contradictions) == 1, f"Expected 1, got {len(contradictions)}"
        assert contradictions[0]["layer"] == "fact"
        assert contradictions[0]["world"] == ""
        print("✓ test_backwards_compat_no_layer")


def test_world_crud():
    """Test 8: World create/list/get/rename/delete."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        wm = WorldManager(g)

        # Create
        w = wm.create("arelia", "Lunar magic kingdom")
        assert w["name"] == "arelia"
        assert wm.exists("arelia")

        # Add entity
        g.add_relation("elves", "are", "tall", "species", "property",
                       origin="test", layer="thesis", world="arelia")

        # List
        worlds = wm.list_all()
        assert len(worlds) == 1
        assert worlds[0]["name"] == "arelia"
        assert worlds[0]["entity_count"] >= 1

        # Get
        w = wm.get("arelia")
        assert w is not None
        assert w["description"] == "Lunar magic kingdom"

        # Rename
        wm.rename("arelia", "Arelia Prime")
        assert not wm.exists("arelia")
        assert wm.exists("Arelia Prime")

        # Delete (with archive)
        result = wm.delete("Arelia Prime", archive_first=True)
        assert result["deleted"]
        assert not wm.exists("Arelia Prime")
        print("✓ test_world_crud")


def test_world_export_import():
    """Test 9: World export/import roundtrip preserves data."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        wm = WorldManager(g)
        wm.create("arelia", "Lunar magic kingdom")
        g.add_relation("Arelia", "has_capital", "Tarrael", "kingdom", "city",
                       origin="test", layer="thesis", world="arelia")
        g.add_relation("elves", "are", "tall", "species", "property",
                       origin="test", layer="thesis", world="arelia")

        # Count before
        before = wm.get("arelia")
        assert before["entity_count"] == 4  # Arelia, Tarrael, elves, tall

        # Export
        exported = wm.export("arelia")
        assert exported["stats"]["entity_count"] == 4
        assert exported["stats"]["relation_count"] == 2

        # Delete
        wm.delete("arelia", archive_first=False)
        assert not wm.exists("arelia")

        # Import
        result = wm.import_data(exported)
        assert result["entities_imported"] == 4
        assert result["relations_imported"] == 2

        # Verify
        after = wm.get("arelia")
        assert after is not None
        assert after["entity_count"] == 4

        # Verify elves is exactly 1 (not duplicated)
        ents = g.query_entities(name="elves", layer="thesis", world="arelia")
        # Should match 'elves' itself
        elves_ents = [e for e in ents if e["name"] == "elves"]
        assert len(elves_ents) == 1, f"Expected 1 elves, got {len(elves_ents)}"
        print("✓ test_world_export_import")


def test_world_name_validation():
    """Test 10: World names are validated."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        wm = WorldManager(g)

        # Empty name
        try:
            wm.create("", "empty")
            assert False, "Should reject empty name"
        except ValueError:
            pass

        # Slash in name
        try:
            wm.create("bad/name", "slash")
            assert False, "Should reject slash"
        except ValueError:
            pass

        # Too long
        try:
            wm.create("a" * 100, "too long")
            assert False, "Should reject too long"
        except ValueError:
            pass

        # Valid names
        for valid in ["arelia", "Arelia-Prime", "world_1", "Мир 1"]:
            try:
                wm.create(valid, "test")
            except ValueError as e:
                assert False, f"Should accept {valid!r}: {e}"

        print("✓ test_world_name_validation")


def test_stats_by_layer():
    """Test 11: Stats include per-layer breakdown."""
    with tempfile.TemporaryDirectory() as tmp:
        g = MemoryGraph(Path(tmp) / "test.db")
        # Add facts
        g.add_entity("Paris", "city", origin="test")
        g.add_relation("Paris", "capital_of", "France", "city", "country", origin="test")
        # Add hypothesis
        g.add_entity("GPT-5", "model", origin="test", layer="hypothesis")
        # Add thesis
        g.create_world("arelia", "test")
        g.add_entity("elves", "species", origin="test", layer="thesis", world="arelia")
        g.add_relation("elves", "are", "tall", "species", "property",
                       origin="test", layer="thesis", world="arelia")

        stats = g.stats()
        assert stats["by_layer"]["fact"]["entities"] >= 2
        assert stats["by_layer"]["hypothesis"]["entities"] >= 1
        assert stats["by_layer"]["thesis"]["entities"] >= 1
        assert stats["total"]["worlds"] == 1
        print("✓ test_stats_by_layer")


if __name__ == "__main__":
    test_layer_world_schema()
    test_thesis_requires_world()
    test_invalid_layer_rejected()
    test_cross_layer_no_contradiction()
    test_cross_world_no_contradiction()
    test_same_world_contradiction_detected()
    test_backwards_compat_no_layer()
    test_world_crud()
    test_world_export_import()
    test_world_name_validation()
    test_stats_by_layer()
    print()
    print("🎉 All v2.1 worldbuilding tests passed!")
