from __future__ import annotations

import re
from typing import List, Dict, Any


class POLEREnhanced:
    """A lightweight resonance-based text processor.

    It does not need to read the whole text exhaustively. Instead it splits the
    content into fragments, scores them by resonance with the query, and returns
    a compact summary plus the most relevant spans. This keeps the sandbox fast
    while retaining the POLER-style flow: perception → epsilon → resonance.
    """

    def __init__(self, text: str, query: str, source: str = "") -> None:
        self.text = text or ""
        self.query = query or ""
        self.source = source or ""
        self.fragments = self._split_fragments(self.text)

    def analyze(self) -> Dict[str, Any]:
        if not self.text:
            return {"fragments": [], "summary": "", "scores": [], "epsilon": [], "resonance": []}

        scored_fragments = []
        for index, fragment in enumerate(self.fragments):
            epsilon = self._epsilon_score(fragment)
            resonance = self._resonance_score(fragment)
            scored_fragments.append((index, fragment, epsilon, resonance))

        ranked = sorted(scored_fragments, key=lambda item: item[3], reverse=True)[:5]
        selected = [
            {
                "index": index,
                "text": fragment,
                "epsilon": round(epsilon, 3),
                "resonance": round(resonance, 3),
                "source": self.source,
            }
            for index, fragment, epsilon, resonance in ranked
            if fragment.strip()
        ]
        summary = " | ".join(item["text"] for item in selected[:3]) if selected else self.text[:400]
        return {
            "fragments": [item["text"] for item in selected],
            "summary": summary[:800],
            "scores": [round(item[3], 3) for item in ranked],
            "epsilon": [round(item[2], 3) for item in ranked],
            "resonance": [round(item[3], 3) for item in ranked],
            "selected": selected,
        }

    def _split_fragments(self, text: str) -> List[str]:
        parts = re.split(r"\n{2,}|\n", text)
        return [part.strip() for part in parts if part and part.strip()]

    def _epsilon_score(self, fragment: str) -> float:
        if not fragment:
            return 0.0
        tokens = self._tokens(fragment)
        if not tokens:
            return 0.0
        query_terms = list(self._query_terms())
        overlap = len(set(query_terms) & set(tokens))
        rarity_bonus = min(len(set(tokens)) / 40.0, 0.4)
        return min(1.0, 0.2 + 0.6 * (overlap / max(1, len(query_terms))) + rarity_bonus)

    def _resonance_score(self, fragment: str) -> float:
        if not fragment:
            return 0.0
        query_terms = self._query_terms()
        fragment_terms = self._tokens(fragment)
        if not query_terms:
            return 0.5 + min(len(fragment_terms) / 50.0, 0.4)

        overlap = len(set(query_terms) & set(fragment_terms))
        length_bonus = min(len(fragment_terms) / 80.0, 0.3)
        return min(1.0, 0.3 + 0.6 * overlap / max(1, len(query_terms)) + length_bonus)

    def _query_terms(self) -> List[str]:
        terms = self._tokens(self.query)
        return terms or ["general"]

    @staticmethod
    def _tokens(text: str) -> List[str]:
        """Tokenize Latin, Cyrillic and other Unicode words consistently."""
        return [term.casefold() for term in re.findall(r"[\w]+", text, re.UNICODE) if len(term) > 2]
