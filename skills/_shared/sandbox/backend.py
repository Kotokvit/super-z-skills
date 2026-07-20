"""
backend.py — SandboxBackend: LLM-free internal agent orchestration.

This is the core of the "pseudo-API" sandbox. It provides the same
interface as external LLM providers (chat() returning text), but
instead of calling OpenAI/Anthropic/z-ai, it distributes work
between internal agents:

    PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent

The cycle repeats if the Critic decides to "iterate" (up to MAX_ROUNDS).

This backend is designed for the scenario where:
1. The tool is embedded in an AI sandbox on a website
2. The AI connects through its own agents on its own server
3. No external LLM provider is needed
4. The AI itself IS the "LLM" — it processes queries through role distribution

Usage:
    from sandbox import SandboxBackend

    # As standalone
    backend = SandboxBackend()
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}]
    )

    # With skill context (used by llm_wrapper.py)
    backend = SandboxBackend(skill_context={
        "skill_name": "blog-writer",
        "skill_md": "# Blog Writer\\n\\nWrite engaging blog posts...",
    })
    result = backend.chat(
        messages=[{"role": "user", "content": "напиши пост про ИИ"}]
    )

    # Via CLI
    super-z --run blog-writer "напиши пост" --backend sandbox

Integration with llm_wrapper.py:
    The backend replaces call_z_ai_chat() when the user selects
    backend="sandbox". It returns the same type of result — a text
    string that the wrapper formats as a Pattern 1 brief.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sandbox.agents import (
    Agent,
    PlannerAgent,
    ExecutorAgent,
    ReviewerAgent,
    CriticAgent,
    AgentMessage,
    AgentRole,
)


# ─── Sandbox Backend ────────────────────────────────────────────────────

class SandboxBackend:
    """LLM-free backend that uses internal agent orchestration.

    This is NOT a mock — it performs real work by decomposing queries,
    generating structured content from skill methodology, reviewing
    quality, and iterating until the critic accepts or max rounds reached.
    """

    MAX_ROUNDS = 3  # maximum planner→executor→reviewer→critic cycles

    def __init__(self, skill_context: Optional[Dict] = None,
                 max_rounds: int = MAX_ROUNDS,
                 verbose: bool = False):
        self.skill_context = skill_context or {}
        self.max_rounds = max_rounds
        self.verbose = verbose

        # Create agent instances
        self.planner = PlannerAgent(skill_context)
        self.executor = ExecutorAgent(skill_context)
        self.reviewer = ReviewerAgent(skill_context)
        self.critic = CriticAgent(skill_context)

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
            "rounds": len([t for t in self.trace if t.get("round")]),
        })

        if self.verbose:
            print(f"[sandbox] Completed in {elapsed:.2f}s, "
                  f"result: {len(result) if result else 0} chars")

        return result

    def _run_agent_loop(self, query: str) -> str:
        """Run the planner→executor→reviewer→critic loop.

        Returns the final text output.
        """
        current_content = ""

        for round_num in range(1, self.max_rounds + 1):
            if self.verbose:
                print(f"[sandbox] Round {round_num}/{self.max_rounds}")

            # Step 1: Planner
            plan_msg = AgentMessage(
                role="user",
                action="start_plan",
                payload={"query": query},
            )
            plan_result = self.planner.process(plan_msg)

            # Step 2: Executor
            exec_result = self.executor.process(plan_result)

            # Step 3: Reviewer
            review_result = self.reviewer.process(exec_result)

            # Step 4: Critic
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
                # Apply review suggestions if content needs polish
                if review_result.payload.get("average_score", 0) < 0.8:
                    current_content = self._apply_polish(
                        current_content,
                        review_result.payload.get("suggestions", []),
                    )
                break

            elif decision == "reject":
                # Restart from planner with feedback
                query = self._inject_feedback(
                    query,
                    critic_result.payload.get("reason", ""),
                    review_result.payload.get("issues", []),
                )
                current_content = ""
                continue

            elif decision == "iterate":
                # Re-run from executor with feedback
                query = self._inject_feedback(
                    query,
                    "; ".join(review_result.payload.get("suggestions", [])),
                    review_result.payload.get("issues", []),
                )
                continue

        return current_content

    def _extract_query(self, messages: List[Dict]) -> str:
        """Extract the user's query from the messages list."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        # Fallback: return last message content
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
        self.planner = PlannerAgent(self.skill_context)
        self.executor = ExecutorAgent(self.skill_context)
        self.reviewer = ReviewerAgent(self.skill_context)
        self.critic = CriticAgent(self.skill_context)

    def _inject_feedback(self, query: str, reason: str,
                         issues: List[str]) -> str:
        """Add feedback from reviewer/critic to the query for next round."""
        feedback = f"[FEEDBACK: {reason}]"
        if issues:
            feedback += f" [ISSUES: {'; '.join(issues[:3])}]"
        return f"{query} {feedback}"

    def _apply_polish(self, content: str,
                      suggestions: List[str]) -> str:
        """Apply minor polish to accepted content."""
        if not content:
            return content

        # Ensure content ends cleanly
        content = content.rstrip()

        # Add quality notice if suggestions exist
        if suggestions and len(content) > 50:
            content += "\n\n---\nПримечание: результат сгенерирован в режиме " \
                       "песочницы (sandbox) без внешнего LLM."

        return content

    def stats(self) -> Dict:
        """Return execution statistics."""
        total_rounds = len([t for t in self.trace if t.get("round")])
        avg_score = 0.0
        scores = [t.get("review_score", 0) for t in self.trace
                  if t.get("review_score") is not None]
        if scores:
            avg_score = sum(scores) / len(scores)

        return {
            "total_queries": len([t for t in self.trace if t.get("query")]),
            "total_rounds": total_rounds,
            "average_review_score": round(avg_score, 2),
            "trace": self.trace[-10:],  # last 10 entries
        }
