#!/usr/bin/env python3
"""
address_resolver.py
===================
Path D, Шаг 2: AddressResolver — инвертированный токенизатор.

Концепция:
  Классический токенизатор:  text → token IDs → embedding lookup
  Инвертированный:           query → meaning atoms → weight addresses

Вход:
  - текстовый запрос пользователя
  - work/analysis/weight_db/reverse_index.json (построен на шаге 1)
  - work/analysis/weight_db/meaning_index.json
  - work/analysis/poler_eri_out/archetypes.json

Выход:
  - список weight addresses (tensor names + byte offsets)
  - вектор состояния p (dim=10 архетипов)
  - p' = Π[J-D]Π·p  (результат SCTP, упрощённая версия на Python+numpy)

Слои:
  LENS-LITE:     TF-IDF inverted index по name atoms + bigrams
  SCTP-LITE:     один шаг Π[J-D]Π над dim=10 вектором p
  ADDRESS-RS:    p' → top-k архетипов → top-k tensor addresses
"""

import json
import re
import math
import time
from collections import defaultdict, Counter
from pathlib import Path
import numpy as np

# ============ КОНСТАНТЫ ============
BASE = Path("/home/z/my-project")
WORK = BASE / "work"
ANALYSIS = WORK / "analysis"
WEIGHT_DB = ANALYSIS / "weight_db"
ERI_OUT = ANALYSIS / "poler_eri_out"

NAME_PATTERN = re.compile(r"[._\-/\s]+")


class AddressResolver:
    """
    Инвертированный токенизатор + AddressResolver.
    Singleton — загружает индекс один раз.
    """

    def __init__(self):
        print("[AddressResolver] Загрузка индексов...", end=" ")
        t0 = time.time()
        with open(WEIGHT_DB / "reverse_index.json") as f:
            self.reverse = json.load(f)
        with open(WEIGHT_DB / "meaning_index.json") as f:
            self.meaning = json.load(f)
        with open(WEIGHT_DB / "address_table.json") as f:
            self.addresses = json.load(f)
        with open(ERI_OUT / "archetypes.json") as f:
            self.archetypes = json.load(f)["archetypes"]

        # TF-IDF: doc_freq для каждого атома/биграммы
        self.df_atom = Counter()
        self.df_bigram = Counter()
        for name, meta in self.meaning.items():
            for a in set(meta["atoms"]):
                self.df_atom[a] += 1
            for b in set(meta["bigrams"]):
                self.df_bigram[b] += 1
        self.N = len(self.meaning)

        # Матрицы SCTP: J (antisymmetric resonance), D (dissipation)
        # Используем sparse graph как источник направленного потока (J),
        # а similarity_matrix между архетипами — как D (симметричная диссипация).
        with open(ERI_OUT / "archetypes.json") as f:
            arch_data = json.load(f)
        sim_matrix = np.array(arch_data.get("similarity_matrix", []))
        if sim_matrix.size == 0:
            sim_matrix = np.eye(10)
        # D = симметричная часть similarity (без диагонали)
        sym = (sim_matrix + sim_matrix.T) / 2.0
        np.fill_diagonal(sym, 0)
        self.D = sym

        # J строим из ко-активации тегов между архетипами + разницы в "физике".
        # Direction: от большего архетипа к меньшему (по суммарной "массе" numel).
        # Magnitude: количество общих тегов.
        # Это даёт ненулевой J даже без explicit sparse graph между архетипами.
        self.J = np.zeros((10, 10))
        arch_stats = {}
        for name, meta in self.meaning.items():
            a = meta["archetype_id"]
            if a < 0:
                continue
            if a not in arch_stats:
                arch_stats[a] = {"count": 0, "tags": set(), "numel_sum": 0, "eps_sum": 0}
            arch_stats[a]["count"] += 1
            arch_stats[a]["tags"].update(meta.get("tags", []))
            # numel берём из address_table
            addr = self.addresses.get(name, {})
            arch_stats[a]["numel_sum"] += addr.get("numel", 0)
            arch_stats[a]["eps_sum"] += meta.get("epsilon", 0)

        # Средняя "масса" архетипа = средний numel
        arch_mass = {}
        for a, s in arch_stats.items():
            arch_mass[a] = s["numel_sum"] / s["count"] if s["count"] else 0

        # J[A,B] = sign(mass_A - mass_B) * |common_tags|  (антисимметрично по построению)
        for a in range(10):
            for b in range(10):
                if a == b or a not in arch_stats or b not in arch_stats:
                    continue
                common = len(arch_stats[a]["tags"] & arch_stats[b]["tags"])
                if common == 0:
                    continue
                # направление: от тяжёлого к лёгкому (mass flow)
                if arch_mass[a] > arch_mass[b]:
                    self.J[a, b] += common * 0.1
                    self.J[b, a] -= common * 0.1
                elif arch_mass[a] < arch_mass[b]:
                    self.J[b, a] += common * 0.1
                    self.J[a, b] -= common * 0.1

        j_max = np.abs(self.J).max()
        if j_max > 0:
            self.J = self.J / j_max

        # Π = identity (пока простая проекция, без понижения размерности)
        self.Pi = np.eye(10)
        self.A = 10  # число архетипов
        j_nonzero = (self.J != 0).sum() // 2  # антисимметрично — пары
        print(f"  SCTP: J построен из ко-активации тегов, "
              f"non-zero pairs={j_nonzero}, "
              f"||J||={np.linalg.norm(self.J):.3f}, ||D||={np.linalg.norm(self.D):.3f}")

        print(f"OK ({time.time() - t0:.2f}s, {self.N} тензоров)")

    # ============ LENS-LITE: TF-IDF + GRAPH EXPANSION ============
    def _tokenize(self, query: str) -> list:
        atoms = [a for a in NAME_PATTERN.split(query.lower()) if a and len(a) > 1]
        bigrams = [f"{atoms[i]}_{atoms[i+1]}" for i in range(len(atoms) - 1)]
        return atoms, bigrams

    def _tfidf_score(self, query_atoms, query_bigrams) -> dict:
        """Возвращает {tensor_name: tfidf_score}"""
        scores = defaultdict(float)
        for atom in query_atoms:
            if atom in self.reverse["by_atom"]:
                df = self.df_atom.get(atom, 1)
                idf = math.log(self.N / df)
                for name in self.reverse["by_atom"][atom]:
                    scores[name] += idf
        for bigram in query_bigrams:
            if bigram in self.reverse["by_bigram"]:
                df = self.df_bigram.get(bigram, 1)
                idf = math.log(self.N / df) * 1.5  # биграмма весомее
                for name in self.reverse["by_bigram"][bigram]:
                    scores[name] += idf
        return dict(scores)

    def lens_query(self, query: str, top_k: int = 5) -> dict:
        """
        LENS-слой: текстовый запрос → top-k tensor pointers + archetype distribution.
        """
        atoms, bigrams = self._tokenize(query)
        scores = self._tfidf_score(atoms, bigrams)

        if not scores:
            return {
                "query": query,
                "atoms": atoms,
                "bigrams": bigrams,
                "hits": [],
                "p": [0.0] * self.A,
                "verdict": "NO_HITS",
            }

        # top-k тензоров
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        hits = []
        for name, score in ranked:
            meta = self.meaning.get(name, {})
            hits.append({
                "name": name,
                "score": score,
                "archetype_id": meta.get("archetype_id", -1),
                "epsilon": meta.get("epsilon", 0),
                "role": meta.get("role", "unknown"),
            })

        # распределение по архетипам = вектор p (raw, до нормализации)
        p_raw = np.zeros(self.A)
        for h in hits:
            if h["archetype_id"] >= 0:
                p_raw[h["archetype_id"]] += h["score"]
        # нормализация softmax для стабильности
        if p_raw.sum() > 0:
            p = self._softmax(p_raw)
        else:
            p = np.zeros(self.A)

        # графовое расширение: добавить топ-3 соседа из sparse graph
        expanded = set(h["name"] for h in hits)
        for h in hits:
            neighbors = self.meaning.get(h["name"], {}).get("neighbors", [])
            for n in neighbors[:3]:  # top-3 соседа
                expanded.add(n["neighbor"])
        # добавим расширенные хиты в результат (без score, как graph expansion)
        expanded_hits = []
        for name in expanded:
            if name not in scores:
                meta = self.meaning.get(name, {})
                expanded_hits.append({
                    "name": name,
                    "score": 0.0,
                    "archetype_id": meta.get("archetype_id", -1),
                    "epsilon": meta.get("epsilon", 0),
                    "role": meta.get("role", "unknown"),
                    "via_graph": True,
                })

        return {
            "query": query,
            "atoms": atoms,
            "bigrams": bigrams,
            "hits": hits,
            "graph_expansion": expanded_hits,
            "p": p.tolist(),
            "verdict": "OK" if hits else "NO_HITS",
        }

    # ============ SCTP-LITE: Π[J-D]Π·p ============
    def sctp_step(self, p: list, alpha: float = 1.0) -> dict:
        """
        SCTP-слой: p → p' = Π[J-D]Π·p
        Возвращает p' + активированные архетипы.
        """
        p_vec = np.array(p, dtype=np.float64)
        # оператор S = J - D
        S = self.J * alpha - self.D * (1 - alpha)  # α=1: чистый резонанс
        # проекция Π
        p_prime = self.Pi @ (S @ (self.Pi @ p_vec))
        # нормализация чтобы p' был в [0,1] и суммировался к 1
        p_prime = p_prime - p_prime.min()
        if p_prime.sum() > 0:
            p_prime = p_prime / p_prime.sum()
        else:
            p_prime = np.zeros_like(p_prime)

        # какие архетипы "зажглись" в p'
        activated = []
        for i, val in enumerate(p_prime):
            if val > 0.05:  # порог
                activated.append({
                    "archetype_id": i,
                    "activation": float(val),
                    "name": self.archetypes[i]["archetype_name"] if i < len(self.archetypes) else f"ARCH_{i}",
                })
        activated.sort(key=lambda x: -x["activation"])

        # δ = p' - p — насколько SCTP изменил состояние
        delta = p_prime - p_vec
        # топ-3 архетипа, которые усилились (positive resonance)
        delta_top = []
        for i, d in enumerate(delta):
            if d > 0.001:
                delta_top.append({
                    "archetype_id": i,
                    "name": self.archetypes[i]["archetype_name"] if i < len(self.archetypes) else f"ARCH_{i}",
                    "delta": float(d),
                })
        delta_top.sort(key=lambda x: -x["delta"])

        return {
            "p_prime": p_prime.tolist(),
            "p": p_vec.tolist(),
            "delta": delta.tolist(),
            "delta_top": delta_top[:5],
            "activated_archetypes": activated,
            "operator_norm": float(np.linalg.norm(S)),
            "energy": float(np.dot(p_vec, p_prime)),
            "delta_l2": float(np.linalg.norm(delta)),
        }

    # ============ ADDRESS RESOLVER: p' → tensor addresses ============
    def resolve_addresses(self, sctp_result: dict, lens_result: dict,
                          top_archetypes: int = 3, top_per_archetype: int = 5) -> dict:
        """
        Берёт p' + lens hits → собирает финальный список weight addresses.
        """
        activated = sctp_result["activated_archetypes"][:top_archetypes]
        # все lens hits (TF-IDF + graph expansion)
        all_lens_hits = lens_result["hits"] + lens_result.get("graph_expansion", [])

        # Собираем тензоры из топ-N архетипов
        # + union с lens hits (чтобы сохранить точные находки)
        resolved = {}
        for hit in all_lens_hits:
            name = hit["name"]
            if name in self.addresses:
                meta = self.meaning.get(name, {})
                resolved[name] = {
                    "address": self.addresses[name],
                    "meaning": meta,
                    "source": "lens",
                    "lens_score": hit.get("score", 0),
                }

        # добавляем топ-N тензоров из каждого активированного архетипа
        for arch in activated:
            arch_id = arch["archetype_id"]
            arch_name = arch["name"]
            # все тензоры этого архетипа
            tensors_in_arch = [
                (n, m) for n, m in self.meaning.items()
                if m.get("archetype_id") == arch_id
            ]
            # сортируем по ε (более редкие = более интересны)
            tensors_in_arch.sort(key=lambda x: -x[1].get("epsilon", 0))
            for name, meta in tensors_in_arch[:top_per_archetype]:
                if name in self.addresses and name not in resolved:
                    resolved[name] = {
                        "address": self.addresses[name],
                        "meaning": meta,
                        "source": f"sctp:arch_{arch_id}({arch_name})",
                        "activation": arch["activation"],
                    }

        # финальная статистика
        total_bytes = sum(r["address"]["byte_length"] for r in resolved.values())
        total_numel = sum(r["address"]["numel"] for r in resolved.values())
        model_bytes = next(iter(self.addresses.values()))["file_size"]

        return {
            "addresses": list(resolved.values()),
            "address_count": len(resolved),
            "total_bytes": total_bytes,
            "total_numel": total_numel,
            "model_bytes": model_bytes,
            "sparsity_ratio": total_bytes / model_bytes if model_bytes else 0,
            "activated_archetypes": activated,
        }

    # ============ FULL PIPELINE ============
    def resolve(self, query: str, alpha: float = 1.0,
                top_archetypes: int = 3, top_per_archetype: int = 5) -> dict:
        """
        Полный pipeline: query → LENS → SCTP → AddressResolver
        """
        t0 = time.time()
        lens = self.lens_query(query, top_k=5)
        t1 = time.time()
        sctp = self.sctp_step(lens["p"], alpha=alpha) if lens["verdict"] == "OK" else {
            "p_prime": lens["p"],
            "activated_archetypes": [],
            "operator_norm": 0,
            "energy": 0,
        }
        t2 = time.time()
        resolved = self.resolve_addresses(sctp, lens, top_archetypes, top_per_archetype)
        t3 = time.time()

        return {
            "query": query,
            "timing_ms": {
                "lens": (t1 - t0) * 1000,
                "sctp": (t2 - t1) * 1000,
                "resolve": (t3 - t2) * 1000,
                "total": (t3 - t0) * 1000,
            },
            "lens": lens,
            "sctp": sctp,
            "resolved": resolved,
        }

    @staticmethod
    def _softmax(x, beta=1.5):
        x = np.array(x)
        if x.sum() <= 0:
            return np.zeros_like(x)
        x = x * beta
        x = x - x.max()
        e = np.exp(x)
        return e / e.sum()


# ============ CLI ============
if __name__ == "__main__":
    import sys

    resolver = AddressResolver()
    print()
    print("=" * 70)
    print("  ADDRESS RESOLVER — Path D, Шаг 2")
    print("  Тестовый smoke test на 3 запросах")
    print("=" * 70)

    queries = [
        "attention q k v projection",
        "audio convolution filter encoder",
        "feedforward mlp gate up down",
    ] if len(sys.argv) < 2 else [" ".join(sys.argv[1:])]

    for q in queries:
        print(f"\n{'─' * 70}")
        print(f"  ЗАПРОС: {q}")
        print(f"{'─' * 70}")
        result = resolver.resolve(q)

        print(f"\n[LENS] ({result['timing_ms']['lens']:.1f} ms)")
        print(f"  atoms: {result['lens']['atoms']}")
        print(f"  bigrams: {result['lens']['bigrams'][:5]}...")
        print(f"  verdict: {result['lens']['verdict']}")
        if result['lens']['hits']:
            print(f"  TF-IDF hits ({len(result['lens']['hits'])}):")
            for h in result['lens']['hits'][:5]:
                print(f"    {h['name']:60s}  score={h['score']:.2f}  arch={h['archetype_id']}")
            print(f"  Graph expansion: +{len(result['lens'].get('graph_expansion', []))} тензоров")

        print(f"\n[SCTP] ({result['timing_ms']['sctp']:.1f} ms)")
        print(f"  operator norm: {result['sctp']['operator_norm']:.4f}")
        print(f"  energy: {result['sctp']['energy']:.4f}")
        print(f"  ||δ|| = ||p'-p||: {result['sctp']['delta_l2']:.4f}")
        if result['sctp']['delta_top']:
            print(f"  Резонансные сдвиги (top-5 усиленных архетипов):")
            for d in result['sctp']['delta_top'][:5]:
                print(f"    [{d['archetype_id']:2d}] {d['name']:30s}  Δ={d['delta']:+.4f}")
        if result['sctp']['activated_archetypes']:
            print(f"  Активированные архетипы (top по |p'|):")
            for a in result['sctp']['activated_archetypes'][:5]:
                print(f"    [{a['archetype_id']:2d}] {a['name']:30s}  activation={a['activation']:.4f}")

        print(f"\n[ADDRESSES] ({result['timing_ms']['resolve']:.1f} ms)")
        r = result['resolved']
        print(f"  Резолвлено тензоров: {r['address_count']}")
        print(f"  Total bytes: {r['total_bytes']:,} / {r['model_bytes']:,} "
              f"(sparsity={r['sparsity_ratio']*100:.4f}%)")
        print(f"  Total numel: {r['total_numel']:,}")
        print(f"  Sample addresses:")
        for addr in r['addresses'][:3]:
            print(f"    {addr['address']['file']}:{addr['address']['byte_offset']}-"
                  f"{addr['address']['byte_offset'] + addr['address']['byte_length']} "
                  f"[{addr['address']['dtype']} {addr['address']['shape']}]")
            print(f"      via {addr['source']}")

        print(f"\n[ИТОГО] {result['timing_ms']['total']:.1f} ms")
