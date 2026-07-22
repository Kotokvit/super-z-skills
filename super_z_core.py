#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Z Core Engine
===================
Ядро маршрутизации задач.
Логика: Local (Бесплатно) → AI Callback (Встроенный разум) → External CLI (Платно)
"""

import os
import sys
import json
import subprocess
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

# Импорт конфигурации
from super_z_config import Config, EnvMode, detect_environment, get_cost_estimate

# Импорт Poler Enhanced как локального движка
try:
    import poler_enhanced as poler
    POLER_AVAILABLE = True
except ImportError:
    POLER_AVAILABLE = False

class SkillType(Enum):
    LOCAL = "LOCAL"               # Выполняется локальным кодом (Poler, скрипты)
    AI_REASONING = "AI_REASONING" # Требует рассуждений LLM (через callback)
    EXTERNAL_API = "EXTERNAL_API" # Требует внешнего API (Web Search, Image Gen)

@dataclass
class SkillResult:
    success: bool
    output: Any
    backend_used: str
    cost: float
    latency_ms: int
    error: Optional[str] = None

class LocalExecutor:
    """Выполняет задачи локально без LLM и CLI."""
    
    @staticmethod
    def run_poler_analysis(text: str, keyword: str, **kwargs) -> Dict:
        """Запускает анализ Poler локально."""
        if not POLER_AVAILABLE:
            raise RuntimeError("poler_enhanced.py не найден")
        
        analyzer = poler.PolerAnalyzer(
            window=kwargs.get('window', 5000),
            phi=kwargs.get('phi', 0.85),
            kappa=kwargs.get('kappa', 1.0),
            top=kwargs.get('top', 10)
        )
        
        # Если текст - это путь к файлу
        if Path(text).exists():
            return analyzer.analyze_file(text, keyword)
        else:
            return analyzer.analyze_text(text, keyword)
    
    @staticmethod
    def run_python_script(code: str, args: List[str] = None) -> str:
        """Выполняет Python код локально."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            f.flush()
            try:
                cmd = [sys.executable, f.name] + (args or [])
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                return result.stdout + result.stderr
            finally:
                os.unlink(f.name)

def classify_skill(skill_name: str, params: Dict) -> SkillType:
    """
    Классифицирует навык: LOCAL / AI_REASONING / EXTERNAL_API.
    Правила:
    - poler-*, analyze-, process- → LOCAL (если есть poler_enhanced)
    - write-, plan-, reason-, summarize- → AI_REASONING
    - web-search, image-gen, tts, asr → EXTERNAL_API
    """
    name_lower = skill_name.lower()
    
    # 1. Локальные навыки (обработка текста/данных)
    local_prefixes = ['poler', 'analyze', 'process', 'convert', 'format']
    if any(name_lower.startswith(p) for p in local_prefixes):
        if POLER_AVAILABLE or name_lower != 'poler':
            return SkillType.LOCAL
    
    # 2. Навыки, требующие рассуждений (LLM)
    reasoning_keywords = ['write', 'plan', 'reason', 'summarize', 'explain', 'translate', 'creative']
    if any(k in name_lower for k in reasoning_keywords):
        return SkillType.AI_REASONING
    
    # 3. Внешние API (требуют интернета/специфичных моделей)
    external_keywords = ['web-search', 'image-gen', 'tts', 'asr', 'speech', 'vision']
    if any(k in name_lower for k in external_keywords):
        return SkillType.EXTERNAL_API
    
    # По умолчанию - пробуем локально, если не выйдет - AI
    return SkillType.LOCAL

def get_backend_type_routing(skill_name: str, params: Dict) -> Dict[str, Any]:
    """
    Улучшенная маршрутизация с метаданными.
    Возвращает не просто тип, а полную информацию о бэкенде.
    """
    skill_type = classify_skill(skill_name, params)
    env_mode = Config.MODE
    
    # Определение доступных бэкендов
    available_backends = []
    recommended_backend = ""
    
    if skill_type == SkillType.LOCAL:
        available_backends = ["local_executor"]
        recommended_backend = "local_executor"
        if not POLER_AVAILABLE and 'poler' in skill_name.lower():
            recommended_backend = "error"  # Нет полерa
    
    elif skill_type == SkillType.AI_REASONING:
        if env_mode == EnvMode.LLM_NATIVE:
            available_backends = ["llm_callback_native"]
            recommended_backend = "llm_callback_native"
        elif Config.LLM_CALLBACK_ENABLED:
            available_backends = ["llm_callback_external", "cli_fallback"]
            recommended_backend = "llm_callback_external"
        else:
            available_backends = ["cli_fallback"]
            recommended_backend = "cli_fallback"
    
    elif skill_type == SkillType.EXTERNAL_API:
        if Config.CLI_ENABLED:
            available_backends = ["cli_external_api"]
            recommended_backend = "cli_external_api"
        else:
            available_backends = ["error_no_cli"]
            recommended_backend = "error_no_cli"
    
    cost_est = get_cost_estimate(skill_type.value)
    
    return {
        "skill_name": skill_name,
        "skill_type": skill_type.value,
        "environment": env_mode.value,
        "recommended_backend": recommended_backend,
        "available_backends": available_backends,
        "estimated_cost": cost_est["cost"],
        "estimated_latency_ms": cost_est["latency_ms"],
        "poler_available": POLER_AVAILABLE,
        "cli_enabled": Config.CLI_ENABLED,
        "llm_native": env_mode == EnvMode.LLM_NATIVE,
    }

class SuperZCore:
    """Основной движок выполнения навыков."""
    
    def __init__(self, llm_callback=None):
        self.llm_callback = llm_callback  # Функция для вызова LLM
        self.local_executor = LocalExecutor()
        self.stats = {
            "total_runs": 0,
            "local_runs": 0,
            "llm_runs": 0,
            "cli_runs": 0,
            "total_cost": 0.0,
            "money_saved": 0.0
        }
    
    def run_skill(self, skill_name: str, params: Dict) -> SkillResult:
        """Выполняет навык по оптимальному пути."""
        self.stats["total_runs"] += 1
        
        # 1. Маршрутизация
        routing = get_backend_type_routing(skill_name, params)
        backend = routing["recommended_backend"]
        
        # 2. Выполнение
        try:
            if backend == "local_executor":
                return self._run_local(skill_name, params)
            elif backend in ["llm_callback_native", "llm_callback_external"]:
                return self._run_llm_callback(skill_name, params)
            elif backend == "cli_fallback" or backend == "cli_external_api":
                return self._run_cli(skill_name, params)
            else:
                return SkillResult(
                    success=False,
                    output=None,
                    backend_used="none",
                    cost=0.0,
                    latency_ms=0,
                    error=f"No suitable backend: {backend}"
                )
        except Exception as e:
            return SkillResult(
                success=False,
                output=None,
                backend_used=backend,
                cost=0.0,
                latency_ms=0,
                error=str(e)
            )
    
    def _run_local(self, skill_name: str, params: Dict) -> SkillResult:
        """Локальное выполнение."""
        self.stats["local_runs"] += 1
        
        if 'poler' in skill_name.lower():
            text = params.pop('text', '')  # Извлекаем и удаляем из dict
            keyword = params.pop('keyword', 'сфер')
            # Передаем остальные параметры как kwargs
            result = self.local_executor.run_poler_analysis(text, keyword, **params)
            cost = 0.0
            # Экономия по сравнению с CLI
            self.stats["money_saved"] += 0.05  # Примерная цена CLI вызова
        else:
            # Другие локальные скрипты
            code = params.get('code', '')
            result = self.local_executor.run_python_script(code)
            cost = 0.0
        
        return SkillResult(
            success=True,
            output=result,
            backend_used="local_executor",
            cost=cost,
            latency_ms=50
        )
    
    def _run_llm_callback(self, skill_name: str, params: Dict) -> SkillResult:
        """Вызов через LLM Callback."""
        self.stats["llm_runs"] += 1
        
        if not self.llm_callback:
            raise RuntimeError("LLM callback not configured")
        
        # Формируем промпт для задачи
        prompt = f"Execute skill '{skill_name}' with params: {json.dumps(params)}"
        
        # Вызываем LLM
        response = self.llm_callback(prompt)
        
        cost = 0.002  # Примерная стоимость токенов
        self.stats["money_saved"] += 0.048  # Экономия vs CLI
        
        return SkillResult(
            success=True,
            output=response,
            backend_used="llm_callback",
            cost=cost,
            latency_ms=2000
        )
    
    def _run_cli(self, skill_name: str, params: Dict) -> SkillResult:
        """Вызов через внешний CLI (резерв)."""
        self.stats["cli_runs"] += 1
        
        if not Config.CLI_ENABLED:
            raise RuntimeError("CLI is disabled")
        
        cmd = [Config.CLI_PATH, "run", skill_name, json.dumps(params)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        cost = 0.05
        if result.returncode != 0:
            return SkillResult(
                success=False,
                output=None,
                backend_used="cli",
                cost=cost,
                latency_ms=5000,
                error=result.stderr
            )
        
        return SkillResult(
            success=True,
            output=result.stdout,
            backend_used="cli",
            cost=cost,
            latency_ms=5000
        )
    
    def get_cost_summary(self) -> Dict:
        """Сводка по затратам и экономии."""
        total_potential_cli_cost = self.stats["total_runs"] * 0.05
        actual_cost = self.stats["total_cost"]
        saved = total_potential_cli_cost - actual_cost
        
        return {
            "total_runs": self.stats["total_runs"],
            "local_runs": self.stats["local_runs"],
            "llm_runs": self.stats["llm_runs"],
            "cli_runs": self.stats["cli_runs"],
            "actual_cost": f"${actual_cost:.4f}",
            "potential_cli_cost": f"${total_potential_cli_cost:.4f}",
            "money_saved": f"${saved:.4f}",
            "savings_percent": f"{(saved / total_potential_cli_cost * 100) if total_potential_cli_cost > 0 else 0:.1f}%"
        }

# Factory для создания экземпляра
def create_core(llm_callback=None) -> SuperZCore:
    return SuperZCore(llm_callback=llm_callback)
