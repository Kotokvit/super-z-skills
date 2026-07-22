"""
sandbox — LLM-powered Agent Sandbox for Super-Z.

v3 (LOCAL-FIRST, DEFAULT): Super Z Core + Observer + POLER
    Super Z Core — Local-First routing (free → cheap → paid)
    Observer   — бинарные решения (нужна ли генерация? хватит ли? ~200 токенов/вызов)
    POLER      — динамическая обработка текста (без чтения всего текста, через резонанс)
    SandboxV2  — генерация контента (1 вызов LLM, не 4)

    Routing priority:
        1. LOCAL (Python/Bash) → FREE, instant
        2. AI callback (host LLM) → FREE, in-process
        3. External CLI (z-ai) → PAID, last resort

    Supported modules (v2 architecture):
        super_z_config.py      → Environment auto-detection & routing
        super_z_bridge.py      → AI native tool adapter (Bash/Read/Write)
        super_z_llm_callback.py → Free LLM reasoning provider
        super_z_core.py        → Main Local-First routing engine

v2: Observer + POLER + Sandbox (token-efficient)
    Observer decides + 1 LLM call for content = 1-3 calls, ~500-1500 tokens, 3-15s

v1 (LEGACY): 4-agent chain
    PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent
    4 полных LLM-генерации, ~5000 токенов, 25-80s — ПЕРЕБОР

Usage (v3 — local-first, DEFAULT):
    from sandbox import SuperZCore

    # With AI callback (FREE)
    core = SuperZCore(llm_callback=my_llm_function)
    result = core.run_skill("blog-writer", "Write about AI", skill_md)

    # Without callback (falls back to CLI, PAID)
    core = SuperZCore()
    result = core.run_skill("blog-writer", "Write about AI", skill_md)

Usage (v2 — Observer + POLER):
    from sandbox import SandboxV2
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider(callback=my_llm_function)  # FREE
    backend = SandboxV2(llm_provider=provider, use_observer=True)
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}],
    )

Usage (v1 — legacy, kept for compatibility):
    from sandbox import SandboxBackend
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider(callback=my_llm_function)  # FREE
    backend = SandboxBackend(llm_provider=provider)
"""

# v3 — Local-First routing (DEFAULT)
try:
    from super_z_core import (
        SuperZCore,
        run_skill_local_first,
        get_backend_type_routing,
        detect_environment,
        EnvironmentInfo,
        EnvironmentType,
        classify_skill,
        SkillCategory,
        LocalExecutor,
        create_llm_provider,
    )
    _HAS_SUPER_Z_CORE = True
except ImportError:
    _HAS_SUPER_Z_CORE = False

# v3 modules — super_z_config (environment detection)
try:
    from super_z_config import (
        EnvironmentType as ConfigEnvironmentType,
        EnvironmentInfo as ConfigEnvironmentInfo,
        SkillCategory as ConfigSkillCategory,
        detect_environment as config_detect_environment,
        classify_skill as config_classify_skill,
        get_routing_decision,
        RoutingDecision,
        LOCAL_SKILLS as CONFIG_LOCAL_SKILLS,
        EXTERNAL_SKILLS as CONFIG_EXTERNAL_SKILLS,
    )
    _HAS_SUPER_Z_CONFIG = True
except ImportError:
    _HAS_SUPER_Z_CONFIG = False

# v3 modules — super_z_bridge (AI native tool adapter)
try:
    from super_z_bridge import (
        AIBridge,
        ToolProtocol,
        get_bridge,
        bridge_execute_poler,
        bridge_execute_skill,
    )
    _HAS_SUPER_Z_BRIDGE = True
except ImportError:
    _HAS_SUPER_Z_BRIDGE = False

# v3 modules — super_z_llm_callback (free LLM reasoning)
try:
    from super_z_llm_callback import (
        LLMCallbackProvider,
        get_callback_provider,
        execute_with_callback,
        execute_poler,
        create_llm_callback_for_core,
    )
    _HAS_SUPER_Z_LLM_CALLBACK = True
except ImportError:
    _HAS_SUPER_Z_LLM_CALLBACK = False

# v2 — Observer + POLER (token-efficient)
try:
    from sandbox_v2 import SandboxV2, Observer, PolerContextExtractor
except ImportError:
    _HAS_SANDBOX_V2 = False
else:
    _HAS_SANDBOX_V2 = True

# v1 — Legacy 4-agent chain (kept for backward compatibility)
try:
    from backend import SandboxBackend
    from agents import (
        Agent,
        PlannerAgent,
        ExecutorAgent,
        ReviewerAgent,
        CriticAgent,
        AgentMessage,
        AgentRole,
    )
    _HAS_V1_AGENTS = True
except ImportError:
    _HAS_V1_AGENTS = False

# Shared components
try:
    from llm_provider import (
        LLMProvider,
        HostLLMProvider,
        MockLLMProvider,
    )
    _HAS_LLM_PROVIDER = True
except ImportError:
    _HAS_LLM_PROVIDER = False

__all__ = [
    # v3 (local-first, default)
    "SuperZCore",
    "run_skill_local_first",
    "get_backend_type_routing",
    "detect_environment",
    "EnvironmentInfo",
    "EnvironmentType",
    "classify_skill",
    "SkillCategory",
    "LocalExecutor",
    "create_llm_provider",
    # v3 modules — super_z_config
    "ConfigEnvironmentType",
    "ConfigEnvironmentInfo",
    "ConfigSkillCategory",
    "config_detect_environment",
    "config_classify_skill",
    "get_routing_decision",
    "RoutingDecision",
    "CONFIG_LOCAL_SKILLS",
    "CONFIG_EXTERNAL_SKILLS",
    # v3 modules — super_z_bridge
    "AIBridge",
    "ToolProtocol",
    "get_bridge",
    "bridge_execute_poler",
    "bridge_execute_skill",
    # v3 modules — super_z_llm_callback
    "LLMCallbackProvider",
    "get_callback_provider",
    "execute_with_callback",
    "execute_poler",
    "create_llm_callback_for_core",
    # v2 (observer+POLER)
    "SandboxV2",
    "Observer",
    "PolerContextExtractor",
    # v1 (legacy)
    "SandboxBackend",
    "Agent",
    "PlannerAgent",
    "ExecutorAgent",
    "ReviewerAgent",
    "CriticAgent",
    "AgentMessage",
    "AgentRole",
    # Shared
    "LLMProvider",
    "HostLLMProvider",
    "MockLLMProvider",
]
