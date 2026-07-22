#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Z Bridge
==============
Мост между нативными инструментами AI и ядром SuperZCore.
Позволяет AI (Claude/GPT/GLM) вызывать навыки напрямую через свои инструменты,
без внешнего CLI.
"""

import os
import sys
import json
from typing import Optional, Callable, Dict, Any

# Импорт ядра
from super_z_core import SuperZCore, create_core, SkillResult
from super_z_config import Config, EnvMode

class SuperZBridge:
    """
    Мост для интеграции с AI-платформами.
    
    Использование внутри AI:
    1. AI получает задачу от пользователя
    2. AI вызывает bridge.run_skill(...) вместо внешнего CLI
    3. Bridge маршрутизирует задачу: Local → LLM Callback → CLI
    """
    
    def __init__(self, llm_callback: Optional[Callable] = None):
        """
        Инициализация моста.
        
        :param llm_callback: Функция обратного вызова для LLM.
                             Сигнатура: callback(prompt: str) -> str
                             Если None, используется встроенный механизм AI.
        """
        self.llm_callback = llm_callback
        self.core = create_core(llm_callback=self._default_llm_callback if not llm_callback else llm_callback)
        
        # Статистика
        self.usage_stats = {
            "skills_called": [],
            "total_savings": 0.0
        }
    
    def _default_llm_callback(self, prompt: str) -> str:
        """
        Заглушка для LLM callback.
        В реальной среде AI должен передать свою функцию выполнения.
        """
        # Эмуляция: возвращаем промпт назад (для тестов)
        return f"[LLM Response to: {prompt[:100]}...]"
    
    def run_skill(self, skill_name: str, **params) -> Dict[str, Any]:
        """
        Выполняет навык через оптимальный бэкенд.
        
        Примеры использования:
        - bridge.run_skill("poler-analysis", text="...", keyword="сфер")
        - bridge.run_skill("blog-writer", topic="AI future", length=1000)
        - bridge.run_skill("web-search", query="news")
        
        Возвращает dict с результатом и метаданными.
        """
        result: SkillResult = self.core.run_skill(skill_name, params)
        
        # Сохраняем статистику
        self.usage_stats["skills_called"].append({
            "skill": skill_name,
            "backend": result.backend_used,
            "cost": result.cost,
            "success": result.success
        })
        self.usage_stats["total_savings"] += self.core.stats.get("money_saved", 0.0)
        
        # Формируем ответ
        response = {
            "success": result.success,
            "output": result.output,
            "backend_used": result.backend_used,
            "cost": result.cost,
            "latency_ms": result.latency_ms,
            "routing_info": self.core.get_cost_summary()
        }
        
        if result.error:
            response["error"] = result.error
        
        return response
    
    def analyze_text_with_poler(self, text: str, keyword: str, **options) -> Dict:
        """
        Специализированный метод для Poler-анализа.
        Автоматически выбирает локальное выполнение.
        """
        return self.run_skill(
            "poler-analysis",
            text=text,
            keyword=keyword,
            window=options.get('window', 5000),
            phi=options.get('phi', 0.85),
            kappa=options.get('kappa', 1.0),
            top=options.get('top', 10)
        )
    
    def get_environment_info(self) -> Dict:
        """Возвращает информацию о текущей среде."""
        return {
            "mode": Config.MODE.value,
            "workspace": str(Config.WORKSPACE),
            "poler_available": True,  # Проверяется в core
            "cli_enabled": Config.CLI_ENABLED,
            "llm_callback_active": self.llm_callback is not None,
            "force_local": Config.FORCE_LOCAL
        }
    
    def get_savings_report(self) -> Dict:
        """Отчет об экономии."""
        summary = self.core.get_cost_summary()
        summary["total_savings_user"] = f"${self.usage_stats['total_savings']:.4f}"
        summary["skills_processed"] = len(self.usage_stats["skills_called"])
        return summary

# Глобальный экземпляр (Singleton pattern)
_bridge_instance: Optional[SuperZBridge] = None

def get_bridge(llm_callback: Optional[Callable] = None) -> SuperZBridge:
    """Получить глобальный экземпляр моста."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = SuperZBridge(llm_callback=llm_callback)
    elif llm_callback and _bridge_instance.llm_callback != llm_callback:
        _bridge_instance.llm_callback = llm_callback
    return _bridge_instance

# CLI интерфейс для тестирования
if __name__ == "__main__":
    print("🔹 Super Z Bridge — Тестовый режим")
    print("=" * 50)
    
    # Создаем мост
    bridge = get_bridge()
    
    # Инфо о среде
    env = bridge.get_environment_info()
    print(f"Режим: {env['mode']}")
    print(f"Poler доступен: {env['poler_available']}")
    print(f"CLI включен: {env['cli_enabled']}")
    print()
    
    # Тест Poler
    test_text = "Сфера Мнемар сияла в центре Архисферы. Драконья матрица пульсировала."
    print(f"Тест Poler на тексте: '{test_text}'")
    result = bridge.analyze_text_with_poler(test_text, keyword="сфер", top=3)
    
    if result["success"]:
        print(f"✅ Успешно! Бэкенд: {result['backend_used']}")
        print(f"Стоимость: ${result['cost']}")
        if isinstance(result["output"], dict) and "summary" in result["output"]:
            summary = result["output"]["summary"]
            print(f"Найдено окон: {summary.get('total_windows', 0)}")
            print(f"Σ ε: {summary.get('total_epsilon', 0):.2f}")
    else:
        print(f"❌ Ошибка: {result.get('error', 'Неизвестная')}")
    
    print()
    
    # Отчет об экономии
    savings = bridge.get_savings_report()
    print("📊 Отчет об экономии:")
    print(f"  Всего запусков: {savings['total_runs']}")
    print(f"  Локальных: {savings['local_runs']}")
    print(f"  Через LLM: {savings['llm_runs']}")
    print(f"  Через CLI: {savings['cli_runs']}")
    print(f"  сэкономлено: {savings['money_saved']} ({savings['savings_percent']})")
