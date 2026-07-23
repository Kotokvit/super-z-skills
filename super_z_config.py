#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Z Configuration & Environment Detection
==============================================
Определяет среду выполнения и настраивает маршрутизацию.
Приоритет: Local (Бесплатно) → LLM Callback (Встроенный AI) → External CLI (Платно)
"""

import os
import sys
from enum import Enum
from typing import Optional, Dict, Any
from pathlib import Path

class EnvMode(Enum):
    LLM_NATIVE = "llm_native"       # Работа внутри AI (Claude/GPT/GLM) через инструменты
    LOCAL_CLI = "local_cli"         # Локальный запуск с собственным CLI
    STANDALONE = "standalone"       # Изолированный режим без AI и CLI

class Config:
    """Глобальная конфигурация Super Z."""
    
    # Режим работы (автоопределяется или задается вручную)
    MODE: EnvMode = EnvMode.LOCAL_CLI
    
    # Пути
    WORKSPACE: Path = Path(os.getenv("SUPER_Z_WORKSPACE", "/workspace"))
    CORE_DIR: Path = Path(__file__).parent
    
    # Настройки LLM Callback
    LLM_CALLBACK_ENABLED: bool = True
    LLM_PROVIDER: str = os.getenv("SUPER_Z_LLM_PROVIDER", "native")  # native, openai, anthropic
    
    # Настройки CLI (резервный вариант)
    CLI_PATH: str = os.getenv("SUPER_Z_CLI_PATH", "z-ai")  # Путь к внешнему CLI
    CLI_ENABLED: bool = os.getenv("SUPER_Z_CLI_ENABLED", "false").lower() == "true"
    
    # Экономия
    FORCE_LOCAL: bool = os.getenv("SUPER_Z_FORCE_LOCAL", "true").lower() == "true"
    
    @classmethod
    def init(cls):
        """Инициализация конфигурации."""
        cls.MODE = detect_environment()
        if cls.MODE == EnvMode.LLM_NATIVE:
            cls.CLI_ENABLED = False
            cls.FORCE_LOCAL = True
        return cls

def detect_environment() -> EnvMode:
    """
    Автоопределение среды выполнения.
    Алгоритм:
    1. Если есть переменная SUPER_Z_LLM_NATIVE=true → LLM_NATIVE
    2. Если запущено внутри AI-песочницы (проверка инструментов) → LLM_NATIVE
    3. Если есть внешний CLI → LOCAL_CLI
    4. Иначе → STANDALONE
    """
    # 1. Явное указание
    if os.getenv("SUPER_Z_LLM_NATIVE", "").lower() == "true":
        return EnvMode.LLM_NATIVE
    
    # 2. Проверка на наличие AI-инструментов (эмуляция)
    # В реальной среде AI здесь будет проверка доступности tools
    if _check_ai_tools_available():
        return EnvMode.LLM_NATIVE
    
    # 3. Проверка наличия CLI
    cli_cmd = os.getenv("SUPER_Z_CLI_PATH", "z-ai")
    if _check_cli_exists(cli_cmd):
        return EnvMode.LOCAL_CLI
    
    # 4. По умолчанию - автономный режим (только локальные скрипты)
    return EnvMode.STANDALONE

def _check_ai_tools_available() -> bool:
    """Проверяет, доступны ли нативные инструменты AI."""
    # Эвристика: если мы в процессе выполнения задачи AI, эти флаги могут быть установлены
    # В реальной интеграции это проверяет наличие объектов инструментов
    has_bash = os.getenv("AI_HAS_BASH_TOOL", "false").lower() == "true"
    has_read = os.getenv("AI_HAS_READ_TOOL", "false").lower() == "true"
    return has_bash and has_read

def _check_cli_exists(cmd: str) -> bool:
    """Проверяет существование внешнего CLI."""
    import shutil
    return shutil.which(cmd) is not None

def get_cost_estimate(skill_type: str) -> Dict[str, Any]:
    """Оценка стоимости выполнения навыка."""
    costs = {
        "LOCAL": {"cost": 0.0, "currency": "USD", "latency_ms": 50},
        "AI_REASONING": {"cost": 0.002, "currency": "USD", "latency_ms": 2000}, # ~$2/1M tokens
        "EXTERNAL_API": {"cost": 0.05, "currency": "USD", "latency_ms": 5000}, # Примерная цена API
    }
    return costs.get(skill_type, {"cost": 0.01, "currency": "USD", "latency_ms": 1000})

# Auto-init on import
Config.init()
