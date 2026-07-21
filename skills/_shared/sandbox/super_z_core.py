#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Z Core — Нервная Система
================================
Архитектура Super Z:
  poler_enhanced.py = Ядро Смысла — превращает данные в знание
  super_z_core.py   = Нервная Система — маршрутизация задач
  super_z_bridge.py  = Руки и Глаза — взаимодействие с миром
  grab              = Пылесос — сбор данных (без понимания)

ПРИНЦИП: ЛЮБОЙ навык проходит через Ядро Смысла ПЕРЕД LLM.
  1. Задача → POLER Core (понимание) → Маршрутизация
  2. Если POLER Core дал достаточно → Local-First (без LLM)
  3. Если нужен LLM → обогащённый контекст от POLER Core

МАРШРУТИЗАЦИЯ:
  Local → POLER Core → [достаточно?] → Ответ
                            ↓ НЕТ
                       → AI (LLM) → Ответ
                            ↓ НЕТ
                       → CLI (команда) → Ответ
                            ↓ НЕТ
                       → Human (ручной разбор)

ПРОТОТИП: Python → миграция на Rust/Zig/C
  - Маршрутизатор → Rust: match + async (tokio)
  - POLER Core вызов → Zig: zero-copy FFI
  - CLI bridge → C: POSIX execve
"""

import json
import os
import sys
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Callable

# ═══════════════════════════════════════════════════════════════════════
# ИМПОРТ ЯДРА СМЫСЛА
# ═══════════════════════════════════════════════════════════════════════
# POLER[Ψ] — обязательная зависимость. Без него — мы просто диспетчер.

POLER_CORE_PATH = os.environ.get(
    'POLER_CORE_PATH',
    str(Path(__file__).parent / 'poler_enhanced_v3.py')
)

def _import_poler_core():
    """Импортирует POLER Core. FATAL если не найден."""
    try:
        # Сначала пробуем пакетный импорт
        from poler_enhanced_v3 import PolerAnalyzer, build_veins, auto_discover_themes
        return PolerAnalyzer, build_veins, auto_discover_themes
    except ImportError:
        pass
    
    # Пробуем загрузить по пути
    core_path = Path(POLER_CORE_PATH)
    if core_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("poler_enhanced_v3", str(core_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.PolerAnalyzer, mod.build_veins, mod.auto_discover_themes
    
    raise ImportError(
        f"POLER Core не найден: {POLER_CORE_PATH}\n"
        "Ядро Смысла — обязательный компонент. Без него маршрутизация невозможна."
    )

# Ленивый импорт — загружаем при первом использовании
_poler = None

def get_poler_core():
    """Возвращает POLER Core (ленивая загрузка)."""
    global _poler
    if _poler is None:
        PolerAnalyzer, build_veins, auto_discover_themes = _import_poler_core()
        _poler = {
            'analyzer_class': PolerAnalyzer,
            'build_veins': build_veins,
            'auto_discover_themes': auto_discover_themes,
            'analyzer': PolerAnalyzer(),
        }
    return _poler

# ═══════════════════════════════════════════════════════════════════════
# ТИПЫ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Task:
    """Задача для маршрутизации."""
    task_id: str
    input_text: str = ""
    input_files: List[str] = field(default_factory=list)
    skill_name: str = ""
    intent: str = ""  # auto-detected via POLER Core
    domain: str = ""  # auto-detected via POLER Core
    confidence: float = 0.0
    context: Dict = field(default_factory=dict)
    
    # Результат POLER Core
    poler_result: Optional[Dict] = None
    poler_veins: Optional[Dict] = None

@dataclass 
class RouteDecision:
    """Решение о маршрутизации."""
    route: str  # 'local' | 'ai' | 'cli' | 'human'
    reason: str
    confidence: float
    poler_insights: Dict = field(default_factory=dict)
    estimated_cost: str = "free"  # 'free' | 'low' | 'medium' | 'high'

@dataclass
class TaskResult:
    """Результат выполнения задачи."""
    task_id: str
    route: str
    success: bool
    data: Dict = field(default_factory=dict)
    duration_ms: float = 0.0
    poler_context: Dict = field(default_factory=dict)

# ═══════════════════════════════════════════════════════════════════════
# ЯДРО МАРШРУТИЗАЦИИ
# ═══════════════════════════════════════════════════════════════════════

# Пороги confidence для принятия решений
LOCAL_THRESHOLD = float(os.environ.get('POLER_LOCAL_THRESHOLD', '0.7'))
AI_THRESHOLD = float(os.environ.get('POLER_AI_THRESHOLD', '0.4'))

# Навыки, которые МОГУТ работать локально (без LLM)
LOCAL_SKILLS = {
    'search', 'grep', 'analyze', 'extract', 'summarize',
    'transform', 'convert', 'validate', 'count', 'compare',
    'poler', 'veins', 'semantic_nav',
}

# Навыки, которые ТОЛЬКО через LLM
AI_ONLY_SKILLS = {
    'generate', 'write', 'compose', 'translate_creative',
    'brainstorm', 'creative',
}

def process_through_poler_core(task: Task) -> Task:
    """
    ОБЯЗАТЕЛЬНЫЙ ЭТАП: Пропускаем задачу через POLER Core.
    
    Это — «сердце» маршрутизации. Без этого этапа система СЛЕПА.
    POLER Core:
    1. Понимает, о чём текст/документ
    2. Извлекает ключевые слова и домены
    3. Строит семантические вены для навигации
    4. Возвращает контекст для принятия решения
    """
    core = get_poler_core()
    
    # Если есть текст — анализируем через вены
    text = task.input_text
    if not text and task.input_files:
        # Читаем первый файл
        for fpath in task.input_files:
            try:
                p = Path(fpath)
                if p.exists() and p.suffix.lower() in ['.txt', '.md', '.json', '.epub', '.html']:
                    text += p.read_text(encoding='utf-8', errors='ignore') + '\n\n'
            except:
                continue
    
    if text.strip():
        # Шаг 1: Авто-обнаружение тем
        themes = core['auto_discover_themes'](text)
        
        # Шаг 2: Построение вен
        veins = core['build_veins'](
            text,
            source_file=task.input_files[0] if task.input_files else "",
        )
        
        # Заполняем задачу результатами POLER Core
        task.poler_result = themes
        task.poler_veins = veins
        
        # Определяем домен и intent
        if themes.get('domains'):
            top_domain = themes['domains'][0]
            task.domain = top_domain['domain']
            task.confidence = top_domain['confidence']
        
        # Intent определяется по ключевым словам
        if veins.get('veins'):
            top_vein = veins['veins'][0]
            task.intent = f"{top_vein['domain']}:{top_vein['keyword']}"
    else:
        # Нет текста — минимальный контекст
        task.poler_result = {'domains': [], 'keywords': [], 'theme_map': {}}
        task.poler_veins = {'veins': [], 'domains': [], 'navigation_map': {}}
    
    return task

def decide_route(task: Task) -> RouteDecision:
    """
    ПРИНЯТИЕ РЕШЕНИЯ О МАРШРУТЕ.
    
    Логика:
    1. Если POLER Core дал высокий confidence → LOCAL (без LLM)
    2. Если средний confidence + нужен AI → AI (LLM с контекстом POLER)
    3. Если задача CLI-типа → CLI
    4. Если ничего не подошло → HUMAN
    """
    # Навык явно требует AI
    if task.skill_name in AI_ONLY_SKILLS:
        return RouteDecision(
            route='ai',
            reason=f'Навык "{task.skill_name}" требует LLM',
            confidence=task.confidence,
            poler_insights=task.poler_veins or {},
            estimated_cost='medium',
        )
    
    # POLER Core дал высокий confidence → LOCAL
    if task.confidence >= LOCAL_THRESHOLD:
        return RouteDecision(
            route='local',
            reason=f'POLER Core: домен="{task.domain}", confidence={task.confidence:.2f} >= {LOCAL_THRESHOLD}',
            confidence=task.confidence,
            poler_insights=task.poler_veins or {},
            estimated_cost='free',
        )
    
    # Средний confidence → AI с контекстом
    if task.confidence >= AI_THRESHOLD:
        return RouteDecision(
            route='ai',
            reason=f'POLER Core: домен="{task.domain}", confidence={task.confidence:.2f} — нужен LLM',
            confidence=task.confidence,
            poler_insights=task.poler_veins or {},
            estimated_cost='low',
        )
    
    # Низкий confidence → CLI или Human
    if task.skill_name in LOCAL_SKILLS or task.intent:
        return RouteDecision(
            route='cli',
            reason=f'POLER Core: низкий confidence ({task.confidence:.2f}), пробуем CLI',
            confidence=task.confidence,
            poler_insights=task.poler_veins or {},
            estimated_cost='free',
        )
    
    # Всё — к человеку
    return RouteDecision(
        route='human',
        reason=f'POLER Core: не удалось определить задачу (confidence={task.confidence:.2f})',
        confidence=task.confidence,
        poler_insights=task.poler_veins or {},
        estimated_cost='high',
    )

def execute_local(task: Task, decision: RouteDecision) -> TaskResult:
    """Выполнение локально (без LLM) — используя POLER Core."""
    start = time.time()
    core = get_poler_core()
    
    result_data = {
        'domain': task.domain,
        'confidence': task.confidence,
        'veins_count': len(task.poler_veins.get('veins', [])) if task.poler_veins else 0,
        'navigation_map': task.poler_veins.get('navigation_map', {}) if task.poler_veins else {},
    }
    
    # Если задача — навигация по документу, возвращаем вены
    if task.skill_name in ('poler', 'veins', 'semantic_nav', 'analyze', 'search'):
        result_data['veins'] = task.poler_veins
        result_data['themes'] = task.poler_result
    
    duration = (time.time() - start) * 1000
    
    return TaskResult(
        task_id=task.task_id,
        route='local',
        success=True,
        data=result_data,
        duration_ms=duration,
        poler_context=decision.poler_insights,
    )

def execute_ai(task: Task, decision: RouteDecision) -> TaskResult:
    """Выполнение через AI (LLM) — с обогащённым контекстом от POLER Core."""
    start = time.time()
    
    # Формируем enriched prompt с контекстом POLER
    poler_context = decision.poler_insights
    
    enriched_prompt = ""
    if poler_context:
        domains = poler_context.get('domains', [])
        veins = poler_context.get('veins', [])
        
        if domains:
            enriched_prompt += f"[POLER Domain: {', '.join(d['domain'] for d in domains)}]\n"
        if veins:
            top_keywords = [v['keyword'] for v in veins[:5]]
            enriched_prompt += f"[POLER Keywords: {', '.join(top_keywords)}]\n"
            enriched_prompt += f"[POLER Peak ε: {veins[0].get('epsilon_peak', 0):.0f}]\n"
    
    # В реальной системе здесь вызов LLM
    # Сейчас — заглушка, возвращаем enriched контекст
    result_data = {
        'route': 'ai',
        'domain': task.domain,
        'enriched_prompt_preview': enriched_prompt[:500],
        'poler_context_injected': bool(poler_context),
        'note': 'LLM вызов с обогащённым контекстом от POLER Core',
    }
    
    duration = (time.time() - start) * 1000
    
    return TaskResult(
        task_id=task.task_id,
        route='ai',
        success=True,
        data=result_data,
        duration_ms=duration,
        poler_context=decision.poler_insights,
    )

def execute_cli(task: Task, decision: RouteDecision) -> TaskResult:
    """Выполнение через CLI команду."""
    start = time.time()
    
    result_data = {
        'route': 'cli',
        'domain': task.domain,
        'note': 'CLI выполнение (заглушка)',
    }
    
    duration = (time.time() - start) * 1000
    
    return TaskResult(
        task_id=task.task_id,
        route='cli',
        success=True,
        data=result_data,
        duration_ms=duration,
        poler_context=decision.poler_insights,
    )

def execute_human(task: Task, decision: RouteDecision) -> TaskResult:
    """Передача человеку."""
    return TaskResult(
        task_id=task.task_id,
        route='human',
        success=False,
        data={
            'route': 'human',
            'reason': decision.reason,
            'domain': task.domain,
            'confidence': task.confidence,
        },
        poler_context=decision.poler_insights,
    )

# ═══════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ЦИКЛ МАРШРУТИЗАЦИИ
# ═══════════════════════════════════════════════════════════════════════

EXECUTORS = {
    'local': execute_local,
    'ai': execute_ai,
    'cli': execute_cli,
    'human': execute_human,
}

def route_task(task: Task) -> TaskResult:
    """
    ГЛАВНЫЙ ВХОД: Маршрутизация задачи через POLER Core.
    
    Цикл:
      Задача → POLER Core → decide_route → execute → Результат
    
    ЛЮБАЯ задача проходит через POLER Core ПЕРЕД любым другим действием.
    """
    # ЭТАП 1: POLER Core — ОБЯЗАТЕЛЬНЫЙ
    task = process_through_poler_core(task)
    
    # ЭТАП 2: Решение о маршруте
    decision = decide_route(task)
    
    # ЭТАП 3: Выполнение
    executor = EXECUTORS.get(decision.route, execute_human)
    result = executor(task, decision)
    
    return result

# ═══════════════════════════════════════════════════════════════════════
# ПУБЛИЧНЫЙ API
# ═══════════════════════════════════════════════════════════════════════

def process(
    text: str = "",
    files: Optional[List[str]] = None,
    skill: str = "",
    task_id: str = "",
) -> TaskResult:
    """
    Единый вход для обработки ЛЮБОЙ задачи.
    
    Автоматически:
    1. Пропускает через POLER Core (понимание)
    2. Выбирает маршрут (Local → AI → CLI → Human)
    3. Выполняет и возвращает результат
    
    Args:
        text: Входной текст
        files: Список файлов для анализа
        skill: Имя навыка (если известно)
        task_id: ID задачи
    
    Returns:
        TaskResult с результатом
    """
    if not task_id:
        task_id = f"task_{int(time.time() * 1000)}"
    
    task = Task(
        task_id=task_id,
        input_text=text,
        input_files=files or [],
        skill_name=skill,
    )
    
    return route_task(task)

def analyze_document(
    filepath: str,
    custom_seeds: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    """
    Быстрый анализ документа через POLER Core.
    
    Возвращает: домены, вены, карту навигации.
    """
    core = get_poler_core()
    p = Path(filepath)
    
    if not p.exists():
        return {'error': f'Файл не найден: {filepath}'}
    
    text = ""
    try:
        text = p.read_text(encoding='utf-8', errors='ignore')
    except:
        return {'error': f'Не удалось прочитать: {filepath}'}
    
    return core['build_veins'](text, custom_seeds=custom_seeds, source_file=filepath)

def quick_understand(text: str) -> Dict:
    """
    Быстрое понимание текста — только домены и ключевые слова.
    Без полного анализа вен (быстрее).
    """
    core = get_poler_core()
    return core['auto_discover_themes'](text)

# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        prog='super_z_core',
        description='Super Z Core — Нервная Система (маршрутизация через POLER Core)',
    )
    parser.add_argument('input', nargs='?', help='Файл или текст')
    parser.add_argument('--stdin', action='store_true', help='Читать из stdin')
    parser.add_argument('--skill', default='', help='Имя навыка')
    parser.add_argument('--task-id', default='', help='ID задачи')
    parser.add_argument('--analyze', action='store_true',
                        help='Только анализ через POLER Core (без маршрутизации)')
    parser.add_argument('--understand', action='store_true',
                        help='Быстрое понимание (только домены + ключевые слова)')
    parser.add_argument('-f', '--format', choices=['ascii', 'json'], default='ascii')
    
    args = parser.parse_args()
    
    # Собираем текст
    text = args.input or ""
    if args.stdin:
        text = sys.stdin.read()
    elif args.input and Path(args.input).exists():
        text = Path(args.input).read_text(encoding='utf-8', errors='ignore')
    
    if not text.strip():
        parser.print_help()
        return
    
    # Режим анализа
    if args.analyze:
        result = analyze_document(args.input) if args.input and Path(args.input).exists() else \
                 get_poler_core()['build_veins'](text, source_file='<stdin>')
        if args.format == 'json':
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_analysis(result)
        return
    
    # Режим быстрого понимания
    if args.understand:
        result = quick_understand(text)
        if args.format == 'json':
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_understand(result)
        return
    
    # Полная маршрутизация
    task_result = process(
        text=text,
        skill=args.skill,
        task_id=args.task_id,
    )
    
    if args.format == 'json':
        out = {
            'task_id': task_result.task_id,
            'route': task_result.route,
            'success': task_result.success,
            'duration_ms': task_result.duration_ms,
            'data': task_result.data,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Task: {task_result.task_id}")
        print(f"Route: {task_result.route}")
        print(f"Success: {task_result.success}")
        print(f"Duration: {task_result.duration_ms:.1f}ms")
        if task_result.data:
            for k, v in task_result.data.items():
                if k not in ('veins', 'themes', 'navigation_map'):
                    print(f"  {k}: {v}")

def _print_analysis(result: Dict):
    """ASCII вывод анализа."""
    print("POLER[Ψ] v3.0 — Анализ документа")
    print("=" * 50)
    
    domains = result.get('domains', [])
    if domains:
        print(f"\nДомены ({len(domains)}):")
        for d in domains:
            print(f"  {d['domain']:20s} confidence={d['confidence']:.2f}  hits={d['hits']}")
    
    veins = result.get('veins', [])
    if veins:
        print(f"\nВены ({len(veins)}):")
        for i, v in enumerate(veins[:10], 1):
            print(f"  {i}. [{v['domain']:15s}] {v['keyword']:20s}  "
                  f"ε={v['epsilon_peak']:,.0f}  R={v['resonance_integral']:,.0f}")

def _print_understand(result: Dict):
    """ASCII вывод понимания."""
    print("POLER[Ψ] — Быстрое понимание")
    print("=" * 50)
    
    domains = result.get('domains', [])
    if domains:
        print(f"\nДомены:")
        for d in domains:
            markers = ', '.join(d['matched'][:3])
            print(f"  {d['domain']:20s} ({d['confidence']:.2f}): {markers}")
    
    keywords = result.get('keywords', [])
    if keywords:
        print(f"\nКлючевые слова:")
        for kw in keywords[:10]:
            print(f"  {kw['word']:20s} freq={kw['freq']}  score={kw['score']:.2f}")

if __name__ == '__main__':
    main()
