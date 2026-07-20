"""
test_sandbox.py — Tests for the LLM-powered sandbox backend.

Tests cover:
1. LLM Provider abstraction (HostLLMProvider, MockLLMProvider)
2. Agent creation and LLM-powered reasoning
3. Full planner→executor→reviewer→critic chain with mock LLM
4. SandboxBackend.chat() API
5. Integration with llm_wrapper.py backend selection
6. Edge cases (empty queries, missing context, LLM failures)
7. Critic iteration logic (accept/reject/iterate)
8. Agent role-specific prompts
"""
from __future__ import annotations

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add sandbox to path
SANDBOX_DIR = Path(__file__).resolve().parent
if str(SANDBOX_DIR) not in sys.path:
    sys.path.insert(0, str(SANDBOX_DIR))

from agents import (
    Agent, PlannerAgent, ExecutorAgent, ReviewerAgent, CriticAgent,
    AgentMessage, AgentRole,
    PLANNER_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT, CRITIC_SYSTEM_PROMPT,
)
from backend import SandboxBackend
from llm_provider import (
    LLMProvider, HostLLMProvider, MockLLMProvider,
)


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def skill_context():
    """Sample skill context for testing."""
    return {
        "skill_name": "blog-writer",
        "skill_md": """# Blog Writer

## Methodology
- Research the topic thoroughly
- Create an outline with 3-5 sections
- Write engaging introduction
- Add specific examples
- Conclude with actionable takeaways

## Output Format
- Title
- Introduction
- Body (3-5 sections)
- Conclusion
""",
    }


@pytest.fixture
def mock_provider():
    """MockLLMProvider with default responses."""
    return MockLLMProvider()


@pytest.fixture
def backend(mock_provider, skill_context):
    """SandboxBackend with mock LLM provider."""
    return SandboxBackend(
        llm_provider=mock_provider,
        skill_context=skill_context,
        verbose=False,
    )


# ─── LLM Provider Tests ────────────────────────────────────────────────

class TestMockLLMProvider:
    def test_returns_response(self):
        provider = MockLLMProvider()
        result = provider.chat("system", "user prompt")
        assert result is not None
        assert len(result) > 0

    def test_tracks_call_count(self):
        provider = MockLLMProvider()
        assert provider.call_count == 0
        provider.chat("system", "prompt 1")
        assert provider.call_count == 1
        provider.chat("system", "prompt 2")
        assert provider.call_count == 2

    def test_logs_calls(self):
        provider = MockLLMProvider()
        provider.chat("planner system", "plan this")
        provider.chat("executor system", "execute this")
        assert len(provider.call_log) == 2
        assert "planner" in provider.call_log[0]["system_prompt"]

    def test_custom_responses(self):
        provider = MockLLMProvider(responses={
            "planner": '{"subtasks": [{"step": 1, "type": "custom"}]}',
            "executor": "Custom execution result",
        })
        result = provider.chat("You are a planner agent", "plan")
        assert "custom" in result.lower()

    def test_name(self):
        provider = MockLLMProvider()
        assert provider.name() == "mock"


class TestHostLLMProvider:
    def test_callback_provider(self):
        def my_callback(system_prompt, user_prompt):
            return f"Response to: {user_prompt[:20]}"

        provider = HostLLMProvider(callback=my_callback)
        result = provider.chat("system", "test query")
        assert result is not None
        assert "test query" in result

    def test_callback_error_returns_none(self):
        def bad_callback(system_prompt, user_prompt):
            raise RuntimeError("LLM failed")

        provider = HostLLMProvider(callback=bad_callback)
        result = provider.chat("system", "test")
        assert result is None

    def test_name_with_callback(self):
        provider = HostLLMProvider(callback=lambda s, u: "ok")
        assert provider.name() == "host_callback"

    def test_name_with_cli(self):
        provider = HostLLMProvider(cli_command="/usr/local/bin/z-ai")
        assert "z-ai" in provider.name()


# ─── Agent Message Tests ───────────────────────────────────────────────

class TestAgentMessage:
    def test_message_creation(self):
        msg = AgentMessage(role="user", action="test", payload={"key": "value"})
        assert msg.role == "user"
        assert msg.action == "test"
        assert msg.payload["key"] == "value"
        assert msg.id  # auto-generated
        assert msg.timestamp > 0

    def test_message_serialization(self):
        msg = AgentMessage(role="planner", action="plan", payload={"steps": 3})
        d = msg.to_dict()
        assert d["role"] == "planner"
        assert d["payload"]["steps"] == 3


# ─── Agent Role Prompt Tests ───────────────────────────────────────────

class TestRolePrompts:
    """Verify each agent has a distinct role-specific system prompt."""

    def test_planner_has_planner_prompt(self):
        assert "planner" in PLANNER_SYSTEM_PROMPT.lower()
        assert "subtask" in PLANNER_SYSTEM_PROMPT.lower() or "plan" in PLANNER_SYSTEM_PROMPT.lower()

    def test_executor_has_executor_prompt(self):
        assert "executor" in EXECUTOR_SYSTEM_PROMPT.lower()
        assert "content" in EXECUTOR_SYSTEM_PROMPT.lower()

    def test_reviewer_has_reviewer_prompt(self):
        assert "reviewer" in REVIEWER_SYSTEM_PROMPT.lower()
        assert "quality" in REVIEWER_SYSTEM_PROMPT.lower() or "evaluat" in REVIEWER_SYSTEM_PROMPT.lower()

    def test_critic_has_critic_prompt(self):
        assert "critic" in CRITIC_SYSTEM_PROMPT.lower()
        assert "accept" in CRITIC_SYSTEM_PROMPT.lower()

    def test_prompts_are_different(self):
        prompts = {PLANNER_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT,
                   REVIEWER_SYSTEM_PROMPT, CRITIC_SYSTEM_PROMPT}
        assert len(prompts) == 4  # all unique


# ─── Planner Agent Tests ──────────────────────────────────────────────

class TestPlannerAgent:
    def test_creates_plan_via_llm(self, mock_provider, skill_context):
        planner = PlannerAgent(mock_provider, skill_context)
        msg = AgentMessage(payload={"query": "напиши пост про ИИ"})
        result = planner.process(msg)
        assert result.action == "plan_created"
        assert "subtasks" in result.payload

    def test_llm_was_called(self, mock_provider, skill_context):
        planner = PlannerAgent(mock_provider, skill_context)
        msg = AgentMessage(payload={"query": "test query"})
        planner.process(msg)
        # Planner should have called the LLM
        assert mock_provider.call_count >= 1

    def test_fallback_on_llm_failure(self, skill_context):
        # Provider that always returns None
        failing_provider = MockLLMProvider(responses={"default": None})
        # Override chat to return None
        failing_provider.chat = lambda *a, **kw: None

        planner = PlannerAgent(failing_provider, skill_context)
        msg = AgentMessage(payload={"query": "test"})
        result = planner.process(msg)
        assert result.action == "plan_created"
        assert result.confidence < 0.5  # low confidence on fallback

    def test_empty_skill_md(self, mock_provider):
        planner = PlannerAgent(mock_provider, {"skill_name": "test", "skill_md": ""})
        msg = AgentMessage(payload={"query": "test query"})
        result = planner.process(msg)
        assert result.confidence > 0


# ─── Executor Agent Tests ──────────────────────────────────────────────

class TestExecutorAgent:
    def test_generates_content_via_llm(self, mock_provider, skill_context):
        planner = PlannerAgent(mock_provider, skill_context)
        plan_msg = AgentMessage(payload={"query": "напиши пост"})
        plan_result = planner.process(plan_msg)

        executor = ExecutorAgent(mock_provider, skill_context)
        exec_result = executor.process(plan_result)
        assert exec_result.action == "content_generated"
        assert "final_content" in exec_result.payload
        assert len(exec_result.payload["final_content"]) > 0

    def test_llm_was_called(self, mock_provider, skill_context):
        planner = PlannerAgent(mock_provider, skill_context)
        plan_result = planner.process(AgentMessage(payload={"query": "test"}))

        initial_calls = mock_provider.call_count
        executor = ExecutorAgent(mock_provider, skill_context)
        executor.process(plan_result)
        assert mock_provider.call_count > initial_calls


# ─── Reviewer Agent Tests ──────────────────────────────────────────────

class TestReviewerAgent:
    def test_review_via_llm(self, mock_provider, skill_context):
        reviewer = ReviewerAgent(mock_provider, skill_context)
        msg = AgentMessage(payload={
            "query": "напиши пост про ИИ",
            "final_content": "Искусственный интеллект — важная тема.",
            "skill_name": "blog-writer",
        })
        result = reviewer.process(msg)
        assert result.action == "review_completed"
        assert "verdict" in result.payload

    def test_fallback_review(self, skill_context):
        failing_provider = MockLLMProvider()
        failing_provider.chat = lambda *a, **kw: None

        reviewer = ReviewerAgent(failing_provider, skill_context)
        msg = AgentMessage(payload={
            "query": "test",
            "final_content": "Some content here that is more than fifty characters long to pass the check.",
            "skill_name": "test",
        })
        result = reviewer.process(msg)
        assert result.action == "review_completed"
        assert result.payload["verdict"] in ("pass", "needs_improvement")


# ─── Critic Agent Tests ────────────────────────────────────────────────

class TestCriticAgent:
    def test_accept_good_content(self, mock_provider, skill_context):
        critic = CriticAgent(mock_provider, skill_context)
        msg = AgentMessage(payload={
            "verdict": "pass",
            "average_score": 0.85,
            "issues": [],
            "suggestions": [],
            "query": "test",
        })
        result = critic.process(msg)
        assert result.payload["decision"] == "accept"

    def test_max_iterations_forces_accept(self, mock_provider, skill_context):
        critic = CriticAgent(mock_provider, skill_context)
        for _ in range(2):
            msg = AgentMessage(payload={
                "verdict": "needs_improvement",
                "average_score": 0.4,
                "issues": [],
                "suggestions": [],
                "query": "test",
            })
            critic.process(msg)

        # Third iteration should force accept
        msg = AgentMessage(payload={
            "verdict": "needs_improvement",
            "average_score": 0.4,
            "issues": [],
            "suggestions": [],
            "query": "test",
        })
        result = critic.process(msg)
        assert result.payload["decision"] == "accept"


# ─── Backend Integration Tests ──────────────────────────────────────────

class TestSandboxBackend:
    def test_chat_basic(self, backend):
        messages = [
            {"role": "user", "content": "напиши пост про ИИ"},
        ]
        result = backend.chat(messages)
        assert result is not None
        assert len(result) > 0

    def test_chat_with_system_prompt(self, backend):
        messages = [
            {"role": "system", "content": "# Test Skill\n\nDo something useful."},
            {"role": "user", "content": "сделай что-нибудь"},
        ]
        result = backend.chat(messages)
        assert result is not None

    def test_chat_empty_messages(self, backend):
        result = backend.chat([])
        assert result is None

    def test_backend_stats(self, backend):
        messages = [{"role": "user", "content": "test query"}]
        backend.chat(messages)
        stats = backend.stats()
        assert "total_queries" in stats
        assert stats["total_queries"] >= 1
        assert "llm_calls" in stats
        assert stats["llm_calls"] >= 4  # planner + executor + reviewer + critic

    def test_max_rounds(self, mock_provider, skill_context):
        backend = SandboxBackend(
            llm_provider=mock_provider,
            skill_context=skill_context,
            max_rounds=1,
        )
        messages = [{"role": "user", "content": "test"}]
        result = backend.chat(messages)
        assert result is not None

    def test_multiple_queries(self, backend):
        for query in ["query 1", "query 2", "query 3"]:
            result = backend.chat([{"role": "user", "content": query}])
            assert result is not None
        stats = backend.stats()
        assert stats["total_queries"] == 3

    def test_agents_use_same_provider(self, backend, mock_provider):
        """Verify all agents share the same LLM provider."""
        messages = [{"role": "user", "content": "test"}]
        backend.chat(messages)
        # Should have at least 4 LLM calls (one per agent in the chain)
        assert mock_provider.call_count >= 4


# ─── Backend Detection Tests ────────────────────────────────────────────

class TestBackendDetection:
    def test_detect_sandbox_env(self):
        from llm_wrapper import _detect_backend
        assert _detect_backend("sandbox") == "sandbox"
        assert _detect_backend("SANDBOX") == "sandbox"

    def test_detect_mock(self):
        from llm_wrapper import _detect_backend
        assert _detect_backend("mock") == "mock"

    def test_detect_default(self):
        from llm_wrapper import _detect_backend
        with patch.dict(os.environ, {}, clear=True):
            result = _detect_backend(None)
            assert result == "zai_cli"

    def test_detect_env_var(self):
        from llm_wrapper import _detect_backend
        with patch.dict(os.environ, {"SUPER_Z_BACKEND": "sandbox"}):
            result = _detect_backend(None)
            assert result == "sandbox"

        with patch.dict(os.environ, {"SUPER_Z_BACKEND": "mock"}):
            result = _detect_backend(None)
            assert result == "mock"


# ─── llm_wrapper Integration Tests ──────────────────────────────────────

class TestLLMWrapperIntegration:
    def test_sandbox_backend_produces_brief(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from llm_wrapper import run_skill

        result = run_skill(
            skill_name="blog-writer",
            user_query="напиши пост про ИИ",
            json_output=False,
            backend="sandbox",
        )

        assert result is not None
        assert result.get("status") == "success"
        assert "claims" in result
        assert result["metadata"]["backend"] == "sandbox"

    def test_mock_backend(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from llm_wrapper import run_skill

        result = run_skill(
            skill_name="blog-writer",
            user_query="test",
            json_output=False,
            backend="mock",
        )

        assert result is not None
        assert result.get("status") == "success"
        assert "MOCK RESPONSE" in result["claims"][0]["text"]

    def test_nonexistent_skill(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from llm_wrapper import run_skill

        result = run_skill(
            skill_name="nonexistent-skill-xyz",
            user_query="test",
            backend="sandbox",
        )
        assert result.get("status") == "error"


# ─── Agent Chain Trace Tests ────────────────────────────────────────────

class TestAgentChainTrace:
    def test_trace_records_rounds(self, backend):
        messages = [{"role": "user", "content": "test trace"}]
        backend.chat(messages)
        round_entries = [t for t in backend.trace if t.get("round")]
        assert len(round_entries) >= 1

    def test_trace_has_agent_data(self, backend):
        messages = [{"role": "user", "content": "test trace detail"}]
        backend.chat(messages)
        rounds = [t for t in backend.trace if t.get("round")]
        if rounds:
            first = rounds[0]
            assert "plan_confidence" in first
            assert "exec_confidence" in first
            assert "review_verdict" in first
            assert "critic_decision" in first

    def test_trace_records_llm_calls(self, backend, mock_provider):
        messages = [{"role": "user", "content": "test llm tracking"}]
        backend.chat(messages)
        stats = backend.stats()
        assert stats["llm_calls"] >= 4


# ─── Real LLM Behavior Simulation ───────────────────────────────────────

class TestRealLLMSimulation:
    """Test with a provider that simulates realistic LLM responses."""

    def test_full_chain_with_realistic_responses(self, skill_context):
        """Simulate what happens when a real LLM responds to each agent."""
        call_count = [0]

        def realistic_llm(system_prompt, user_prompt):
            call_count[0] += 1
            # Check role from the FIRST LINE of the prompt to avoid
            # false matches (e.g., executor prompt mentions "from the planner")
            sp_lower = system_prompt.lower()
            if sp_lower.startswith("you are a planner"):
                return json.dumps({
                    "subtasks": [
                        {"step": 1, "type": "understand",
                         "description": "Analyze the blog post request",
                         "output": "brief"},
                        {"step": 2, "type": "generate",
                         "description": "Write the blog post content",
                         "output": "content"},
                        {"step": 3, "type": "synthesize",
                         "description": "Combine into final output",
                         "output": "final"},
                    ],
                    "methodology_hints": ["Research topic", "Write engaging intro"],
                    "output_format": "document",
                    "estimated_steps": 3,
                }, ensure_ascii=False)
            elif sp_lower.startswith("you are an executor"):
                return ("# Искусственный интеллект в современной медицине\n\n"
                        "Искусственный интеллект стремительно трансформирует "
                        "здравоохранение. От диагностики до разработки лекарств — "
                        "ИИ уже помогает врачам принимать более точные решения.\n\n"
                        "## Диагностика\n\nГлубокое обучение позволяет анализировать "
                        "медицинские снимки с точностью, сопоставимой с опытными "
                        "радиологами. Алгоритмы обнаруживают патологии на ранних "
                        "стадиях, когда лечение наиболее эффективно.\n\n"
                        "## Разработка лекарств\n\nИИ сокращает время разработки "
                        "новых препаратов с 10-15 лет до 2-3 лет, моделируя "
                        "молекулярные взаимодействия и предсказывая эффективность "
                        "кандидатов.")
            elif sp_lower.startswith("you are a reviewer"):
                return json.dumps({
                    "verdict": "pass",
                    "scores": {
                        "relevance": 0.9,
                        "completeness": 0.8,
                        "quality": 0.85,
                        "structure": 0.9,
                        "substance": 0.8,
                    },
                    "average_score": 0.85,
                    "issues": [],
                    "suggestions": ["Could add more specific data points"],
                }, ensure_ascii=False)
            elif sp_lower.startswith("you are a critic"):
                return json.dumps({
                    "decision": "accept",
                    "reason": "Content quality meets threshold (0.85 >= 0.7)",
                    "quality_score": 0.85,
                    "focus_areas": [],
                }, ensure_ascii=False)
            return "Generic response"

        provider = HostLLMProvider(callback=realistic_llm)
        backend_obj = SandboxBackend(
            llm_provider=provider,
            skill_context=skill_context,
        )

        result = backend_obj.chat([
            {"role": "user", "content": "напиши пост про ИИ в медицине"}
        ])

        # Verify the chain ran correctly
        assert result is not None
        assert "Искусственный интеллект" in result
        assert "медицин" in result.lower()
        assert call_count[0] == 4  # planner + executor + reviewer + critic

        stats = backend_obj.stats()
        assert stats["llm_calls"] == 4
        assert stats["total_rounds"] == 1  # accepted on first round


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
