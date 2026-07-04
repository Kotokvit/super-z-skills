#!/usr/bin/env python3
"""
POLER Lens — одноразовая проекция запроса на разреженный статический граф.

Идея:
  Запуск LLM = 800 000 строк attention, генерация, галлюцинации, GPU.
  POLER Lens = один проход запроса по 3 019 рёбрам, детерминированно, CPU.

Запрос → TF-IDF скорая схема над именами тензоров →
  → топ-K тензоров-якорей →
  → расширение через разреженный граф (top-5 соседей каждого якоря) →
  → агрегация по архетипам →
  → детерминированный ответ (без генерации).

Это и есть «попиздеть с линзой»: вопрос задаёт структуру, линза
возвращает указатели на конкретные тензоры, которые уже лежат в
исходной модели. Никакого выдумывания.
"""

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ANALYSIS_DIR = Path("/home/z/my-project/work/analysis")
ERI_DIR = ANALYSIS_DIR / "poler_eri_out"
TOOLKIT_DIR = ANALYSIS_DIR / "toolkit_out"

ARCHETYPES_PATH = ERI_DIR / "archetypes.json"
EDGES_PATH = ERI_DIR / "sparse_graph_edges.txt"
STATS_PATH = ANALYSIS_DIR / "tensors_stats.json"
CLUSTERS_PATH = TOOLKIT_DIR / "clusters.json"

TOP_K_ANCHORS = 5      # сколько тензоров-якорей брать из TF-IDF
TOP_K_NEIGHBORS = 3    # сколько соседей из графа на каждый якорь
TOP_K_FINAL = 12       # сколько финальных «источников» в ответе линзы


# ---------- 1. Граф и архив с прототипами ----------

def load_edges(path):
    """source \t target \t weight → adjacency dict."""
    adj = defaultdict(list)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            src, dst, w = parts
            w = float(w)
            adj[src].append((dst, w))
            adj[dst].append((src, w))  # граф неориентированный
    # отсортировать соседей по убыванию веса
    for k in adj:
        adj[k].sort(key=lambda x: -x[1])
    return adj


def load_archetypes(path):
    with open(path) as f:
        data = json.load(f)
    arches = data["archetypes"]
    by_id = {a["id"]: a for a in arches}
    return by_id


def load_clusters_full(path):
    """Загрузить ПОЛНОЕ распределение 611 тензоров по архетипам.

    Возвращает:
      tensor_to_arch: {tensor_name: cluster_id}
      tensor_to_arch_name: {tensor_name: archetype_name (CONV_BIAS, ATTN_PROJ...)}
      cluster_id_to_name: {cluster_id: archetype_name}
    """
    with open(path) as f:
        data = json.load(f)
    names = data["names"]
    labels = data["labels"]
    archetypes_map = data.get("archetypes", {})

    tensor_to_arch = {}
    tensor_to_arch_name = {}
    cluster_id_to_name = {}

    for name, lbl in zip(names, labels):
        tensor_to_arch[name] = lbl
        info = archetypes_map.get(name, {})
        arch_name = info.get("archetype", f"CLUSTER_{lbl}")
        tensor_to_arch_name[name] = arch_name
        if lbl not in cluster_id_to_name:
            cluster_id_to_name[lbl] = arch_name

    return tensor_to_arch, tensor_to_arch_name, cluster_id_to_name


def load_stats(path):
    with open(path) as f:
        data = json.load(f)
    # data — это dict { tensor_name: stats_dict }
    if isinstance(data, dict):
        return data
    # fallback: list of dicts with "name"
    return {t["name"]: t for t in data}


def load_clusters(path):
    with open(path) as f:
        return json.load(f)


# ---------- 2. Токенизация без токенизатора ----------

TOKEN_SPLIT = re.compile(r"[._\-/\s]+")

def tokenize_name(name):
    """Разбить имя тензора на «атомы» без какого-либо токенизатора.

    Пример: model.audio_tower.layers.0.self_attn.q_proj.weight
      → [model, audio, tower, layers, 0, self, attn, q, proj, weight]
    """
    raw = TOKEN_SPLIT.split(name.lower())
    # отрезать расширения и singleton-ы
    atoms = [r for r in raw if r and r not in {"weight", "bias"}]
    # биграммы для устойчивости
    bigrams = [f"{a}_{b}" for a, b in zip(atoms, atoms[1:])]
    return atoms + bigrams


def build_corpus_index(stats):
    """Построить inverted index: token → list of (tensor_name, tf).

    stats — dict {tensor_name: stats_dict}. Имя берём из ключа.
    """
    inv = defaultdict(list)
    doc_tokens = {}
    doc_len = {}
    for name, t in stats.items():
        toks = tokenize_name(name)
        tf = Counter(toks)
        doc_tokens[name] = tf
        doc_len[name] = sum(tf.values())
        for tok, c in tf.items():
            inv[tok].append((name, c))
    return inv, doc_tokens, doc_len


def idf(inv, n_docs):
    """Стандартная IDF: ln((N+1)/(df+1)) + 1."""
    return {tok: math.log((n_docs + 1) / (len(lst) + 1)) + 1
            for tok, lst in inv.items()}


# ---------- 3. Сама проекция запроса ----------

def project_query(query, inv, idf_tab, doc_tokens, doc_len, top_k=TOP_K_ANCHORS):
    """Проецируем запрос на корпус имён тензоров, получаем топ-K якорей.

    Запрос тоже разбиваем «без токенизатора» — те же правила, что для имён.
    Никаких BPE, никаких SentencePiece — голая строковая механика.
    """
    q_tokens = tokenize_name(query)
    # сумма tf-idf по документам
    scores = defaultdict(float)
    q_counter = Counter(q_tokens)
    for tok, qtf in q_counter.items():
        if tok not in inv:
            continue
        idf_val = idf_tab[tok]
        for name, dtf in inv[tok]:
            # tf-idf вес: бём простой произведение
            scores[name] += qtf * dtf * idf_val * idf_val
    # нормировать на длину документа
    for name in list(scores.keys()):
        L = doc_len.get(name, 1)
        scores[name] /= (1 + math.log(L))
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return ranked, q_tokens


def expand_through_lens(anchors, adj, top_k_neighbors=TOP_K_NEIGHBORS):
    """Каждый якорь → его top-k соседей из 3 019-рёберного графа.

    Это и есть фазовый переход: вместо того чтобы «думать» над запросом,
    мы просто подсвечиваем тех, кто уже связан с якорем в исходной
    структуре модели.
    """
    expanded = []
    seen = set()
    for name, score in anchors:
        if name not in seen:
            expanded.append((name, score, "anchor"))
            seen.add(name)
        for nb, w in adj.get(name, [])[:top_k_neighbors]:
            if nb in seen:
                continue
            # вес соседа = вес якоря × ребро (0..1)
            expanded.append((nb, score * w, "lens"))
            seen.add(nb)
    return expanded


def aggregate_by_archetype(expanded, tensor_to_arch, archetypes):
    """Считаем суммарный «свет» по архетипам."""
    by_arch = defaultdict(lambda: {"score": 0.0, "members": []})
    for name, score, kind in expanded:
        arch_id = tensor_to_arch.get(name, -1)
        by_arch[arch_id]["score"] += score
        by_arch[arch_id]["members"].append((name, score, kind))
    # отсортировать
    ranked = sorted(by_arch.items(), key=lambda x: -x[1]["score"])
    return ranked


# ---------- 4. Ответ линзы ----------

def format_lens_answer(query, q_tokens, anchors, expanded, by_arch, stats,
                       archetypes_by_id, t2a, t2a_name, cluster_id_to_name):
    lines = []
    lines.append("=" * 72)
    lines.append(f"  ЗАПРОС:  {query}")
    lines.append(f"  Атомы:   {q_tokens}")
    lines.append("=" * 72)
    lines.append("")

    # 4.1 — Топ-3 архетипа
    lines.append("▸ ФАЗА 1 — проекция на архетипы (какие «грани кристалла» зажглись)")
    lines.append("")
    for arch_id, info in by_arch[:3]:
        if arch_id == -1:
            arch_name = "(вне архетипа)"
            eps = "—"
        else:
            arch_name = cluster_id_to_name.get(arch_id, f"CLUSTER_{arch_id}")
            arch_info = archetypes_by_id.get(arch_id, {})
            eps = f"{arch_info.get('eps_mean', float('nan')):.3f}"
            self_cos = arch_info.get('self_cosine', float('nan'))
        lines.append(f"  Archetype #{arch_id}  {arch_name}  ε={eps}")
        lines.append(f"     суммарный «свет»: {info['score']:.4f}")
        lines.append(f"     зажжённых тензоров: {len(info['members'])}")
        if arch_id != -1:
            lines.append(f"     self-cosine (a⊗_ε a = a): {self_cos:.6f}")
        # показать топ-3 члена
        for name, sc, kind in sorted(info['members'], key=lambda x: -x[1])[:3]:
            tag = "ЯКОРЬ" if kind == "anchor" else "линза"
            lines.append(f"       [{tag}] {name}  (score={sc:.4f})")
        lines.append("")

    # 4.2 — Топ-K финальных источников
    lines.append("▸ ФАЗА 2 — топ-12 «источников» (без генерации, только указатели)")
    lines.append("")
    final = sorted(expanded, key=lambda x: -x[1])[:12]
    for i, (name, score, kind) in enumerate(final, 1):
        st = stats.get(name, {})
        mean = st.get("mean", float('nan'))
        std = st.get("std", float('nan'))
        spar = st.get("sparsity", float('nan'))
        arch_id = t2a.get(name, -1)
        arch_name = t2a_name.get(name, "—" if arch_id == -1 else cluster_id_to_name.get(arch_id, f"CLUSTER_{arch_id}"))
        tag = "ЯКОРЬ" if kind == "anchor" else "линза"
        lines.append(
            f"  {i:2d}. [{tag}] {name}"
        )
        lines.append(
            f"      arch={arch_name}  mean={mean:+.4e}  std={std:.4e}  spar={spar:.3f}"
        )
        lines.append(f"      score={score:.4f}")
    lines.append("")

    # 4.3 — Детерминированный вердикт
    lines.append("▸ ВЕРДИКТ ЛИНЗЫ")
    lines.append("")
    if not by_arch:
        lines.append("  Кристалл ничего не зажёг — запрос не отразился ни в одном архетипе.")
    else:
        top_arch_id, top_info = by_arch[0]
        if top_arch_id == -1:
            lines.append("  Доминирует «вне-архетипная» зона — запрос попал в шум.")
        else:
            arch_name = cluster_id_to_name.get(top_arch_id, f"CLUSTER_{top_arch_id}")
            arch_info = archetypes_by_id.get(top_arch_id, {})
            lines.append(f"  Доминирующий архетип: #{top_arch_id}  {arch_name}")
            lines.append(f"  ε-редкость этого архетипа: {arch_info.get('eps_mean', float('nan')):.3f}")
            lines.append(f"  Идемпотентность a ⊗_ε a = a: self-cosine = {arch_info.get('self_cosine', float('nan')):.6f}")
            lines.append(f"  Зажжено тензоров: {len(top_info['members'])}")
            lines.append("")
            lines.append("  Это не «ответ» в смысле LLM — это указатель на")
            lines.append("  конкретные тензоры в model.safetensors, которые")
            lines.append("  структурно соответствуют запросу. Открыть их —")
            lines.append("  значит открыть готовые, уже обученные данные.")
    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------- 5. main ----------

def main():
    if len(sys.argv) < 2:
        print("Usage: lens_query.py '<запрос>' [--json]")
        sys.exit(1)
    query = sys.argv[1]
    as_json = "--json" in sys.argv

    # Загрузка
    adj = load_edges(EDGES_PATH)
    archetypes_by_id = load_archetypes(ARCHETYPES_PATH)
    t2a, t2a_name, cluster_id_to_name = load_clusters_full(CLUSTERS_PATH)
    stats = load_stats(STATS_PATH)
    inv, doc_tokens, doc_len = build_corpus_index(stats)
    idf_tab = idf(inv, len(stats))

    # Проекция
    anchors, q_tokens = project_query(query, inv, idf_tab, doc_tokens, doc_len)
    expanded = expand_through_lens(anchors, adj)
    by_arch = aggregate_by_archetype(expanded, t2a, archetypes_by_id)

    if as_json:
        out = {
            "query": query,
            "atoms": q_tokens,
            "anchors": [{"name": n, "score": s} for n, s in anchors],
            "expanded_top": [
                {"name": n, "score": s, "kind": k}
                for n, s, k in sorted(expanded, key=lambda x: -x[1])[:TOP_K_FINAL]
            ],
            "archetype_ranks": [
                {
                    "archetype_id": aid,
                    "archetype_name": cluster_id_to_name.get(aid) if aid != -1 else None,
                    "epsilon_mean": archetypes_by_id[aid]["eps_mean"] if aid in archetypes_by_id else None,
                    "self_cosine": archetypes_by_id[aid]["self_cosine"] if aid in archetypes_by_id else None,
                    "score": info["score"],
                    "n_lit": len(info["members"]),
                }
                for aid, info in by_arch
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(format_lens_answer(query, q_tokens, anchors, expanded, by_arch,
                                  stats, archetypes_by_id, t2a, t2a_name,
                                  cluster_id_to_name))


if __name__ == "__main__":
    main()
