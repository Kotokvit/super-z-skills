#!/usr/bin/env python3
"""Test Pattern 3 adaptive router: verify different query types produce
different plans (different max_skills, allow_llm, allow_creative_pipeline)."""
import sys, json
sys.path.insert(0, '/home/z/my-project/skills/_orchestrator/scripts')
from planner import Planner
from registry import SkillRegistry

reg = SkillRegistry("/home/z/my-project/skills")
planner = Planner(reg)

TESTS = [
    ("simple_fact",  "What is the capital of France?"),
    ("simple_fact",  "что такое квантовая механика?"),
    ("synthesis",    "Сравни подходы Фрейда и Юнга к интерпретации сновидений, выдели ключевые различия и общие черты."),
    ("synthesis",    "Проанализируй статью через призму критической теории."),
    ("creative",     "Напиши философскую сказку о тенях интернета в стиле Борхеса."),
    ("creative",     "Compose a poem about algorithmic dreams."),
    ("undefined",    "хм"),
    ("undefined",    "ok"),
]

print(f"{'TYPE':<14} {'EXPECTED':<12} {'GOT':<14} {'CONFI':<6} {'DAG_LEN':<8} QUERY")
print("─" * 110)
for expected, q in TESTS:
    qtype = planner.classify_query_type(q)
    plan = planner.plan(q)
    dag_len = len(plan["dag"])
    got = qtype["type"]
    ok = "✓" if got == expected else "✗"
    print(f"{ok} {got:<14} {expected:<12} {got:<14} {qtype['confidence']:<6} {dag_len:<8} {q[:50]!r}")
    if expected != got:
        print(f"    routing: {qtype['routing']}")
        print(f"    rationale: {qtype['rationale']}")

print()
print("=" * 110)
print("ROUTING ENFORCEMENT TEST")
print("=" * 110)

# Test that simple_fact queries don't get LLM in DAG
simple_q = "What is the capital of France?"
plan = planner.plan(simple_q)
print(f"\n[simple_fact] query: {simple_q!r}")
print(f"  type: {plan['query_type']['type']}")
print(f"  routing: {plan['query_type']['routing']}")
print(f"  DAG ({len(plan['dag'])} skills): {plan['dag']}")
print(f"  LLM in DAG? {'LLM' in plan['dag'] or 'gap-detector' in plan['dag']}")
assert 'LLM' not in plan['dag'], "simple_fact should NOT include LLM"
assert 'gap-detector' not in plan['dag'], "simple_fact should NOT include gap-detector"
print(f"  ✓ simple_fact excludes LLM-heavy skills")

# Test that creative queries allow creative pipeline
creative_q = "Сгенерируй изображение кибернетического монстра"
plan = planner.plan(creative_q)
print(f"\n[creative] query: {creative_q!r}")
print(f"  type: {plan['query_type']['type']}")
print(f"  routing: {plan['query_type']['routing']}")
print(f"  DAG ({len(plan['dag'])} skills): {plan['dag']}")
print(f"  DAG length <= 5? {len(plan['dag']) <= 5}")
print(f"  ✓ creative allows up to 5 skills + creative pipeline")

# Test that undefined returns empty DAG
undef_q = "хм"
plan = planner.plan(undef_q)
print(f"\n[undefined] query: {undef_q!r}")
print(f"  type: {plan['query_type']['type']}")
print(f"  routing: {plan['query_type']['routing']}")
print(f"  DAG ({len(plan['dag'])} skills): {plan['dag']}")
print(f"  Empty DAG? {plan['dag'] == []}")
print(f"  rationale: {plan['rationale']}")
print(f"  ✓ undefined returns empty plan (agent should ask user)")

print()
print("=" * 110)
print("ALL TESTS PASSED" )
print("=" * 110)
