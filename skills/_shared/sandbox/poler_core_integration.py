#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poler_core_integration.py — POLER[n] как ЯДРО СМЫСЛА
=====================================================

АРХИТЕКТУРА:
  grab (пылесос) → POLER (рентген) → brief (знание)
  
  Без POLER — grab собирает байты, но не понимает их.
  С POLER — байты превращаются в смысл: тема, ключевые слова,
  эпсилон-энергия фрагментов, резонанс между документами.

ИСПОЛЬЗОВАНИЕ:
  from poler_core_integration import PolerCore
  core = PolerCore()
  result = core.understand("/path/to/document.pdf")
  # → {theme, keywords, veins: [{keyword, epsilon, resonance}], summary}

ИНТЕГРАЦИЯ:
  - doc_triage.py → PolerCore.understand() вместо topic_local
  - super_z_core.py → PolerCore как первый этап Local-First
  - ingest.py → PolerCore для навигации по документу
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── POLER Enhanced Import ──────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent

# poler_enhanced.py is in the same _shared/sandbox directory
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:
    from poler_enhanced import (
        PolerAnalyzer,
        THEMES as _POLER_THEMES,
        TextWindow,
        read_file,
        read_epub,
        scan_directory,
        tokenize,
        compute_epsilon,
        compute_resonance_series,
        compute_cross_resonance,
        filter_pii,
        EMOTIONAL_MARKERS,
        STOPWORDS,
        NOISE_WORDS,
    )
    _HAS_POLER = True
except ImportError as e:
    sys.stderr.write(f"[poler_core] CRITICAL: poler_enhanced unavailable: {e}\n")
    _HAS_POLER = False

# Also try topic_common for LaTeX stripping
try:
    from topic_common import strip_latex, is_code, detect_language, extract_code_entities
    _HAS_TOPIC_COMMON = True
except ImportError:
    _HAS_TOPIC_COMMON = False


# ═══════════════════════════════════════════════════════════════════════
# РАСШИРЕННЫЕ ТЕМЫ — реальные научные домены + вселенная Этерии
# ═══════════════════════════════════════════════════════════════════════

SCIENTIFIC_THEMES: Dict[str, Dict[str, Any]] = {
    # ── Реальные научные домены ──
    "physics": {
        "keywords": [
            "Hamiltonian", "Lagrangian", "quantum", "entanglement", "Hilbert",
            "fermion", "boson", "gauge", "renormalization", "Feynman",
            "scattering", "perturbation", "symmetry", "isospin", "spinor",
            "vacuum", "propagator", "vertex", "cross-section", "decay",
            "lepton", "quark", "gluon", "hadron", "meson",
            "annihilation", "coupling", "topology", "manifold", "tensor",
            "covariant", "metric", "curvature", "Einstein", "Planck",
            "Schrödinger", "Dirac", "Pauli", "Noether", "Wigner",
            "DFT", "Kohn-Sham", "density functional", "wavefunction",
            "Hamiltonian", "eigenvalue", "eigenvector", "unitary",
            "Hermitian", "commutator", "braket", "bra", "ket",
            "supersymmetry", "string theory", "M-theory", "holography",
            "AdS", "CFT", "conformal", "dual", "branes",
            "condensed matter", "superconduct", "superfluid", "Bose-Einstein",
            "lattice", "Monte Carlo", "partition function", "correlation",
        ],
        "subthemes": {
            "quantum": ["qubit", "entangle", "teleport", "Bell", "superposition", "decoherence"],
            "particle": ["collider", "LHC", "Higgs", "quark", "lepton", "neutrino"],
            "condensed": ["superconduct", "phonon", "Fermi", "band", "crystal"],
            "astrophysics": ["neutron star", "black hole", "cosmic", "dark matter", "dark energy"],
            "nuclear": ["fission", "fusion", "radioact", "isotope", "decay"],
        }
    },
    "mathematics": {
        "keywords": [
            "theorem", "lemma", "proof", "corollary", "proposition",
            "conjecture", "axiom", "isomorphism", "homomorphism", "morphism",
            "topology", "manifold", "differential", "integral", "derivative",
            "algebra", "group", "ring", "field", "module",
            "matrix", "determinant", "eigenvalue", "eigenvector", "singular",
            "Fourier", "Laplace", "transform", "convolution", "series",
            "probability", "stochastic", "Markov", "Bayesian", "distribution",
            "optimization", "convex", "Lagrangian", "gradient", "descent",
            "graph theory", "combinatorics", "number theory", "cryptography",
            "category", "functor", "natural transformation", "adjoint",
            "Riemann", "Lebesgue", "measure", "Borel", "sigma-algebra",
        ],
        "subthemes": {
            "algebra": ["group", "ring", "field", "Galois", "abelian", "module"],
            "topology": ["manifold", "homotopy", "homology", "cohomology", "bundle"],
            "analysis": ["measure", "integral", "convergence", "continuity", "metric"],
            "probability": ["stochastic", "Markov", "Bayesian", "random", "expectation"],
            "number_theory": ["prime", "modular", "elliptic curve", "Diophantine"],
        }
    },
    "computer_science": {
        "keywords": [
            "algorithm", "complexity", "NP-complete", "Turing", "computability",
            "data structure", "binary tree", "hash", "graph", "heap",
            "machine learning", "neural network", "deep learning", "transformer",
            "attention", "backpropagation", "gradient", "loss function",
            "database", "SQL", "transaction", "index", "query",
            "compiler", "parser", "lexer", "AST", "optimization",
            "operating system", "kernel", "scheduler", "memory", "virtual",
            "network", "protocol", "TCP", "HTTP", "socket",
            "encryption", "RSA", "AES", "key", "signature",
            "distributed", "consensus", "Raft", "Paxos", "Byzantine",
            "container", "Docker", "Kubernetes", "microservice", "orchestration",
            "API", "REST", "GraphQL", "WebSocket", "middleware",
            "testing", "CI/CD", "deployment", "monitoring", "logging",
        ],
        "subthemes": {
            "ml": ["neural", "training", "inference", "feature", "classification"],
            "systems": ["kernel", "scheduler", "memory", "filesystem", "IPC"],
            "security": ["encryption", "vulnerability", "authentication", "exploit"],
            "web": ["frontend", "backend", "API", "database", "cache"],
        }
    },
    "chemistry": {
        "keywords": [
            "molecule", "atom", "bond", "orbital", "electron",
            "reaction", "catalyst", "synthesis", "polymer", "crystal",
            "organic", "inorganic", "analytical", "physical", "biochemistry",
            "spectroscopy", "chromatography", "mass spectrometry", "NMR", "X-ray",
            "enthalpy", "entropy", "Gibbs", "equilibrium", "kinetics",
            "oxidation", "reduction", "acid", "base", "pH",
            "SCF", "DFT", "Hartree-Fock", "basis set", "correlation",
            "potential energy", "transition state", "barrier", "conformation",
            "solvent", "solution", "solubility", "diffusion", "osmosis",
        ],
        "subthemes": {
            "quantum_chem": ["SCF", "DFT", "Hartree-Fock", "basis", "correlation"],
            "organic": ["synthesis", "functional group", "reaction mechanism", "stereochemistry"],
            "physical": ["thermodynamics", "kinetics", "equilibrium", "phase"],
        }
    },
    "biology": {
        "keywords": [
            "cell", "DNA", "RNA", "protein", "gene",
            "genome", "transcriptome", "proteome", "metabolome", "epigenome",
            "evolution", "selection", "mutation", "adaptation", "phylogeny",
            "ecology", "ecosystem", "biodiversity", "habitat", "population",
            "neuroscience", "synapse", "neuron", "cortex", "plasticity",
            "immunology", "antibody", "antigen", "T-cell", "immune",
            "microbiome", "bacteria", "virus", "phage", "plasmid",
            "CRISPR", "sequencing", "PCR", "expression", "regulation",
            "signal transduction", "pathway", "receptor", "ligand", "kinase",
            "apoptosis", "proliferation", "differentiation", "stem cell",
            "mitosis", "meiosis", "chromosome", "telomere", "centromere",
        ],
        "subthemes": {
            "genomics": ["sequencing", "genome", "CRISPR", "expression", "variant"],
            "neuro": ["neuron", "synapse", "brain", "cortex", "plasticity"],
            "ecology": ["ecosystem", "biodiversity", "population", "habitat"],
            "molecular": ["protein", "enzyme", "kinase", "receptor", "pathway"],
        }
    },
    "economics": {
        "keywords": [
            "GDP", "inflation", "unemployment", "fiscal", "monetary",
            "supply", "demand", "elasticity", "equilibrium", "market",
            "portfolio", "risk", "return", "volatility", "hedging",
            "econometrics", "regression", "panel data", "time series", "forecast",
            "trade", "tariff", "exchange rate", "balance", "current account",
            "game theory", "Nash", "oligopoly", "monopoly", "auction",
            "behavioral", "prospect theory", "nudge", "bias", "heuristic",
        ],
        "subthemes": {
            "macro": ["GDP", "inflation", "unemployment", "fiscal", "monetary"],
            "finance": ["portfolio", "risk", "option", "derivative", "hedge"],
            "micro": ["supply", "demand", "elasticity", "market", "competition"],
        }
    },
}

# Merge with POLER's original Этерия themes
ALL_THEMES: Dict[str, Dict[str, Any]] = {}
if _HAS_POLER:
    for name, kws in _POLER_THEMES.items():
        ALL_THEMES[name] = {"keywords": kws, "subthemes": {}}
ALL_THEMES.update(SCIENTIFIC_THEMES)


# ═══════════════════════════════════════════════════════════════════════
# АВТОМАТИЧЕСКОЕ ОБНАРУЖЕНИЕ ТЕМЫ
# ═══════════════════════════════════════════════════════════════════════

def detect_themes(text: str, sample_size: int = 20000) -> Dict[str, Any]:
    """Автоматически определить тему текста по словарям ALL_THEMES.
    
    Returns:
        {
            "primary": "physics",
            "secondary": ["quantum", "particle"],
            "scores": {"physics": 42, "mathematics": 5, ...},
            "matched_keywords": {"physics": ["Hamiltonian", "Feynman", ...], ...},
            "method": "poler_themes+v2"
        }
    """
    sample = text[:sample_size].lower()
    
    scores: Dict[str, int] = {}
    matched: Dict[str, List[str]] = {}
    
    for theme_name, theme_data in ALL_THEMES.items():
        kws = theme_data.get("keywords", [])
        score = 0
        found = []
        for kw in kws:
            kw_lower = kw.lower()
            # Use word boundary for short keywords, substring for long
            if len(kw) <= 3 or any(c.isdigit() for c in kw):
                pattern = r'\b' + re.escape(kw_lower) + r'\b'
                count = len(re.findall(pattern, sample))
            else:
                count = sample.count(kw_lower)
            if count > 0:
                score += count
                found.append(kw)
        
        scores[theme_name] = score
        matched[theme_name] = found
    
    # Sort by score
    sorted_themes = sorted(scores.items(), key=lambda x: -x[1])
    
    # Primary: highest scoring with ≥2 distinct keywords or score ≥5
    primary = "general"
    for name, score in sorted_themes:
        if score >= 5 or len(matched.get(name, [])) >= 2:
            primary = name
            break
    
    # Secondary: subthemes from primary
    secondary = []
    if primary in ALL_THEMES:
        subthemes = ALL_THEMES[primary].get("subthemes", {})
        for sub_name, sub_kws in subthemes.items():
            sub_score = sum(1 for kw in sub_kws if kw.lower() in sample)
            if sub_score >= 1:
                secondary.append(sub_name)
    
    return {
        "primary": primary,
        "secondary": secondary[:3],
        "scores": scores,
        "matched_keywords": {k: v for k, v in matched.items() if v},
        "method": "poler_themes+v2",
    }


# ═══════════════════════════════════════════════════════════════════════
# ИЗВЛЕЧЕНИЕ КЛЮЧЕВЫХ СЛОВ (TF-IDF, локально)
# ═══════════════════════════════════════════════════════════════════════

_STOPWORDS_EN = set("""
the a an and or but if then for with without to of in on at by from
as is are was were be been being have has had do does did will would
should could may might can this that these those it its their his
her our your my we you they i me him them us not no nor so too very
also just only own same other another such
""".split())

_STOPWORDS_RU = set("""
а о и в к с у от до из на по за про для над под при без между через
что это тот эта эти этот такой такая такие так же как бы ли же не
ни или либо но если чтобы потому затем потом также тоже когда пока
после перед уже еще ещё был была было будут есть нет да можно
нужно надо который которая которое которые их его её мы вы ты он
она оно они я меня мне тебя себе кто что
""".split())

_ALL_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_RU

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_\-']{2,40}")


def extract_keywords_tfidf(text: str, top_n: int = 10) -> List[Tuple[str, float]]:
    """Извлечь ключевые слова через TF-IDF (чистый stdlib).
    
    Returns list of (keyword, score) sorted by score descending.
    """
    # Tokenize
    tokens = []
    for m in _WORD_RE.finditer(text.lower()):
        w = m.group(0)
        if w in _ALL_STOPWORDS:
            continue
        if w.isdigit():
            continue
        tokens.append(w)
    
    if not tokens:
        return []
    
    # Count
    counts = Counter(tokens)
    total = len(tokens)
    
    # Simple TF scoring (IDF approximation for single-doc: rarity bonus)
    scored = []
    for word, count in counts.items():
        tf = count / total
        # Rarity: rarer words get bonus
        rarity = -math.log(max(count / total, 1e-10))
        score = tf * rarity
        scored.append((word, score))
    
    scored.sort(key=lambda x: -x[1])
    return scored[:top_n]


# ═══════════════════════════════════════════════════════════════════════
# POLER CORE — ЯДРО СМЫСЛА
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Vein:
    """Вена — навигационная точка в документе.
    
    Каждая вена = ключевое слово + его ε-энергия + резонанс + контекст.
    Вены связывают документ в навигационную сеть.
    """
    keyword: str
    epsilon: float
    resonance: float
    windows_count: int
    peak_position: int
    context: str = ""  # First 200 chars of top window
    source_file: str = ""


@dataclass
class UnderstandingResult:
    """Результат понимания документа через POLER.
    
    Это не просто «тема» или «ключевые слова». Это полное понимание:
    - Что это за документ (theme)
    - Какие слова несут больше всего смысла (veins)
    - Как фрагменты резонируют друг с другом (resonance_map)
    - Что спросить у документа (suggested_questions)
    """
    theme: str
    secondary_themes: List[str]
    keywords: List[str]
    veins: List[Vein]
    total_epsilon: float
    avg_epsilon: float
    peak_resonance: float
    chars: int
    elapsed_sec: float
    method: str = "poler_core_v2"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme": self.theme,
            "secondary_themes": self.secondary_themes,
            "keywords": self.keywords,
            "veins": [
                {
                    "keyword": v.keyword,
                    "epsilon": round(v.epsilon, 2),
                    "resonance": round(v.resonance, 2),
                    "windows_count": v.windows_count,
                    "peak_position": v.peak_position,
                    "context": v.context[:200],
                    "source_file": v.source_file,
                }
                for v in self.veins
            ],
            "total_epsilon": round(self.total_epsilon, 2),
            "avg_epsilon": round(self.avg_epsilon, 2),
            "peak_resonance": round(self.peak_resonance, 2),
            "chars": self.chars,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "method": self.method,
        }


class PolerCore:
    """POLER Core — Ядро Смысла.
    
    Архитектура:
      grab (сбор) → PolerCore.understand() (смысл) → brief (знание)
    
    Без этого ядра — система собирает данные, но не понимает.
    С этим ядром — данные превращаются в навигационную сеть вен.
    """
    
    def __init__(self, window_size: int = 5000, phi_decay: float = 0.85,
                 top_veins: int = 10):
        self.window_size = window_size
        self.phi_decay = phi_decay
        self.top_veins = top_veins
        self._analyzer = None
        
        if _HAS_POLER:
            self._analyzer = PolerAnalyzer(
                window=window_size, phi=phi_decay, top=top_veins
            )
    
    def understand(self, text: str, source: str = "",
                   max_text: int = 200000) -> UnderstandingResult:
        """Понять документ — превратить текст в смысл.
        
        Args:
            text: Полный текст документа.
            source: Путь к файлу (для контекста).
            max_text: Максимум символов для анализа (для скорости).
            
        Returns:
            UnderstandingResult с темами, венами, ε-энергией.
        """
        t0 = time.time()
        
        if not text or len(text.strip()) < 20:
            return UnderstandingResult(
                theme="empty", secondary_themes=[], keywords=[],
                veins=[], total_epsilon=0, avg_epsilon=0,
                peak_resonance=0, chars=len(text),
                elapsed_sec=time.time() - t0,
            )
        
        # ── 1. ТЕМА: Автообнаружение через словари ──
        theme_result = detect_themes(text)
        primary = theme_result["primary"]
        secondary = theme_result["secondary"]
        
        # ── 2. КЛЮЧЕВЫЕ СЛОВА: TF-IDF ──
        kw_scores = extract_keywords_tfidf(text, top_n=10)
        keywords = [kw for kw, _ in kw_scores]
        
        # ── 3. ВЕНЫ: POLER ε-анализ по ключевым словам темы ──
        veins: List[Vein] = []
        total_epsilon = 0.0
        avg_epsilon = 0.0
        peak_resonance = 0.0
        
        if self._analyzer and len(text) >= 50:
            # Выбираем ключевые слова для вен:
            # а) совпавшие из темы
            # б) топ TF-IDF
            # в) эмоциональные маркеры
            vein_keywords = set()
            
            # Из темы
            matched = theme_result.get("matched_keywords", {})
            for theme_name, kws in matched.items():
                for kw in kws[:5]:
                    vein_keywords.add(kw)
            
            # Из TF-IDF топ
            for kw, _ in kw_scores[:5]:
                vein_keywords.add(kw)
            
            # Ограничиваем текст для скорости
            analysis_text = text[:max_text]
            
            for keyword in list(vein_keywords)[:self.top_veins]:
                try:
                    result = self._analyzer.analyze_text(
                        analysis_text, keyword
                    )
                    summary = result.get("summary")
                    if not summary or summary.get("total_windows", 0) == 0:
                        continue
                    
                    # Top window context
                    top_windows = result.get("top_by_epsilon", [])
                    context = ""
                    peak_pos = 0
                    if top_windows:
                        context = top_windows[0].get("cleaned_text", "")[:200]
                        peak_pos = top_windows[0].get("position", 0)
                    
                    vein = Vein(
                        keyword=keyword,
                        epsilon=summary.get("avg_epsilon", 0),
                        resonance=summary.get("avg_resonance", 0),
                        windows_count=summary.get("total_windows", 0),
                        peak_position=peak_pos,
                        context=context,
                        source_file=source,
                    )
                    veins.append(vein)
                    total_epsilon += summary.get("total_epsilon", 0)
                    
                except Exception as e:
                    sys.stderr.write(f"[poler_core] vein '{keyword}' error: {e}\n")
            
            if veins:
                avg_epsilon = total_epsilon / len(veins)
                peak_resonance = max((v.resonance for v in veins), default=0)
        
        elapsed = time.time() - t0
        
        return UnderstandingResult(
            theme=primary,
            secondary_themes=secondary,
            keywords=keywords,
            veins=veins,
            total_epsilon=total_epsilon,
            avg_epsilon=avg_epsilon,
            peak_resonance=peak_resonance,
            chars=len(text),
            elapsed_sec=elapsed,
        )
    
    def understand_file(self, file_path: str) -> UnderstandingResult:
        """Понять файл — прочитать и проанализировать."""
        text = self._read_file(file_path)
        return self.understand(text, source=file_path)
    
    def _read_file(self, path: str) -> str:
        """Прочитать файл любого поддерживаемого типа."""
        p = Path(path)
        if not p.exists():
            return ""
        
        ext = p.suffix.lower()
        
        # LaTeX: strip markup first
        if ext in ('.tex', '.latex', '.sty', '.cls'):
            raw = self._read_raw(path)
            if _HAS_TOPIC_COMMON:
                return strip_latex(raw)
            return raw
        
        # EPUB
        if ext == '.epub' and _HAS_POLER:
            return read_epub(path)
        
        # Everything else
        return self._read_raw(path)
    
    def _read_raw(self, path: str) -> str:
        """Read raw text with encoding detection."""
        raw = Path(path).read_bytes()
        for enc in ('utf-8', 'cp1251', 'latin-1'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')
    
    def navigate(self, text: str, query: str) -> List[Dict[str, Any]]:
        """Навигация по документу — найти релевантные фрагменты.
        
        Это «рентген»: находим где в тексте концентрируется смысл запроса.
        """
        if not self._analyzer:
            return []
        
        result = self._analyzer.analyze_text(text[:200000], query)
        top = result.get("top_by_epsilon", [])
        
        return [
            {
                "position": w.get("position", 0),
                "epsilon": round(w.get("epsilon", 0), 2),
                "resonance": round(w.get("resonance", 0), 2),
                "context": w.get("cleaned_text", "")[:500],
            }
            for w in top[:5]
        ]


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="POLER Core — Ядро Смысла. Понимает документы через ε-энергию и резонанс.",
    )
    ap.add_argument("file", help="Путь к файлу для анализа")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--navigate", default=None, help="Navigate: найти фрагменты по запросу")
    ap.add_argument("--top-veins", type=int, default=10, help="Сколько вен извлечь")
    args = ap.parse_args()
    
    core = PolerCore(top_veins=args.top_veins)
    
    if args.navigate:
        text = core._read_file(args.file)
        results = core.navigate(text, args.navigate)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"\n{'='*60}")
                print(f"Фрагмент {i}: ε={r['epsilon']}, R={r['resonance']}")
                print(f"Позиция: {r['position']}")
                print(f"Контекст: {r['context'][:200]}...")
    else:
        result = core.understand_file(args.file)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"🎯 Тема: {result.theme}")
            if result.secondary_themes:
                print(f"   Подтемы: {', '.join(result.secondary_themes)}")
            print(f"🔑 Ключевые слова: {', '.join(result.keywords[:7])}")
            print(f"🩸 Вены ({len(result.veins)}):")
            for v in result.veins[:5]:
                print(f"   • {v.keyword}: ε={v.epsilon:.1f}, R={v.resonance:.1f}, "
                      f"окон={v.windows_count}")
            if result.veins:
                print(f"⚡ Суммарная ε: {result.total_epsilon:.1f}")
                print(f"📊 Средняя ε: {result.avg_epsilon:.1f}")
                print(f"🌊 Пиковый резонанс: {result.peak_resonance:.1f}")
            print(f"⏱ Время: {result.elapsed_sec:.2f}s")


if __name__ == "__main__":
    main()
