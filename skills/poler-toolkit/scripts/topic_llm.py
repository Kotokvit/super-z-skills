#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
topic_llm — версия С интеграцией моих внутренних весов (LLM через z-ai CLI).

ИСПОЛЬЗУЕТ:
  - z-ai chat CLI (z-ai-web-dev-sdk) — внешний API, но НЕТ сторонних Python-пакетов.
  - topic_common.py для чтения файла, чанкинга, детекта кода.

ОТЛИЧИЯ от topic_local.py:
  - Тема кластера — это не список ключевых фраз, а ОДНО естественное предложение
    (например "Q-обучение и функция вознаграждения" вместо "Q-обучение, функция, вознаграждение").
  - Тема документа — это одна-две фразы человеческим языком.
  - Для кода — краткое описание назначения ("Анализатор текста с ε-редкостью,
    кластеризацией фрагментов и grep по архивам") вместо списка имён функций.
  - Понимает семантику, а не просто частоты слов. Например, текст про "капибары и
    нутрии" может быть определён как "сравнительная биология грызунов", а не как
    "капибары, нутрии, грызуны".

АЛГОРИТМ:
  1. read_text(path) — через poler_v6.read_file (epub/zip/tar.gz).
  2. is_code(path) — если код, режим B (см. ниже).
  3. split_into_chunks(text) — абзацы по 1500 символов.
  4. Для каждого чанка — TF-IDF по топ-N биграммам/словам.
  5. Кластеризация (sklearn KMeans или agglomerative fallback).
  6. Для каждого кластера — отправка в LLM:
       "Вот N фрагментов из документа. Назови тему в 1-5 словах."
  7. Для всего документа — отдельный LLM-запрос на общую тему.

РЕЖИМ B (код):
  - detect_language(path) → 'Python' / 'Rust' / etc.
  - LLM получает первые 2000 символов кода + entities (classes/functions/imports).
  - LLM возвращает: "Назначение: ..." одной фразой.

ИСПОЛЬЗОВАНИЕ:
  python topic_llm.py FILE [--format text|json] [--max-chunk-size 1500]
                           [--max-clusters 10] [--max-chunks-per-cluster 3]
                           [--model MODE] [--verbose]

ВЫВОД:
  text (default): человекочитаемый, для пользователя/агента
  json:           структурированный, для парсинга агентом

ОГРАНИЧЕНИЯ:
  - Каждый LLM-запрос ≈ 1-3 секунды + сетевой запрос. Для 10 кластеров → ~30 сек.
  - Если z-ai CLI не доступен → fallback на topic_local (TF-IDF keywords).
  - Большие файлы: LLM получает только sample (первые 2000 символов кода,
    первые 3 чанка по 1500 символов из кластера).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
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
# Переиспользуем кластеризацию и TF-IDF из topic_local
from topic_local import (
    tokenize, cluster_sklearn, cluster_agglomerative, _HAS_SKLEARN,
)


# ════════════════════════════════════════════════════════════════════════
# LLM CLIENT (через z-ai CLI)
# ════════════════════════════════════════════════════════════════════════

def _check_zai_cli() -> bool:
    """Проверяет что z-ai CLI доступен."""
    try:
        result = subprocess.run(['z-ai', '--help'],
                                capture_output=True, timeout=10)
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def llm_chat(prompt: str, system: str = '',
             timeout: int = 60, retries: int = 3,
             rate_limit_sleep: float = 8.0) -> Optional[str]:
    """Вызов LLM через z-ai CLI. Возвращает текст ответа или None при ошибке.

    v6.1.2: добавлен rate_limit_sleep — между попытками делаем паузу (default 8s),
    потому что z-ai API возвращает 429 Too Many Requests при последовательных
    вызовах без задержки. Это критично для пакетной обработки 6+ документов.

    Использует временный файл для --output (CLI требует JSON output).
    """
    if not _check_zai_cli():
        return None

    cmd = ['z-ai', 'chat', '-p', prompt]
    if system:
        cmd.extend(['-s', system])

    # Временный файл для JSON-ответа
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False,
                                     prefix='zai_topic_') as tmp:
        out_path = tmp.name
    cmd.extend(['-o', out_path])

    last_err = None
    import time as _time
    for attempt in range(retries + 1):
        # Пауза перед повторной попыткой (пропускаем первую)
        if attempt > 0:
            sleep_for = rate_limit_sleep * attempt  # 8s, 16s, 24s экспоненциально
            _time.sleep(sleep_for)
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if result.returncode != 0:
                stderr = result.stderr.decode('utf-8', errors='replace') if isinstance(result.stderr, bytes) else (result.stderr or '')
                last_err = f"exit={result.returncode}, stderr={stderr[:300]}"
                # Детект 429 в stderr → следующая попытка с большей паузой
                if '429' in stderr or 'Too many requests' in stderr:
                    continue
                # Другие ошибки тоже retry
                continue
            # Парсим JSON
            with open(out_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # z-ai chat сохраняет {"choices": [{"message": {"content": "..."}}], ...}
            content = (data.get('choices', [{}])[0]
                       .get('message', {})
                       .get('content', ''))
            return content.strip() if content else None
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s"
        except (json.JSONDecodeError, OSError, KeyError, IndexError) as e:
            last_err = f"parse error: {e}"
        finally:
            pass

    try:
        os.unlink(out_path)
    except OSError:
        pass
    return None


# ════════════════════════════════════════════════════════════════════════
# LLM PROMPTS
# ════════════════════════════════════════════════════════════════════════

_SYSTEM_RU = (
    "Ты — ассистент-аналитик текста. Отвечай КРАТКО на русском языке. "
    "Не пиши лишних вводных слов. Не используй markdown-разметку кроме как просили."
)

_SYSTEM_CODE = (
    "Ты — программист-аналитик. Отвечай КРАТКО на русском. "
    "Не описывай как работает код — только его назначение одной фразой."
)


def _build_cluster_prompt(cluster_chunks: List[str], max_chars: int = 3000) -> str:
    """Промпт для темы одного кластера."""
    # Берём до max_chars символов из кластера
    sample = ""
    for ch in cluster_chunks:
        if len(sample) + len(ch) > max_chars:
            sample += ch[:max_chars - len(sample)]
            break
        sample += ch + "\n\n"
    sample = sample.strip()

    return (
        f"Перед тобой фрагменты из документа (часть одного смыслового кластера).\n"
        f"Назови ТЕМУ этого кластера — кратко, 2-7 слов, без точки в конце.\n"
        f"Тема должна быть конкретной (не 'разное' и не 'общее').\n"
        f"Если фрагменты на русском — тема на русском. Если на английском — на английском.\n"
        f"\n"
        f"---ФРАГМЕНТЫ---\n{sample}\n"
        f"---КОНЕЦ---\n"
        f"\n"
        f"Тема (только тема, без префиксов вроде 'Тема:' или 'Cluster topic:'):"
    )


def _build_overall_prompt(all_chunks: List[str], max_chars: int = 5000) -> str:
    """Промпт для общей темы документа."""
    # Берём sample: первые + последние + средние
    if not all_chunks:
        return ""
    if sum(len(c) for c in all_chunks) <= max_chars:
        sample = "\n\n".join(all_chunks)
    else:
        # Распределённый sample
        n = len(all_chunks)
        # 60% — начало, 20% — середина, 20% — конец
        head = max(1, n // 3)
        tail = max(1, n // 5)
        mid = max(1, n // 5)
        selected = (all_chunks[:head] +
                    all_chunks[n//2 - mid//2:n//2 + mid//2] +
                    all_chunks[-tail:])
        sample = ""
        for ch in selected:
            if len(sample) + len(ch) > max_chars:
                break
            sample += ch + "\n\n"
        sample = sample.strip()

    return (
        f"Перед тобой фрагменты из документа. Документ разбит на смысловые кластеры, "
        f"но ты видишь выборку из разных частей.\n"
        f"Назови ОБЩУЮ ТЕМУ документа — 3-10 слов, одной фразой.\n"
        f"Это должно быть конкретное описание, о чём документ, а не 'разное'.\n"
        f"Если фрагменты на русском — тема на русском. Если на английском — на английском.\n"
        f"\n"
        f"---ВЫБОРКА ИЗ ДОКУМЕНТА---\n{sample}\n"
        f"---КОНЕЦ---\n"
        f"\n"
        f"Общая тема документа (только тема, без префиксов):"
    )


def _build_code_prompt(lang: str, head: str,
                       entities: Dict[str, List[str]]) -> str:
    """Промпт для назначения кода."""
    ents_str = ""
    if entities.get('classes'):
        ents_str += f"Классы: {', '.join(entities['classes'][:10])}\n"
    if entities.get('functions'):
        ents_str += f"Функции: {', '.join(entities['functions'][:15])}\n"
    if entities.get('imports'):
        ents_str += f"Импорты: {', '.join(entities['imports'][:10])}\n"
    if entities.get('constants'):
        ents_str += f"Константы: {', '.join(entities['constants'][:10])}\n"

    return (
        f"Перед тобой начало файла на языке {lang} и список его сущностей.\n"
        f"Опиши НАЗНАЧЕНИЕ этого кода ОДНОЙ фразой (5-15 слов).\n"
        f"Например: 'Анализатор текста с ε-редкостью и кластеризацией фрагментов' "
        f"или 'HTTP-сервер для REST API управления задачами'.\n"
        f"Не пересказывай имена функций — опиши ЧТО делает код в целом.\n"
        f"\n"
        f"---НАЧАЛО ФАЙЛА---\n{head[:2500]}\n"
        f"---КОНЕЦ---\n"
        f"\n"
        f"---СУЩНОСТИ---\n{ents_str}\n"
        f"---КОНЕЦ---\n"
        f"\n"
        f"Назначение кода (только фраза, без 'Назначение:' или подобных):"
    )


# ════════════════════════════════════════════════════════════════════════
# MAIN: detect_topics_llm
# ════════════════════════════════════════════════════════════════════════

def detect_topics_llm(path: str,
                      max_chunk_size: int = 1500,
                      max_clusters: int = 10,
                      max_chunks_per_cluster: int = 3,
                      verbose: bool = False) -> Dict:
    """Главная функция LLM-версии. См. docstring в начале файла.

    Fallback: если z-ai CLI не доступен, делегирует в topic_local.detect_topics.
    """
    if not _check_zai_cli():
        if verbose:
            sys.stderr.write("[topic_llm] z-ai CLI недоступен — fallback на topic_local.\n")
        from topic_local import detect_topics as _local
        result = _local(path, max_chunk_size=max_chunk_size,
                        max_clusters=max_clusters)
        result['method'] = 'fallback:tfidf (z-ai CLI недоступен)'
        return result

    text = read_text(path)
    if not text.strip():
        return {'path': path, 'error': 'empty file', 'overall_topic': ''}

    # ─── РЕЖИМ B: КОД ─────────────────────────────────────────────────
    if is_code(text, path):
        lang = detect_language(path, text)
        entities = extract_code_entities(text, lang)
        if verbose:
            sys.stderr.write(f"[topic_llm] Код: {lang}. Запрос к LLM...\n")
        prompt = _build_code_prompt(lang, text[:3000], entities)
        purpose = llm_chat(prompt, system=_SYSTEM_CODE)
        if not purpose:
            # Fallback на entities-based topic
            parts = []
            if entities['classes']:
                parts.append(f"классы: {', '.join(entities['classes'][:5])}")
            if entities['functions']:
                parts.append(f"функции: {', '.join(entities['functions'][:5])}")
            purpose = f"{lang}"
            if parts:
                purpose += " — " + "; ".join(parts)
            method = 'regex-entities (LLM недоступен)'
        else:
            # Очистка: убрать возможные кавычки/префиксы
            purpose = purpose.strip('«»"\'').strip()
            # Если LLM вернул с префиксом "Назначение:" — убрать
            purpose = re.sub(r'^(?:Назначение|Purpose|Topic)\s*:\s*', '',
                              purpose, flags=re.IGNORECASE)
            purpose = f"{lang} — {purpose}"
            method = 'llm-zai'
        return {
            'is_code': True,
            'path': path,
            'language': lang,
            'overall_topic': purpose,
            'entities': entities,
            'method': method,
        }

    # ─── РЕЖИМ A: ТЕКСТ ───────────────────────────────────────────────
    chunks = split_into_chunks(text, max_chars=max_chunk_size)
    if len(chunks) == 0:
        return {'path': path, 'error': 'no chunks', 'overall_topic': ''}

    # Кластеризация (переиспользуем из topic_local)
    if _HAS_SKLEARN and len(chunks) >= 4:
        labels = cluster_sklearn(chunks, max_clusters=max_clusters)
        cluster_method = 'tfidf+kmeans+silhouette'
    else:
        labels = cluster_agglomerative(chunks, threshold=0.15,
                                        max_clusters=max_clusters)
        cluster_method = 'tfidf+agglomerative-jaccard'

    # Группировка чанков по кластерам
    clusters_map: Dict[int, List[str]] = defaultdict(list)
    for chunk, label in zip(chunks, labels):
        clusters_map[label].append(chunk)

    # Для каждого кластера — LLM-запрос
    if verbose:
        sys.stderr.write(f"[topic_llm] {len(clusters_map)} кластеров. "
                         f"LLM-запросы пошли...\n")

    clusters_result = []
    import time as _time
    for cluster_id, (cluster_label, chunk_list) in enumerate(sorted(clusters_map.items())):
        # Берём только max_chunks_per_cluster первых чанков для sample
        sample_chunks = chunk_list[:max_chunks_per_cluster]
        if verbose:
            sys.stderr.write(f"  Кластер {cluster_id + 1}/{len(clusters_map)} "
                             f"({len(chunk_list)} фрагм.) → LLM...\n")
        prompt = _build_cluster_prompt(sample_chunks)
        topic = llm_chat(prompt, system=_SYSTEM_RU)
        # v6.1.2: пауза 1с между LLM-вызовами чтобы не ловить 429
        _time.sleep(1.0)
        if not topic:
            # Fallback на простую тему
            from topic_local import compute_tfidf_keywords
            kws = compute_tfidf_keywords([' '.join(chunk_list)], top_n=3, ngram=2)
            if kws and kws[0]:
                topic = ', '.join(kw for kw, _ in kws[0])
            else:
                topic = '(тема не определена)'
        else:
            topic = topic.strip('«»"\'').strip()
            topic = re.sub(r'^(?:Тема|Topic)\s*:\s*', '', topic, flags=re.IGNORECASE)

        preview = chunk_list[0][:300] if chunk_list else ''
        clusters_result.append({
            'cluster_id': cluster_id,
            'size': len(chunk_list),
            'topic': topic,
            'preview': preview,
        })

    # Общая тема документа
    if verbose:
        sys.stderr.write("[topic_llm] Запрос общей темы документа...\n")
    overall_prompt = _build_overall_prompt(chunks)
    overall_topic = llm_chat(overall_prompt, system=_SYSTEM_RU)
    if not overall_topic:
        # Fallback на TF-IDF
        from topic_local import compute_tfidf_keywords
        kws = compute_tfidf_keywords([text], top_n=5, ngram=2)
        if kws and kws[0]:
            overall_topic = ', '.join(kw for kw, _ in kws[0])
        else:
            overall_topic = '(тема не определена)'
        method = f'llm-zai (clusters) + tfidf-fallback (overall); clustering={cluster_method}'
    else:
        overall_topic = overall_topic.strip('«»"\'').strip()
        overall_topic = re.sub(r'^(?:Общая тема|Overall topic|Topic)\s*:\s*',
                                '', overall_topic, flags=re.IGNORECASE)
        method = f'llm-zai; clustering={cluster_method}'

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
        description='LLM-определение темы документа/кода (через z-ai CLI).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python topic_llm.py document.md
  python topic_llm.py script.py --format json
  python topic_llm.py book.epub --verbose
  python topic_llm.py archive.zip --max-clusters 8

Особенности:
  - Использует z-ai CLI для LLM-запросов (мои внутренние веса).
  - Тема — естественной фразой, не список ключевых слов.
  - Fallback на topic_local (TF-IDF) если z-ai недоступен.
        """,
    )
    parser.add_argument('file', help='Путь к файлу (.txt/.md/.epub/.zip/.tar.gz/.py/.rs/...)')
    parser.add_argument('--format', choices=['text', 'json'], default='text',
                        help='Формат вывода (text по умолчанию)')
    parser.add_argument('--max-chunk-size', type=int, default=1500,
                        help='Макс. символов на чанк (default: 1500)')
    parser.add_argument('--max-clusters', type=int, default=10,
                        help='Макс. число кластеров (default: 10)')
    parser.add_argument('--max-chunks-per-cluster', type=int, default=3,
                        help='Сколько чанков из кластера отправлять в LLM (default: 3)')
    parser.add_argument('--verbose', action='store_true',
                        help='Показывать прогресс LLM-запросов в stderr')
    args = parser.parse_args()

    result = detect_topics_llm(
        args.file,
        max_chunk_size=args.max_chunk_size,
        max_clusters=args.max_clusters,
        max_chunks_per_cluster=args.max_chunks_per_cluster,
        verbose=args.verbose,
    )

    if args.format == 'json':
        print(format_output_json(result))
    else:
        print(format_output_human(result))
        print(f"\n[метод: {result.get('method', '?')}]")


if __name__ == '__main__':
    main()
