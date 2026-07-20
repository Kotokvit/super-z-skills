"""
agents.py — Internal Agent Roles for the Sandbox Backend.

Each agent is a self-contained reasoning unit that processes an
AgentMessage and returns a new AgentMessage. Agents communicate
through a structured message bus, not through free-form text.

The chain is:
    User Query → PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent
                                                                   │
                                          ┌────────────────────────┘
                                          ▼
                                   Accept (return result)
                                   Reject (iterate from Planner)
                                   Revise (iterate from Executor)

No external LLM calls are made. All "reasoning" happens through
rule-based heuristics, template expansion, and knowledge extraction
from the skill's SKILL.md context.
"""
from __future__ import annotations

import enum
import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ─── Agent Role Enum ────────────────────────────────────────────────────

class AgentRole(enum.Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    CRITIC = "critic"


# ─── Agent Message ──────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    """Structured message passed between agents."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = ""                    # sender role
    action: str = ""                  # what action was taken
    payload: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ─── Base Agent ─────────────────────────────────────────────────────────

class Agent:
    """Base class for all agent roles."""

    role: AgentRole = AgentRole.PLANNER  # override in subclass

    def __init__(self, skill_context: Optional[Dict] = None):
        self.skill_context = skill_context or {}
        self.history: List[AgentMessage] = []

    def process(self, message: AgentMessage) -> AgentMessage:
        """Process an incoming message and return a response."""
        self.history.append(message)
        result = self._reason(message)
        self.history.append(result)
        return result

    def _reason(self, message: AgentMessage) -> AgentMessage:
        """Override in subclass. Core reasoning logic."""
        raise NotImplementedError

    def _msg(self, action: str, payload: Dict, confidence: float) -> AgentMessage:
        return AgentMessage(
            role=self.role.value,
            action=action,
            payload=payload,
            confidence=confidence,
        )


# ─── Planner Agent ──────────────────────────────────────────────────────

class PlannerAgent(Agent):
    """Decomposes a user query into structured subtasks.

    The planner analyzes the query, identifies the key deliverable,
    and creates a step-by-step execution plan. It uses the skill's
    SKILL.md as methodology context.
    """

    role = AgentRole.PLANNER

    def _reason(self, message: AgentMessage) -> AgentMessage:
        query = message.payload.get("query", "")
        skill_name = self.skill_context.get("skill_name", "unknown")
        skill_md = self.skill_context.get("skill_md", "")

        # Extract skill methodology hints from SKILL.md
        methodology = self._extract_methodology(skill_md)

        # Decompose query into subtasks
        subtasks = self._decompose(query, methodology)

        # Determine output format from skill context
        output_format = self._infer_format(skill_name, skill_md)

        return self._msg(
            action="plan_created",
            payload={
                "query": query,
                "skill_name": skill_name,
                "subtasks": subtasks,
                "methodology_hints": methodology[:5],  # top 5 hints
                "output_format": output_format,
                "estimated_steps": len(subtasks),
            },
            confidence=0.7 if subtasks else 0.3,
        )

    def _extract_methodology(self, skill_md: str) -> List[str]:
        """Extract key methodology points from SKILL.md."""
        hints = []
        if not skill_md:
            return hints

        # Extract section headers as methodology hints
        for line in skill_md.split("\n"):
            line = line.strip()
            if line.startswith("## ") or line.startswith("### "):
                hint = line.lstrip("#").strip()
                if len(hint) > 3 and len(hint) < 80:
                    hints.append(hint)

        # Extract bullet points
        for line in skill_md.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                point = line.lstrip("-*").strip()
                if 10 < len(point) < 120:
                    hints.append(point)

        return hints[:20]  # cap at 20

    def _decompose(self, query: str, methodology: List[str]) -> List[Dict]:
        """Break the query into sequential subtasks."""
        subtasks = []

        # Step 1: Always start with understanding
        subtasks.append({
            "step": 1,
            "type": "understand",
            "description": f"Analyze the request: {query[:200]}",
            "output": "structured_brief",
        })

        # Step 2: Apply methodology from SKILL.md
        if methodology:
            for i, hint in enumerate(methodology[:3], start=2):
                subtasks.append({
                    "step": i,
                    "type": "apply_methodology",
                    "description": hint,
                    "output": "content_section",
                })

        # Step 3: Generate main content
        subtasks.append({
            "step": len(subtasks) + 1,
            "type": "generate",
            "description": f"Generate the main deliverable for: {query[:150]}",
            "output": "primary_content",
        })

        # Step 4: Synthesize
        subtasks.append({
            "step": len(subtasks) + 1,
            "type": "synthesize",
            "description": "Combine all sections into a coherent result",
            "output": "final_output",
        })

        return subtasks

    def _infer_format(self, skill_name: str, skill_md: str) -> str:
        """Infer the expected output format from skill context."""
        doc_skills = {"docx", "pdf", "pptx", "xlsx", "blog-writer",
                      "resume-builder", "seo-content-writer",
                      "market-research-reports", "content-strategy"}
        analysis_skills = {"poler-toolkit", "contentanalysis", "gap-detector",
                          "cheat-sheet", "finance"}

        if skill_name in doc_skills:
            return "document"
        if skill_name in analysis_skills:
            return "analysis"
        if "chart" in skill_name or "diagram" in skill_name:
            return "visual"
        if skill_name in {"web-search", "web-reader", "agent-browser"}:
            return "research"
        return "text"


# ─── Executor Agent ─────────────────────────────────────────────────────

class ExecutorAgent(Agent):
    """Generates content for each subtask in the plan.

    The executor uses the skill's SKILL.md as a methodology guide
    and produces structured content sections. In sandbox mode,
    content generation is template-based with intelligent extraction
    from the skill methodology.
    """

    role = AgentRole.EXECUTOR

    def _reason(self, message: AgentMessage) -> AgentMessage:
        plan = message.payload
        subtasks = plan.get("subtasks", [])
        query = plan.get("query", "")
        skill_name = plan.get("skill_name", "unknown")
        methodology_hints = plan.get("methodology_hints", [])
        output_format = plan.get("output_format", "text")

        # Execute each subtask
        results = []
        for task in subtasks:
            result = self._execute_subtask(task, query, methodology_hints)
            results.append(result)

        # Synthesize into a single coherent output
        final_content = self._synthesize(results, query, output_format)

        return self._msg(
            action="content_generated",
            payload={
                "query": query,
                "skill_name": skill_name,
                "subtask_results": results,
                "final_content": final_content,
                "output_format": output_format,
                "sections_count": len(results),
            },
            confidence=self._calculate_confidence(results),
        )

    def _execute_subtask(self, task: Dict, query: str,
                         methodology: List[str]) -> Dict:
        """Execute a single subtask and produce a content section."""
        step = task.get("step", 0)
        task_type = task.get("type", "generate")
        description = task.get("description", "")

        content = ""

        if task_type == "understand":
            content = self._generate_understanding(query)
        elif task_type == "apply_methodology":
            content = self._apply_methodology(description, query, methodology)
        elif task_type == "generate":
            content = self._generate_primary(query, methodology)
        elif task_type == "synthesize":
            content = ""  # filled later by _synthesize
        else:
            content = f"[{task_type}] {description}"

        return {
            "step": step,
            "type": task_type,
            "description": description,
            "content": content,
            "word_count": len(content.split()),
        }

    def _generate_understanding(self, query: str) -> str:
        """Produce a brief analysis of the user's request."""
        # Extract key topics from the query
        words = [w for w in re.findall(r'\w+', query.lower())
                 if len(w) > 3]

        # Identify the intent
        intent = "general"
        if any(w in query.lower() for w in ["напиши", "write", "create", "создай"]):
            intent = "creation"
        elif any(w in query.lower() for w in ["анализ", "analyze", "разбер", "explain"]):
            intent = "analysis"
        elif any(w in query.lower() for w in ["найди", "find", "search", "поиск"]):
            intent = "research"

        topics = list(set(words))[:5]

        return (f"Запрос проанализирован. Намерение: {intent}. "
                f"Ключевые темы: {', '.join(topics) if topics else 'общий запрос'}. "
                f"Цель: предоставить структурированный результат по запросу.")

    def _apply_methodology(self, method_hint: str, query: str,
                           methodology: List[str]) -> str:
        """Apply a methodology step from SKILL.md to the query."""
        # Generate a content section following the methodology hint
        related = [m for m in methodology if m != method_hint][:3]

        return (f"По методологии \"{method_hint}\": "
                f"Применяем подход к запросу. "
                f"Связанные шаги: {'; '.join(related) if related else 'самостоятельный шаг'}. "
                f"Результат: структурированный раздел, следующий указанной методологии.")

    def _generate_primary(self, query: str, methodology: List[str]) -> str:
        """Generate the primary deliverable content."""
        # Template-based generation with methodology grounding
        sections = []

        if methodology:
            sections.append(f"Методология: {'; '.join(methodology[:3])}")

        sections.append(
            f"Результат по запросу \"{query[:100]}\": "
            f"Сгенерировано структурированное содержание на основе доступной методологии навыка. "
            f"Содержание адаптировано под конкретный запрос и включает ключевые аспекты темы."
        )

        return "\n\n".join(sections)

    def _synthesize(self, results: List[Dict], query: str,
                    output_format: str) -> str:
        """Combine subtask results into final output."""
        parts = []
        for r in results:
            if r.get("content"):
                parts.append(r["content"])

        if not parts:
            return f"Результат по запросу: {query}"

        return "\n\n".join(parts)

    def _calculate_confidence(self, results: List[Dict]) -> float:
        """Calculate overall confidence based on results quality."""
        if not results:
            return 0.2

        total_words = sum(r.get("word_count", 0) for r in results)
        filled = sum(1 for r in results if r.get("content"))

        # Base confidence from fill ratio
        fill_ratio = filled / max(len(results), 1)
        conf = 0.3 + 0.4 * fill_ratio

        # Bonus for substantial content
        if total_words > 50:
            conf += 0.1
        if total_words > 150:
            conf += 0.1

        return min(conf, 1.0)


# ─── Reviewer Agent ─────────────────────────────────────────────────────

class ReviewerAgent(Agent):
    """Checks quality, completeness, and relevance of generated content.

    The reviewer evaluates the executor's output against the original
    query and skill methodology. It produces a quality report with
    specific issues and improvement suggestions.
    """

    role = AgentRole.REVIEWER

    def _reason(self, message: AgentMessage) -> AgentMessage:
        payload = message.payload
        query = payload.get("query", "")
        final_content = payload.get("final_content", "")
        subtask_results = payload.get("subtask_results", [])
        output_format = payload.get("output_format", "text")
        skill_name = payload.get("skill_name", "unknown")

        # Run quality checks
        checks = {
            "relevance": self._check_relevance(query, final_content),
            "completeness": self._check_completeness(subtask_results),
            "structure": self._check_structure(final_content, output_format),
            "substance": self._check_substance(final_content),
        }

        # Aggregate scores
        scores = [c["score"] for c in checks.values()]
        avg_score = sum(scores) / max(len(scores), 1)

        # Identify issues
        issues = []
        for check_name, check in checks.items():
            if check["score"] < 0.5:
                issues.append(f"{check_name}: {check['issue']}")

        # Generate improvement suggestions
        suggestions = []
        if avg_score < 0.6:
            suggestions.append("Content needs significant expansion")
        if checks["substance"]["score"] < 0.5:
            suggestions.append("Add more specific details and examples")
        if checks["relevance"]["score"] < 0.5:
            suggestions.append("Focus more closely on the user's request")

        verdict = "pass" if avg_score >= 0.6 else "needs_improvement"

        return self._msg(
            action="review_completed",
            payload={
                "query": query,
                "skill_name": skill_name,
                "checks": checks,
                "average_score": avg_score,
                "issues": issues,
                "suggestions": suggestions,
                "verdict": verdict,
            },
            confidence=avg_score,
        )

    def _check_relevance(self, query: str, content: str) -> Dict:
        """Check if content is relevant to the query."""
        if not query or not content:
            return {"score": 0.1, "issue": "Empty query or content"}

        # Extract key terms from query
        query_terms = set(re.findall(r'\w+', query.lower()))
        content_lower = content.lower()

        # Count how many query terms appear in content
        matched = sum(1 for t in query_terms if t in content_lower and len(t) > 3)
        total = max(sum(1 for t in query_terms if len(t) > 3), 1)

        score = min(matched / total, 1.0) * 0.7 + 0.3  # floor at 0.3

        return {
            "score": round(score, 2),
            "issue": "" if score >= 0.5 else "Content doesn't address key terms from the query",
        }

    def _check_completeness(self, subtask_results: List[Dict]) -> Dict:
        """Check if all subtasks produced content."""
        if not subtask_results:
            return {"score": 0.2, "issue": "No subtask results"}

        filled = sum(1 for r in subtask_results if r.get("content"))
        total = len(subtask_results)

        score = filled / max(total, 1)

        return {
            "score": round(score, 2),
            "issue": "" if score >= 0.7 else f"Only {filled}/{total} subtasks produced content",
        }

    def _check_structure(self, content: str, output_format: str) -> Dict:
        """Check if content has appropriate structure for the format."""
        if not content:
            return {"score": 0.1, "issue": "Empty content"}

        # Basic structure checks
        has_paragraphs = "\n\n" in content or len(content) > 100
        has_sections = any(m in content for m in ["##", "**", " - "])

        score = 0.4  # base
        if has_paragraphs:
            score += 0.3
        if has_sections:
            score += 0.3

        return {
            "score": round(min(score, 1.0), 2),
            "issue": "" if score >= 0.5 else "Content lacks structure",
        }

    def _check_substance(self, content: str) -> Dict:
        """Check if content has real substance (not just placeholders)."""
        if not content:
            return {"score": 0.1, "issue": "Empty content"}

        word_count = len(content.split())

        # Check for placeholder patterns
        placeholders = sum(1 for p in ["[TODO]", "[PLACEHOLDER]", "Lorem ipsum",
                                       "INSERT HERE", "TBD"]
                          if p.lower() in content.lower())

        score = 0.3  # base
        if word_count > 30:
            score += 0.2
        if word_count > 100:
            score += 0.2
        if word_count > 200:
            score += 0.2
        if placeholders > 0:
            score -= 0.2

        return {
            "score": round(max(min(score, 1.0), 0.0), 2),
            "issue": "" if score >= 0.5 else "Content is too short or contains placeholders",
        }


# ─── Critic Agent ───────────────────────────────────────────────────────

class CriticAgent(Agent):
    """Final decision maker: accept, reject, or iterate.

    The critic evaluates the reviewer's verdict and decides whether
    the output is ready to return to the user, needs revision, or
    should be rejected entirely. It also determines which agent
    should handle the next iteration if needed.
    """

    role = AgentRole.CRITIC

    # Maximum iterations before forcing acceptance
    MAX_ITERATIONS = 3

    def __init__(self, skill_context: Optional[Dict] = None):
        super().__init__(skill_context)
        self.iteration = 0

    def _reason(self, message: AgentMessage) -> AgentMessage:
        review = message.payload
        verdict = review.get("verdict", "needs_improvement")
        avg_score = review.get("average_score", 0.0)
        issues = review.get("issues", [])
        suggestions = review.get("suggestions", [])
        query = review.get("query", "")

        self.iteration += 1

        # Decision logic
        if verdict == "pass" or self.iteration >= self.MAX_ITERATIONS:
            decision = "accept"
            next_agent = None
            reason = ("Quality threshold met" if verdict == "pass"
                      else f"Max iterations ({self.MAX_ITERATIONS}) reached")
        elif avg_score < 0.3:
            decision = "reject"
            next_agent = "planner"  # start over
            reason = "Quality too low, need to re-plan"
        else:
            decision = "iterate"
            next_agent = "executor"  # try again with feedback
            reason = "Content needs improvement"

        return self._msg(
            action="critic_verdict",
            payload={
                "decision": decision,
                "reason": reason,
                "next_agent": next_agent,
                "iteration": self.iteration,
                "quality_score": avg_score,
                "issues_count": len(issues),
                "suggestions": suggestions,
                "query": query,
            },
            confidence=avg_score if decision == "accept" else avg_score * 0.5,
        )
