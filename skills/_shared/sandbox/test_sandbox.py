"""
test_sandbox.py — Tests for the internal agent sandbox backend.

Tests cover:
1. Agent creation and message passing
2. Full planner→executor→reviewer→critic chain
3. SandboxBackend.chat() API
4. Integration with llm_wrapper.py backend selection
5. Edge cases (empty queries, missing context, etc.)
6. Critic iteration logic (accept/reject/iterate)
"""
from __future__ import annotations

import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add sandbox to path
SANDBOX_DIR = Path(__file__).resolve().parent
if str(SANDBOX_DIR) not in sys.path:
    sys.path.insert(0, str(SANDBOX_DIR))

from agents import (
    Agent, PlannerAgent, ExecutorAgent, ReviewerAgent, CriticAgent,
    AgentMessage, AgentRole,
)
from backend import SandboxBackend


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
def backend(skill_context):
    """SandboxBackend instance with test context."""
    return SandboxBackend(skill_context=skill_context, verbose=False)


# ─── Agent Tests ────────────────────────────────────────────────────────

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


class TestPlannerAgent:
    def test_creates_plan(self, skill_context):
        planner = PlannerAgent(skill_context)
        msg = AgentMessage(payload={"query": "напиши пост про ИИ"})
        result = planner.process(msg)
        assert result.action == "plan_created"
        assert "subtasks" in result.payload
        assert len(result.payload["subtasks"]) >= 2

    def test_extracts_methodology(self, skill_context):
        planner = PlannerAgent(skill_context)
        hints = planner._extract_methodology(skill_context["skill_md"])
        assert len(hints) > 0
        assert any("Research" in h for h in hints)

    def test_infers_format(self, skill_context):
        planner = PlannerAgent(skill_context)
        assert planner._infer_format("blog-writer", "") == "document"
        assert planner._infer_format("poler-toolkit", "") == "analysis"
        assert planner._infer_format("charts", "") == "visual"

    def test_empty_skill_md(self):
        planner = PlannerAgent({"skill_name": "test", "skill_md": ""})
        msg = AgentMessage(payload={"query": "test query"})
        result = planner.process(msg)
        assert result.confidence > 0  # should still produce a plan


class TestExecutorAgent:
    def test_generates_content(self, skill_context):
        planner = PlannerAgent(skill_context)
        plan_msg = AgentMessage(payload={"query": "напиши пост"})
        plan_result = planner.process(plan_msg)

        executor = ExecutorAgent(skill_context)
        exec_result = executor.process(plan_result)
        assert exec_result.action == "content_generated"
        assert "final_content" in exec_result.payload
        assert len(exec_result.payload["final_content"]) > 0

    def test_subtask_execution(self, skill_context):
        executor = ExecutorAgent(skill_context)
        task = {"step": 1, "type": "understand", "description": "test"}
        result = executor._execute_subtask(task, "test query", [])
        assert "content" in result
        assert result["word_count"] >= 0


class TestReviewerAgent:
    def test_review_pass(self, skill_context):
        reviewer = ReviewerAgent(skill_context)
        msg = AgentMessage(payload={
            "query": "напиши пост про ИИ в медицине",
            "final_content": "Искусственный интеллект в медицине — это "
                             "важная тема. Данный пост рассматривает "
                             "применение ИИ для диагностики, лечения "
                             "и исследований.",
            "subtask_results": [
                {"step": 1, "type": "understand", "content": "test"},
                {"step": 2, "type": "generate", "content": "main content"},
            ],
            "output_format": "text",
            "skill_name": "blog-writer",
        })
        result = reviewer.process(msg)
        assert result.action == "review_completed"
        assert "verdict" in result.payload
        assert "checks" in result.payload

    def test_review_empty_content(self, skill_context):
        reviewer = ReviewerAgent(skill_context)
        msg = AgentMessage(payload={
            "query": "test",
            "final_content": "",
            "subtask_results": [],
            "output_format": "text",
            "skill_name": "test",
        })
        result = reviewer.process(msg)
        assert result.payload["average_score"] < 0.5


class TestCriticAgent:
    def test_accept_good_content(self, skill_context):
        critic = CriticAgent(skill_context)
        msg = AgentMessage(payload={
            "verdict": "pass",
            "average_score": 0.85,
            "issues": [],
            "suggestions": [],
            "query": "test",
        })
        result = critic.process(msg)
        assert result.payload["decision"] == "accept"

    def test_iterate_mediocre_content(self, skill_context):
        critic = CriticAgent(skill_context)
        msg = AgentMessage(payload={
            "verdict": "needs_improvement",
            "average_score": 0.45,
            "issues": ["substance: too short"],
            "suggestions": ["Add more details"],
            "query": "test",
        })
        result = critic.process(msg)
        assert result.payload["decision"] == "iterate"
        assert result.payload["next_agent"] == "executor"

    def test_reject_bad_content(self, skill_context):
        critic = CriticAgent(skill_context)
        msg = AgentMessage(payload={
            "verdict": "needs_improvement",
            "average_score": 0.15,
            "issues": ["Empty content"],
            "suggestions": ["Start over"],
            "query": "test",
        })
        result = critic.process(msg)
        assert result.payload["decision"] == "reject"

    def test_max_iterations(self, skill_context):
        critic = CriticAgent(skill_context)
        # Simulate 3 iterations
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

    def test_max_rounds(self, skill_context):
        backend = SandboxBackend(skill_context=skill_context, max_rounds=1)
        messages = [{"role": "user", "content": "test"}]
        result = backend.chat(messages)
        assert result is not None  # should complete even with 1 round

    def test_multiple_queries(self, backend):
        for query in ["query 1", "query 2", "query 3"]:
            result = backend.chat([{"role": "user", "content": query}])
            assert result is not None
        stats = backend.stats()
        assert stats["total_queries"] == 3


# ─── Backend Detection Tests ────────────────────────────────────────────

class TestBackendDetection:
    def test_detect_sandbox_env(self):
        """Test SUPER_Z_BACKEND=sandbox detection."""
        # We can't modify os.environ in tests easily, so test the logic directly
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
        """Test that sandbox backend produces a valid Pattern 1 brief."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from llm_wrapper import run_skill

        result = run_skill(
            skill_name="blog-writer",
            user_query="напиши пост про ИИ",
            json_output=False,  # don't print to stdout
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

        # Check that trace has round entries
        round_entries = [t for t in backend.trace if t.get("round")]
        assert len(round_entries) >= 1

    def test_trace_has_agent_data(self, backend):
        messages = [{"role": "user", "content": "test trace detail"}]
        backend.chat(messages)

        # Find first round trace
        rounds = [t for t in backend.trace if t.get("round")]
        if rounds:
            first = rounds[0]
            assert "plan_confidence" in first
            assert "exec_confidence" in first
            assert "review_verdict" in first
            assert "critic_decision" in first


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
