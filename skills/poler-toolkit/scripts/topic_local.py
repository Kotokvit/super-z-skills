#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
topic_local — версия БЕЗ LLM (без моих внутренних весов).

Чистый Python stdlib + опциональные пакеты если установлены:
  - sklearn (KMeans для кластеризации TF-IDF векторов)
  - numpy

Если sklearn не установлен — fallback на простую word-overlap агломеративную
кластеризацию (O(n²) но для <100 чанков работает быстро).

Алгоритм:
  1. read_text(path) — через poler_v6.read_file (поддержка epub/zip/tar.gz).
  2. is_code(path) — если код, режим B (см. ниже).
  3. split_into_chunks(text) — абзацы по 1500 символов.
  4. Для каждого чанка — TF-IDF по топ-N биграммам/словам.
  5. Кластеризация:
     - sklearn есть: KMeans на TF-IDF, число кластеров = локтевой метод.
     - sklearn нет: агломеративная word-overlap (Jaccard similarity > 0.15).
  6. Тема кластера = топ-3 биграммы по TF-IDF.
  7. Тема документа = топ-5 биграмм по всему тексту.

РЕЖИМ B (код):
  - detect_language(path) → 'Python' / 'Rust' / etc.
  - extract_code_entities(text) → classes/functions/imports/constants.
  - Тема = "Python — основные сущности: Fragment, GrepResult, analyze_directory, ..."

ИСПОЛЬЗОВАНИЕ:
  python topic_local.py FILE [--format text|json] [--n-clusters N]
                              [--max-chunk-size 1500]

ВЫВОД:
  text (default): человекочитаемый, для пользователя/агента
  json:           структурированный, для парсинга агентом

ЗАВИСИМОСТИ:
  Обязательные: только Python 3.7+ stdlib.
  Опциональные: sklearn, numpy (для лучшей кластеризации).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Импортируем общие утилиты
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from topic_common import (
    read_text, is_code, is_code_heuristic, detect_language,
    split_into_chunks, extract_code_entities,
    format_output_human, format_output_json,
)

# Опциональные зависимости
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    import numpy as np
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


# ════════════════════════════════════════════════════════════════════════
# STOPWORDS (RU + EN mixed — без сторонних библиотек)
# ════════════════════════════════════════════════════════════════════════

_STOPWORDS = set("""
а о и в к с у от до из на по за про для над под при без между через
что это тот эта эти этот такой такая такие так же как бы ли же не
ни или либо и но если чтобы потому затем потом также тоже когда пока
после перед уже еще ещё был была было будут есть нет да нет можно
нужно надо который которая которое которые их его её их мы вы ты он
она оно они я меня мне меня тебя тебе себя себе кто что ничто ничег
однако кроме лишь только даже также впрочем например таким образом
итак значит поэтому таким образом то есть т.е т.д т.п etc etc
the a an and or but if then for with without to of in on at by from
as is are was were be been being have has had do does did will would
should could may might can this that these those it its their his
her our your my we you they i me him them us not no nor so too very
also just only own same other another such
""".split())


# ════════════════════════════════════════════════════════════════════════
# TOKENIZATION + TF-IDF (pure stdlib fallback)
# ════════════════════════════════════════════════════════════════════════

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_\-']{1,40}")


def tokenize(text: str) -> List[str]:
    """Токенизация в нижний регистр, фильтр стоп-слов, длина ≥ 3."""
    tokens = []
    for m in _WORD_RE.finditer(text):
        w = m.group(0).lower()
        if len(w) < 3:
            continue
        if w in _STOPWORDS:
            continue
        # Дополнительный фильтр: чисто числовые токены пропускаем
        if w.replace('-', '').replace("'", '').isdigit():
            continue
        tokens.append(w)
    return tokens


def extract_ngrams(tokens: List[str], n: int = 2) -> List[str]:
    """Биграммы из списка токенов."""
    if n == 1:
        return tokens
    return [' '.join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]


def compute_tfidf_keywords(corpus: List[str], top_n: int = 5,
                            ngram: int = 2) -> List[List[Tuple[str, float]]]:
    """Простой TF-IDF на чистом stdlib.

    corpus: список документов (строк). Возвращает для каждого документа
    список (term, score) отсортированный по убыванию score.
    """
    if not corpus:
        return []

    # Токенизация всех документов
    docs_tokens = [tokenize(doc) for doc in corpus]
    docs_ngrams = [extract_ngrams(toks, ngram) for toks in docs_tokens]

    # DF (document frequency) для каждого терма
    df: Dict[str, int] = defaultdict(int)
    for doc_ngrams in docs_ngrams:
        seen = set(doc_ngrams)
        for term in seen:
            df[term] += 1

    N = len(corpus)
    # IDF: логарифмическое
    idf = {term: max(1.0, __import__('math').log((N + 1) / (df_val + 1)) + 1.0)
           for term, df_val in df.items()}

    # TF-IDF per doc
    results = []
    for doc_ngrams in docs_ngrams:
        if not doc_ngrams:
            results.append([])
            continue
        tf = Counter(doc_ngrams)
        scored = [(term, count * idf.get(term, 1.0)) for term, count in tf.items()]
        scored.sort(key=lambda x: -x[1])
        results.append(scored[:top_n])

    return results


# ════════════════════════════════════════════════════════════════════════
# CLUSTERING (pure stdlib fallback: agglomerative Jaccard)
# ════════════════════════════════════════════════════════════════════════

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cluster_agglomerative(chunks: List[str], threshold: float = 0.15,
                           max_clusters: int = 10) -> List[int]:
    """Агломеративная кластеризация по Jaccard similarity на множествах токенов.

    O(n²). Подходит для <200 чанков.
    """
    n = len(chunks)
    if n == 0:
        return []
    if n == 1:
        return [0]

    # Каждое множество = множество токенов
    token_sets = [set(tokenize(c)) for c in chunks]
    # Начальные кластеры: каждый чанк — отдельный
    clusters = [[i] for i in range(n)]

    while len(clusters) > 1:
        # Найти пару кластеров с макс. similarity
        best_sim = 0.0
        best_pair = (-1, -1)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                # Average linkage: средняя Jaccard по всем парам
                sims = []
                for a in clusters[i]:
                    for b in clusters[j]:
                        sims.append(jaccard(token_sets[a], token_sets[b]))
                avg = sum(sims) / len(sims) if sims else 0.0
                if avg > best_sim:
                    best_sim = avg
                    best_pair = (i, j)

        # Если даже макс.相似度 < threshold — стоп
        if best_sim < threshold or len(clusters) <= max_clusters:
            break

        # Merge
        i, j = best_pair
        clusters[i].extend(clusters[j])
        clusters.pop(j)

    # Map chunk index → cluster id
    labels = [0] * n
    for cluster_id, members in enumerate(clusters):
        for m in members:
            labels[m] = cluster_id
    return labels


def cluster_sklearn(chunks: List[str], max_clusters: int = 10) -> List[int]:
    """KMeans + TF-IDF + silhouette для выбора числа кластеров."""
    if not _HAS_SKLEARN:
        return cluster_agglomerative(chunks)
    n = len(chunks)
    if n <= 1:
        return [0] * n

    # TF-IDF features
    vec = TfidfVectorizer(
        tokenizer=tokenize, preprocessor=lambda x: x.lower(),
        token_pattern=None, ngram_range=(1, 2), max_features=2000,
        min_df=1,
    )
    X = vec.fit_transform(chunks)

    # Silhouette для выбора k (если n >= 4)
    best_k = 2
    best_score = -1.0
    max_k = min(max_clusters, n - 1)
    if n >= 4 and max_k >= 2:
        for k in range(2, max_k + 1):
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X)
            try:
                score = silhouette_score(X, labels)
            except Exception:
                score = -1.0
            if score > best_score:
                best_score = score
                best_k = k
    else:
        best_k = min(2, n)

    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    return list(km.fit_predict(X))


# ════════════════════════════════════════════════════════════════════════
# MAIN: detect_topics
# ════════════════════════════════════════════════════════════════════════

def detect_topics(path: str,
                  max_chunk_size: int = 1500,
                  max_clusters: int = 10,
                  n_keywords: int = 3) -> Dict:
    """Главная функция. Возвращает словарь:

    Для текста:
      {
        'is_code': False,
        'path': ...,
        'overall_topic': '... top-5 keywords ...',
        'num_clusters': N,
        'clusters': [
          {'cluster_id': 0, 'size': M, 'topic': '...', 'preview': '...'},
          ...
        ],
        'method': 'tfidf+kmeans' | 'tfidf+agglomerative'
      }

    Для кода:
      {
        'is_code': True,
        'path': ...,
        'language': 'Python',
        'overall_topic': 'Python — основные сущности: ...',
        'entities': {'classes': [...], 'functions': [...], ...}
      }
    """
    text = read_text(path)
    if not text.strip():
        return {'path': path, 'error': 'empty file', 'overall_topic': ''}

    # ─── РЕЖИМ B: КОД ─────────────────────────────────────────────────
    if is_code(text, path):
        lang = detect_language(path, text)
        entities = extract_code_entities(text, lang)
        # Тема кода: язык + топ сущностей
        parts = []
        if entities['classes']:
            parts.append(f"классы: {', '.join(entities['classes'][:5])}")
        if entities['functions']:
            parts.append(f"функции: {', '.join(entities['functions'][:5])}")
        if entities['imports'] and not parts:
            parts.append(f"импорты: {', '.join(entities['imports'][:5])}")
        topic = f"{lang}"
        if parts:
            topic += " — " + "; ".join(parts)
        return {
            'is_code': True,
            'path': path,
            'language': lang,
            'overall_topic': topic,
            'entities': entities,
            'method': 'regex-entities',
        }

    # ─── РЕЖИМ A: ТЕКСТ ───────────────────────────────────────────────
    chunks = split_into_chunks(text, max_chars=max_chunk_size)
    if len(chunks) == 0:
        return {'path': path, 'error': 'no chunks', 'overall_topic': ''}

    # Кластеризация
    if _HAS_SKLEARN and len(chunks) >= 4:
        labels = cluster_sklearn(chunks, max_clusters=max_clusters)
        method = 'tfidf+kmeans+silhouette'
    else:
        labels = cluster_agglomerative(chunks, threshold=0.15,
                                        max_clusters=max_clusters)
        method = 'tfidf+agglomerative-jaccard'

    # Группировка чанков по кластерам
    clusters_map: Dict[int, List[str]] = defaultdict(list)
    for chunk, label in zip(chunks, labels):
        clusters_map[label].append(chunk)

    # TF-IDF по всему корпусу для тем кластеров
    cluster_texts = [' '.join(chunks_list) for chunks_list in clusters_map.values()]
    cluster_keywords = compute_tfidf_keywords(cluster_texts + [text],
                                              top_n=n_keywords, ngram=2)
    cluster_kws = cluster_keywords[:-1]  # последний — весь текст
    overall_kws = cluster_keywords[-1]

    clusters_result = []
    for cluster_id, (chunks_list, kws) in enumerate(zip(clusters_map.values(),
                                                          cluster_kws)):
        topic_str = ', '.join(kw for kw, _ in kws) if kws else '(нет явной темы)'
        preview = chunks_list[0][:300] if chunks_list else ''
        clusters_result.append({
            'cluster_id': cluster_id,
            'size': len(chunks_list),
            'topic': topic_str,
            'preview': preview,
        })

    overall_topic = ', '.join(kw for kw, _ in overall_kws) if overall_kws else '(тема не определена)'

    return {
        'is_code': False,
        'path': path,
        'overall_topic': overall_topic,
        'num_clusters': len(clusters_result),
        'clusters': clusters_result,
        'method': method,
        'total_chunks': len(chunks),
    }


# ════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Локальное определение темы документа/кода (без LLM).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python topic_local.py document.md
  python topic_local.py script.py --format json
  python topic_local.py book.epub --max-chunk-size 2000
  python topic_local.py archive.zip --format json > topics.json
        """,
    )
    parser.add_argument('file', help='Путь к файлу (.txt/.md/.epub/.zip/.tar.gz/.py/.rs/...)')
    parser.add_argument('--format', choices=['text', 'json'], default='text',
                        help='Формат вывода (text по умолчанию)')
    parser.add_argument('--max-chunk-size', type=int, default=1500,
                        help='Макс. символов на чанк (default: 1500)')
    parser.add_argument('--max-clusters', type=int, default=10,
                        help='Макс. число кластеров (default: 10)')
    parser.add_argument('--n-keywords', type=int, default=3,
                        help='Сколько ключевых фраз на кластер (default: 3)')
    args = parser.parse_args()

    result = detect_topics(
        args.file,
        max_chunk_size=args.max_chunk_size,
        max_clusters=args.max_clusters,
        n_keywords=args.n_keywords,
    )

    if args.format == 'json':
        print(format_output_json(result))
    else:
        print(format_output_human(result))
        # Method note
        if not result.get('is_code'):
            method = result.get('method', '?')
            sklearn_note = ' (sklearn доступен)' if _HAS_SKLEARN else ' (sklearn НЕ доступен — fallback)'
            print(f"\n[метод: {method}{sklearn_note}]")


if __name__ == '__main__':
    main()
