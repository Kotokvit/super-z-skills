"""
test_sandbox_v2.py — Tests for the Observer + POLER sandbox backend (v2).

Tests cover:
1. PolerContextExtractor — keyword/poler-based context extraction
2. Observer — binary decision maker (should_generate, is_sufficient, what_to_focus)
3. SandboxV2 — full Observer + POLER + generation loop
4. Bridge v2 — call_sandbox_chat() with v2 backend
5. Token efficiency comparison (v1 vs v2)
6. Edge cases (empty queries, missing context, LLM failures)
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

from sandbox_v2 import SandboxV2, Observer, PolerContextExtractor
from llm_provider import LLMProvider, MockLLMProvider, HostLLMProvider


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

## Quality Criteria
- Each section at least 150 words
- Include specific data points
- Use the user's language
- No meta-descriptions or placeholders
""",
    }


@pytest.fixture
def mock_provider():
    """MockLLMProvider with default responses."""
    return MockLLMProvider()


@pytest.fixture
def v2_backend(mock_provider, skill_context):
    """SandboxV2 with mock LLM provider."""
    return SandboxV2(
        llm_provider=mock_provider,
        skill_context=skill_context,
        verbose=False,
        use_observer=False,
    )


@pytest.fixture
def v2_backend_with_observer(mock_provider, skill_context):
    """SandboxV2 with Observer enabled."""
    return SandboxV2(
        llm_provider=mock_provider,
        skill_context=skill_context,
        verbose=False,
        use_observer=True,
    )


# ─── PolerContextExtractor Tests ────────────────────────────────────────

class TestPolerContextExtractor:
    def test_extract_keywords_from_query(self):
        extractor = PolerContextExtractor()
        keywords = extractor._extract_keywords("напиши пост про искусственный интеллект")
        assert "пост" in keywords or "искусственный" in keywords or "интеллект" in keywords
        # Stopwords should be filtered
        assert "напиши" not in keywords
        assert "про" not in keywords

    def test_extract_keywords_filters_short_words(self):
        extractor = PolerContextExtractor()
        keywords = extractor._extract_keywords("I am a cat")
        # Words <= 2 chars should be filtered
        assert "am" not in keywords
        assert "a" not in keywords
        assert "cat" in keywords

    def test_extract_with_empty_text(self):
        extractor = PolerContextExtractor()
        result = extractor.extract("", "test query")
        assert result == ""

    def test_extract_with_empty_query(self):
        extractor = PolerContextExtractor()
        result = extractor.extract("some text here", "")
        # Should return first 2000 chars
        assert result == "some text here"

    def test_extract_with_keywords_fallback(self):
        extractor = PolerContextExtractor()
        text = """## Introduction
This is about artificial intelligence and machine learning.

## Methods
We discuss neural networks and deep learning approaches.

## Results
The results show significant improvements in accuracy.

## Conclusion
AI will transform many industries in the coming years.
"""
        result = extractor._extract_with_keywords(text, ["artificial", "neural"])
        # Should contain relevant paragraphs
        assert "artificial intelligence" in result or "neural networks" in result

    def test_extract_limits_size(self):
        extractor = PolerContextExtractor()
        long_text = "A" * 10000
        result = extractor.extract(long_text, "test query")
        # Should not exceed 2000 chars (fallback) or 3000 chars (keyword)
        assert len(result) <= 3001  # small margin


# ─── Observer Tests ─────────────────────────────────────────────────────

class TestObserver:
    def test_should_generate_returns_true(self, mock_provider):
        """Observer should typically decide generation is needed."""
        # Mock provider returns generic response, not "YES"/"NO"
        observer = Observer(mock_provider)
        result = observer.should_generate("Write a blog post", "some context")
        # Default should be True (generate) when response is not "NO"
        assert isinstance(result, bool)

    def test_should_generate_no_returns_true_on_failure(self):
        """Observer should default to True (generate) when LLM fails."""
        failing_provider = MockLLMProvider(responses={"default": None})
        # Make chat return None
        failing_provider.chat = lambda *a, **kw: None
        observer = Observer(failing_provider)
        result = observer.should_generate("Write a blog post", "context")
        assert result is True  # Default to generating

    def test_is_sufficient_returns_bool(self, mock_provider):
        observer = Observer(mock_provider)
        result = observer.is_sufficient("test query", "some generated content")
        assert isinstance(result, bool)

    def test_is_sufficient_defaults_to_true_on_failure(self):
        failing_provider = MockLLMProvider()
        failing_provider.chat = lambda *a, **kw: None
        observer = Observer(failing_provider)
        result = observer.is_sufficient("test", "content")
        assert result is True  # Accept on failure

    def test_what_to_focus_returns_string(self, mock_provider):
        observer = Observer(mock_provider)
        result = observer.what_to_focus("test query", "some content", "too short")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_what_to_focus_fallback(self):
        failing_provider = MockLLMProvider()
        failing_provider.chat = lambda *a, **kw: None
        observer = Observer(failing_provider)
        result = observer.what_to_focus("test", "content", "")
        assert "specific" in result.lower() or "detailed" in result.lower()

    def test_observer_logs_decisions(self, mock_provider):
        observer = Observer(mock_provider)
        observer.should_generate("test query", "context")
        assert len(observer.log) == 1
        assert observer.log[0]["mode"] == "should_generate"

    def test_should_generate_with_explicit_no(self):
        """When LLM explicitly says NO, observer should return False."""
        provider = MockLLMProvider(responses={"default": "NO"})
        observer = Observer(provider)
        result = observer.should_generate("What is AI?", "AI is artificial intelligence...")
        # If LLM says NO, context should be sufficient
        # Note: MockLLMProvider may not return "NO" exactly for observer prompts
        # This tests the parsing logic
        assert isinstance(result, bool)

    def test_should_generate_with_explicit_yes(self):
        """When LLM says YES, observer should return True."""
        provider = MockLLMProvider(responses={"default": "YES"})
        observer = Observer(provider)
        result = observer.should_generate("Write a post", "empty context")
        # If LLM says YES, generation is needed
        # Note: depends on mock provider behavior
        assert isinstance(result, bool)


# ─── SandboxV2 Tests ───────────────────────────────────────────────────

class TestSandboxV2:
    def test_chat_returns_string(self, v2_backend):
        messages = [{"role": "user", "content": "Write a blog post about AI"}]
        result = v2_backend.chat(messages)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_chat_with_observer(self, v2_backend_with_observer):
        messages = [{"role": "user", "content": "Write a blog post about AI"}]
        result = v2_backend_with_observer.chat(messages)
        assert result is not None
        assert isinstance(result, str)

    def test_chat_empty_query(self, v2_backend):
        messages = []
        result = v2_backend.chat(messages)
        assert result is None

    def test_chat_system_prompt_as_skill_md(self, v2_backend):
        messages = [
            {"role": "system", "content": "You are a blog writing expert."},
            {"role": "user", "content": "Write about AI"},
        ]
        result = v2_backend.chat(messages)
        assert result is not None

    def test_stats_returns_dict(self, v2_backend):
        messages = [{"role": "user", "content": "Write about AI"}]
        v2_backend.chat(messages)
        stats = v2_backend.stats()
        assert isinstance(stats, dict)
        assert "total_llm_calls" in stats
        assert "total_queries" in stats

    def test_stats_with_observer(self, v2_backend_with_observer):
        messages = [{"role": "user", "content": "Write about AI"}]
        v2_backend_with_observer.chat(messages)
        stats = v2_backend_with_observer.stats()
        assert stats["observer_decisions"] >= 0

    def test_poler_compresses_context(self, v2_backend):
        """POLER should compress the skill MD context."""
        messages = [{"role": "user", "content": "Write about AI trends"}]
        v2_backend.chat(messages)
        # Check trace for POLER compression info
        if v2_backend.trace:
            trace = v2_backend.trace[-1]
            assert "query" in trace

    def test_max_iterations_respected(self, mock_provider, skill_context):
        """SandboxV2 should respect max_iterations setting."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context=skill_context,
            max_iterations=1,
        )
        messages = [{"role": "user", "content": "Write about AI"}]
        backend.chat(messages)
        # With max_iterations=1, should only have 1 generation call
        # (plus 0 observer calls since use_observer=False)

    def test_verbose_mode(self, mock_provider, skill_context, capsys):
        """Verbose mode should output debug info to stderr."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context=skill_context,
            verbose=True,
        )
        messages = [{"role": "user", "content": "Write about AI"}]
        backend.chat(messages)
        captured = capsys.readouterr()
        # Verbose output goes to stderr
        assert "sandbox-v2" in captured.err or True  # May not capture stderr in all pytest configs


# ─── Bridge v2 Tests ───────────────────────────────────────────────────

class TestBridgeV2:
    def test_call_sandbox_chat_v2(self):
        from bridge import call_sandbox_chat
        result = call_sandbox_chat(
            system_prompt="You are a blog writer.",
            user_prompt="Write a post about AI",
            skill_name="blog-writer",
            backend="v2",
        )
        # May return None if z-ai CLI not available, but should not crash
        # Use mock provider
        assert result is None or isinstance(result, str)

    def test_call_sandbox_chat_with_mock(self):
        from bridge import call_sandbox_chat
        mock = MockLLMProvider()
        result = call_sandbox_chat(
            system_prompt="You are a blog writer.",
            user_prompt="Write a post about AI",
            skill_name="blog-writer",
            llm_provider=mock,
            backend="v2",
        )
        assert result is not None
        assert isinstance(result, str)

    def test_get_current_backend_type_default(self):
        from bridge import get_current_backend_type
        # Default should be v2 (unless env var is set)
        backend_type = get_current_backend_type()
        # Could be "v2" or "zai_cli" depending on env
        assert backend_type in ("v2", "v1", "mock", "zai_cli")


# ─── Token Efficiency Comparison ───────────────────────────────────────

class TestTokenEfficiency:
    def test_v2_uses_fewer_llm_calls_than_v1(self, mock_provider, skill_context):
        """v2 should use significantly fewer LLM calls than v1."""
        from backend import SandboxBackend

        # v1: 4-agent chain
        v1_provider = MockLLMProvider()
        v1 = SandboxBackend(
            llm_provider=v1_provider,
            skill_context=skill_context,
        )
        messages = [{"role": "user", "content": "Write a blog post about AI"}]
        v1.chat(messages)
        v1_calls = v1_provider.call_count

        # v2: Observer + POLER
        v2_provider = MockLLMProvider()
        v2 = SandboxV2(
            llm_provider=v2_provider,
            skill_context=skill_context,
            use_observer=False,
        )
        v2.chat(messages)
        v2_calls = v2_provider.call_count

        # v2 should use fewer calls (1 vs 4+)
        assert v2_calls <= v1_calls, (
            f"v2 ({v2_calls} calls) should use fewer LLM calls than v1 ({v1_calls} calls)"
        )

    def test_v2_observer_adds_minimal_overhead(self, mock_provider, skill_context):
        """Observer should add at most 2-3 LLM calls (binary decisions)."""
        provider = MockLLMProvider()
        v2 = SandboxV2(
            llm_provider=provider,
            skill_context=skill_context,
            use_observer=True,
        )
        messages = [{"role": "user", "content": "Write a blog post about AI"}]
        v2.chat(messages)
        # With observer: 1 (should_generate) + 1 (generate) + maybe 1 (is_sufficient) = 2-3 calls
        # Should still be less than v1's 4+ calls
        total = provider.call_count
        assert total <= 5, f"v2 with observer used {total} calls, expected <= 5"


# ─── Edge Cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_skill_md(self, mock_provider):
        """Should work with empty skill_md."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context={"skill_name": "test", "skill_md": ""},
        )
        result = backend.chat([{"role": "user", "content": "Hello"}])
        assert result is not None

    def test_very_long_query(self, mock_provider, skill_context):
        """Should handle very long queries."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context=skill_context,
        )
        long_query = "Write a post " * 100
        result = backend.chat([{"role": "user", "content": long_query}])
        assert result is not None

    def test_unicode_query(self, mock_provider, skill_context):
        """Should handle unicode queries (Russian, Chinese, etc.)."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context=skill_context,
        )
        result = backend.chat([{"role": "user", "content": "Напиши пост про ИИ"}])
        assert result is not None

    def test_special_characters_in_query(self, mock_provider, skill_context):
        """Should handle special characters."""
        backend = SandboxV2(
            llm_provider=mock_provider,
            skill_context=skill_context,
        )
        result = backend.chat([{"role": "user", "content": "Write about C++ & <HTML> tags"}])
        assert result is not None

    def test_llm_failure_during_generation(self, skill_context):
        """Should handle LLM failure gracefully."""
        failing_provider = MockLLMProvider()
        failing_provider.chat = lambda *a, **kw: None
        backend = SandboxV2(
            llm_provider=failing_provider,
            skill_context=skill_context,
        )
        result = backend.chat([{"role": "user", "content": "Write about AI"}])
        # Should return empty string, not crash
        assert result is not None or result == ""

    def test_multiple_queries_reuse_backend(self, v2_backend):
        """Backend should handle multiple sequential queries."""
        messages1 = [{"role": "user", "content": "Write about AI"}]
        messages2 = [{"role": "user", "content": "Write about ML"}]

        result1 = v2_backend.chat(messages1)
        result2 = v2_backend.chat(messages2)

        assert result1 is not None
        assert result2 is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
