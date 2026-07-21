"""
backend.py — SandboxBackend: Agent orchestration with LLM-powered agents.

This is the core of the sandbox. It provides the same interface as external
LLM providers (chat() returning text), but instead of calling OpenAI/Anthropic
once, it distributes work between LLM-powered internal agents:

    PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent

Each agent calls the SAME LLM with a DIFFERENT role-specific system prompt.
This is exactly how multi-agent systems work in ChatGPT, Claude, Kimi 2.0.

The cycle repeats if the Critic decides to "iterate" (up to MAX_ROUNDS).

Usage:
    from sandbox import SandboxBackend
    from sandbox.llm_provider import HostLLMProvider

    # With z-ai CLI as the host LLM
    provider = HostLLMProvider.from_zai_cli()
    backend = SandboxBackend(llm_provider=provider)
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}]
    )

    # With a Python callback as the host LLM
    def my_llm(system_prompt, user_prompt):
        # Call your own LLM here
        return "response text"

    provider = HostLLMProvider(callback=my_llm)
    backend = SandboxBackend(llm_provider=provider,
                             skill_context={"skill_name": "blog-writer"})

    # Via CLI
    super-z --run blog-writer "напиши пост" --backend sandbox
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List, Optional

from agents import (
    PlannerAgent,
    ExecutorAgent,
    ReviewerAgent,
    CriticAgent,
    AgentMessage,
)
from llm_provider import LLMProvider, HostLLMProvider, MockLLMProvider


# ─── Sandbox Backend ────────────────────────────────────────────────────

class SandboxBackend:
    """LLM-powered backend that uses internal agent orchestration.

    Each agent calls the LLM with a role-specific system prompt,
    producing real reasoning and content instead of template strings.
    """

    MAX_ROUNDS = 3  # maximum planner→executor→reviewer→critic cycles

    def __init__(self, llm_provider: Optional[LLMProvider] = None,
                 skill_context: Optional[Dict] = None,
                 max_rounds: int = MAX_ROUNDS,
                 verbose: bool = False):
        self.skill_context = skill_context or {}
        self.max_rounds = max_rounds
        self.verbose = verbose

        # Set up LLM provider
        # Default: use z-ai CLI as the host LLM (same LLM, different roles)
        self.llm_provider = llm_provider or HostLLMProvider.from_zai_cli()

        # Create agent instances — each gets the SAME LLM provider
        # but uses a DIFFERENT system prompt
        self.planner = PlannerAgent(self.llm_provider, skill_context)
        self.executor = ExecutorAgent(self.llm_provider, skill_context)
        self.reviewer = ReviewerAgent(self.llm_provider, skill_context)
        self.critic = CriticAgent(self.llm_provider, skill_context)

        # Execution trace for debugging
        self.trace: List[Dict] = []

    def chat(self, messages: List[Dict[str, str]],
             **kwargs) -> Optional[str]:
        """Main API: takes messages, returns text response.

        This is the same interface as call_z_ai_chat() in llm_wrapper.py,
        making it a drop-in replacement.

        Args:
            messages: List of {"role": "user"|"system"|"assistant",
                               "content": "..."} dicts.
            **kwargs: Ignored (for API compatibility).

        Returns:
            Generated text string, or None on failure.
        """
        start_time = time.time()

        # Extract user query from messages
        query = self._extract_query(messages)
        system_prompt = self._extract_system_prompt(messages)

        # Update skill context with system prompt if present
        if system_prompt and not self.skill_context.get("skill_md"):
            self.skill_context["skill_md"] = system_prompt
            self._refresh_agents()

        if not query:
            return None

        # Run the agent loop
        result = self._run_agent_loop(query)

        # Record trace
        elapsed = time.time() - start_time
        self.trace.append({
            "query": query[:200],
            "result_length": len(result) if result else 0,
            "elapsed_sec": round(elapsed, 2),
            "llm_calls": self.llm_provider.call_count
                         if hasattr(self.llm_provider, 'call_count')
                         else -1,
        })

        if self.verbose:
            provider_name = (self.llm_provider.name()
                           if hasattr(self.llm_provider, 'name')
                           else "unknown")
            print(f"[sandbox] Completed in {elapsed:.2f}s via {provider_name}, "
                  f"result: {len(result) if result else 0} chars",
                  file=sys.stderr)

        return result

    def _run_agent_loop(self, query: str) -> str:
        """Run the planner→executor→reviewer→critic loop.

        Each agent calls the LLM with its role-specific system prompt.
        The loop continues until the critic accepts or max rounds reached.
        """
        current_content = ""

        for round_num in range(1, self.max_rounds + 1):
            if self.verbose:
                print(f"[sandbox] Round {round_num}/{self.max_rounds}",
                      file=sys.stderr)

            # Step 1: Planner calls LLM with PLANNER_SYSTEM_PROMPT
            plan_msg = AgentMessage(
                role="user",
                action="start_plan",
                payload={"query": query},
            )
            plan_result = self.planner.process(plan_msg)

            # Step 2: Executor calls LLM with EXECUTOR_SYSTEM_PROMPT
            exec_result = self.executor.process(plan_result)

            # Step 3: Reviewer calls LLM with REVIEWER_SYSTEM_PROMPT
            review_result = self.reviewer.process(exec_result)

            # Step 4: Critic calls LLM with CRITIC_SYSTEM_PROMPT
            critic_result = self.critic.process(review_result)

            # Record round trace
            self.trace.append({
                "round": round_num,
                "plan_confidence": plan_result.confidence,
                "exec_confidence": exec_result.confidence,
                "review_verdict": review_result.payload.get("verdict"),
                "review_score": review_result.payload.get("average_score"),
                "critic_decision": critic_result.payload.get("decision"),
            })

            # Extract content from executor result
            current_content = exec_result.payload.get("final_content", "")

            # Check critic's decision
            decision = critic_result.payload.get("decision", "accept")

            if decision == "accept":
                break

            elif decision == "reject":
                # Restart from planner with feedback
                query = self._inject_feedback(
                    query,
                    critic_result.payload.get("reason", ""),
                    review_result.payload.get("issues", []),
                    critic_result.payload.get("focus_areas", []),
                )
                current_content = ""
                self.critic.iteration = 0  # reset for new attempt
                continue

            elif decision == "iterate":
                # Re-run from executor with feedback
                query = self._inject_feedback(
                    query,
                    "; ".join(review_result.payload.get("suggestions", [])),
                    review_result.payload.get("issues", []),
                    critic_result.payload.get("focus_areas", []),
                )
                continue

        return current_content

    def _extract_query(self, messages: List[Dict]) -> str:
        """Extract the user's query from the messages list."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        if messages:
            return messages[-1].get("content", "")
        return ""

    def _extract_system_prompt(self, messages: List[Dict]) -> str:
        """Extract system prompt from messages."""
        for msg in messages:
            if msg.get("role") == "system":
                return msg.get("content", "")
        return ""

    def _refresh_agents(self):
        """Recreate agents with updated skill context."""
        self.planner = PlannerAgent(self.llm_provider, self.skill_context)
        self.executor = ExecutorAgent(self.llm_provider, self.skill_context)
        self.reviewer = ReviewerAgent(self.llm_provider, self.skill_context)
        self.critic = CriticAgent(self.llm_provider, self.skill_context)

    def _inject_feedback(self, query: str, reason: str,
                         issues: List[str],
                         focus_areas: List[str] = None) -> str:
        """Add feedback from reviewer/critic to the query for next round."""
        parts = [query]
        if reason:
            parts.append(f"[FEEDBACK: {reason}]")
        if issues:
            parts.append(f"[ISSUES: {'; '.join(issues[:3])}]")
        if focus_areas:
            parts.append(f"[FOCUS: {'; '.join(focus_areas[:3])}]")
        return " ".join(parts)

    def stats(self) -> Dict:
        """Return execution statistics."""
        total_rounds = len([t for t in self.trace if t.get("round")])
        avg_score = 0.0
        scores = [t.get("review_score", 0) for t in self.trace
                  if t.get("review_score") is not None]
        if scores:
            avg_score = sum(scores) / len(scores)

        llm_calls = -1
        if hasattr(self.llm_provider, 'call_count'):
            llm_calls = self.llm_provider.call_count

        provider_name = "unknown"
        if hasattr(self.llm_provider, 'name'):
            provider_name = self.llm_provider.name()

        return {
            "total_queries": len([t for t in self.trace if t.get("query")]),
            "total_rounds": total_rounds,
            "average_review_score": round(avg_score, 2),
            "llm_calls": llm_calls,
            "llm_provider": provider_name,
            "trace": self.trace[-10:],
        }
