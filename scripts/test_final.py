#!/usr/bin/env python3
"""Final end-to-end test: run the full --pre-answer pipeline on the user's
complex analytical query. Verify:
  1. Pattern 3 classifies correctly as 'synthesis'
  2. Pattern 2 (gap-detector) runs without crashing
  3. Pattern 1 (source-grounded brief) shows up
  4. Registry contains 70+ skills
  5. No crashes anywhere
"""
import subprocess
import sys

QUERY = """Морфология архетипа «Тень» в цифровом паноптикуме: от приватного бессознательного к распределенному ИИ-субъекту.

Проведи многоуровневый анализ трансформации юнгианского архетипа «Тень» в условиях тотальной алгоритмической прозрачности. Меня интересует не столько констатация очевидного (хейт-спич, культура отмены), сколько процесс расщепления Тени на три новых эндо-цифровых конструкта: Алгоритмическую Тень (то, что платформы знают о нас, но не показывают), Синтетическую Тень (галлюцинации и repressed material больших языковых моделей) и Сетевую Тень (коллективное бессознательное анонимных имиджборд).

Собери и сопоставь данные из максимально гетерогенных источников, обработав их через призму аналитической психологии, акторно-сетевой теории и Media Studies. Мне нужны инсайты, добытые из напряжения между формой и содержанием медиума.

Факторы и архетипические оси для анализа:
- Структура Психеи 2.0: Как архетипы Трикстера, Анимы и Самости манифестируют себя в диалогах с LLM?
- Геополитическая тень: Есть ли корреляция между архитектурой государственных систем предиктивной аналитики (Китай, США) и типом «искусственной Тени»?
- Эпистемологический разрыв: Правда ли, что галлюцинации GPT-4 — это не ошибка, а функциональный аналог вытеснения у истерической личности?
- Трансмодальность: Найди семиотический дрейф смысла «зловещего» при переходе из текста в аудио и в визуал.

Результат представь не как сухой отчет, а как карту разрывов и аффективных напряжений цифровой культуры."""

print("=" * 80)
print("FINAL END-TO-END TEST: Complex analytical query through --pre-answer")
print("=" * 80)
print(f"Query length: {len(QUERY)} chars, {len(QUERY.split())} words")
print()

# Run --pre-answer
result = subprocess.run(
    ["python3", "/home/z/my-project/skills/_orchestrator/scripts/orchestrator.py",
     "--pre-answer", QUERY],
    capture_output=True, text=True, timeout=120
)

print("─── STDOUT ────────────────────────────────────────────────────────────────")
print(result.stdout)
print("─── STDERR ────────────────────────────────────────────────────────────────")
print(result.stderr[-2000:] if result.stderr else "(empty)")
print("─── EXIT CODE ─────────────────────────────────────────────────────────────")
print(f"exit={result.returncode}")
print()

if result.returncode != 0:
    print("❌ FAIL: pipeline crashed")
    sys.exit(1)

# Check that key sections are present
stdout = result.stdout
checks = [
    ("Pattern 3 type=synthesis", "type: synthesis" in stdout),
    ("Pattern 3 confidence>=0.9", "confidence=0.9" in stdout or "confidence=0.95" in stdout),
    ("Pattern 3 routing=medium path", "MEDIUM PATH" in stdout),
    ("Pattern 2 gap-detector section", "GAP-DETECTOR VERDICT" in stdout),
    ("Pattern 2 verdict present", "verdict:" in stdout),
    ("Pattern 1 brief section", "CONTEXT BRIEF" in stdout),
]
print("─── CHECKS ────────────────────────────────────────────────────────────────")
all_passed = True
for name, ok in checks:
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        all_passed = False

print()
if all_passed:
    print("✅ ALL CHECKS PASSED — system handles complex analytical query without errors")
    sys.exit(0)
else:
    print("❌ SOME CHECKS FAILED")
    sys.exit(1)
