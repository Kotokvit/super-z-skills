#!/usr/bin/env python3
"""Debug Pattern 3 classifier on the complex analytical query."""
import sys, json
sys.path.insert(0, '/home/z/my-project/skills/_orchestrator/scripts')
from planner import Planner
from registry import SkillRegistry

QUERY = """Проведи многоуровневый анализ трансформации юнгианского архетипа «Тень» в условиях тотальной алгоритмической прозрачности. Меня интересует не столько констатация очевидного (хейт-спич, культура отмены), сколько процесс расщепления Тени на три новых эндо-цифровых конструкта: Алгоритмическую Тень, Синтетическую Тень и Сетевую Тень. Собери и сопоставь данные из максимально гетерогенных источников, обработав их через призму аналитической психологии, акторно-сетевой теории и Media Studies."""

reg = SkillRegistry("/home/z/my-project/skills")
planner = Planner(reg)

# Test each pattern individually
print("=== Pattern matching debug ===")
print(f"Query length: {len(QUERY)}")
print(f"Query words: {len(QUERY.split())}")
print()
for i, pat in enumerate(planner.SIMPLE_FACT_PATTERNS):
    m = pat.search(QUERY)
    print(f"SIMPLE_FACT_PATTERNS[{i}]: {'MATCH' if m else 'no'} — pattern={pat.pattern[:80]}")
    if m:
        print(f"  matched text: {m.group(0)!r}")
for i, pat in enumerate(planner.SYNTHESIS_PATTERNS):
    m = pat.search(QUERY)
    print(f"SYNTHESIS_PATTERNS[{i}]: {'MATCH' if m else 'no'} — pattern={pat.pattern[:80]}")
    if m:
        print(f"  matched text: {m.group(0)!r}")
for i, pat in enumerate(planner.CREATIVE_PATTERNS):
    m = pat.search(QUERY)
    print(f"CREATIVE_PATTERNS[{i}]: {'MATCH' if m else 'no'} — pattern={pat.pattern[:80]}")
    if m:
        print(f"  matched text: {m.group(0)!r}")
print()
print("=== Final classification ===")
result = planner.classify_query_type(QUERY)
print(json.dumps(result, ensure_ascii=False, indent=2))
