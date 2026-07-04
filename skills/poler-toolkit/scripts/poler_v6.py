#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POLER[n] v6.1 — FINAL UNIFIED BUILD + AUTO-CHUNK
==================================================
Объединение v4 (база) + v5 (grep killer + archive + epsilon_v4).

НОВОЕ в v6.1:
  • --auto-chunk: автоматическое chunked-чтение по кластерам.
    POLER сам бьёт файл на кластеры, AI глотает столько сколько влезает.
    Излишек — в swap-файл (OPFS/tmpfile). Потом продолжает.
    Финал — 100% покрытие файла + ответ.
  • --theme в subcommand analyze (снято ограничение v6.0)
  • --read-cluster N: ручной режим чтения конкретного кластера

ИЗ v4 (poler_v4.py) — ПОЛНОСТЬЮ СОХРАНЕНО:
  • read_epub(), read_file(), json_to_text(), read_png_metadata(), scan_directory()
  • read_archive() — ZIP / TAR / GZ без распаковки на диск
  • analyze_directory(), compute_cross_resonance(), diff_files()
  • THEMES dict (биология/астрономия/география/культивация/навигация)
  • PolerAnalyzer class, format_directory_markdown(), format_diff_markdown()
  • Fragment dataclass (normalized_epsilon, cluster_id, section, keyword_count)
  • detect_sections(), get_section_for_position(), deduplicate_positions()
  • cluster_fragments() с адаптивным gap
  • cluster_cross_file() — кросс-файловая кластеризация (Jaccard)
  • build_search_index(), search_in_text() — full-text search (multi-word)
  • analyze_text() с нормализацией ε 0-100 + секции + кластеризация
  • analyze_large_file() — streaming 2-pass
  • build_summary(), format_text/json/markdown, --quiet
  • CLI флаги v4: --recursive, --theme, --cross-resonance, --diff,
    --include-images, EPUB, full-text search, --multi, --stdin
  • HTTP API: /analyze, /analyze_many, /search, /health

ИЗ v5 (poler_v5.py) — ДОБАВЛЕНО:
  1. GrepResult dataclass + grep_search_file() + grep_search_directory()
  2. grep_format_text() + grep_format_json() (grep-совместимый вывод)
  3. compute_epsilon_v4() — улучшенная формула ε с нормализацией по длине
     (v5 идентична v4 — формула уже была улучшена в v4; оставлена как есть)
  4. API endpoints /grep и /grep_dir (regex-поиск по файлу и директории)
  5. Subcommand CLI: analyze | grep | analyze_dir | diff | api
  6. Обратная совместимость: если первый аргумент не subcommand,
     treat as file path → старый CLI v4

ИСПРАВЛЕНИЕ БАГА v6 (КРИТИЧНО):
  В analyze_large_file() добавлена проверка расширения ПЕРЕД streaming-блоком.
  Для .epub/.zip/.tar/.gz/.tgz вызывается read_file() (который раскрывает архив
  или EPUB), а не open() в текстовом режиме. Раньше большой EPUB (>100КБ)
  попадал в streaming и открывался как бинарный ZIP в режиме 'r' → мусор.

НОВОЕ в v6:
  • Grep-killer: regex-поиск с номерами строк, контекстом, --include/--exclude
  • /grep и /grep_dir API endpoints
  • Subcommand CLI с обратной совместимостью к v4
  • --diff как subcommand: `poler_v6.py diff Ф1 Ф2 -k KW`

Zero dependencies — только Python 3.8+ стандартная библиотека.
"""

import argparse
import fnmatch
import gzip
import json
import math
import os
import re
import sys
import tarfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

__version__ = "6.1.0"
__author__ = "POLER[n] Studio + Super Z (v6 final unified)"

# ════════════════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ════════════════════════════════════════════════════════════════════════

DEFAULT_WINDOW = 3000
DEFAULT_OVERLAP_MERGE = 500
DEFAULT_CHUNK_SIZE = 100000
DEFAULT_API_PORT = 8000

PII_PATTERNS: List[Tuple[str, str]] = [
    (r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL]'),
    (r'\+?\d{1,3}[-.\s]?\(?\d{2,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}', '[PHONE]'),
    (r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}', '[CARD]'),
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]'),
    (r'\b\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\b', '[DATE]'),
    (r'(?<!\d)\d{10,12}(?!\d)', '[ID]'),
]

NOISE_WORDS = {
    'chatgpt', 'gpt', 'claude', 'gemini', 'llama', 'mistral', 'copilot', 'bard',
    'пользователь', 'user', 'chat', 'assistant', 'ассистент',
    'сказал', 'написал', 'responded', 'answered', 'replied',
    'http', 'https', 'www', 'com', 'org', 'net', 'ru', 'ua',
}

STOPWORDS = set("""
і в на з до по для що як це цей ця ці ту він вона воно вони ми ви я
та або але щоб коли якщо бо те тому й ой ну ось де під над між
and the a an of to in on at for with by is are was were be been being
he she it they we you i this that these those but or not no yes
но из на к с по для что как это этот эта эти тот он она оно они мы вы я
да нет или но чтобы если потому без при о а
de la le les un une du des et en
""".split())

EMOTIONAL_MARKERS = {
    'важливо', 'критично', 'загроза', 'сенс', 'істота', 'важливий', 'значущий',
    'проблема', 'сутність', 'глибокий', 'фундаментальний', 'криза', 'ризик',
    'відповідальність', 'свідомість', 'реальність', 'істина', 'буття',
    'важно', 'угроза', 'смысл', 'сущность', 'важный', 'значимый',
    'проблема', 'глубокий', 'фундаментальный', 'кризис', 'риск',
    'ответственность', 'сознание', 'реальность', 'истина', 'бытие',
    'important', 'critical', 'threat', 'meaning', 'crisis', 'risk',
    'responsibility', 'consciousness', 'reality', 'truth', 'existence',
    'essence', 'fundamental', 'deep',
    'история', 'сила', 'власть', 'закон', 'порядок', 'хаос', 'магия',
    'культура', 'религия', 'политика', 'экономика', 'война', 'мир',
}

SECTION_PATTERNS = [
    r'^#{1,6}\s+(.+)$',
    r'^\d+[\.\)]\s+(.+)$',
    r'^[А-ЯІЇЄҐ]{3,}[\s:]',
    r'^[A-Z][A-Za-z\s]{5,}:',
    r'^={3,}',
    r'^-{3,}',
]

THEMES: Dict[str, List[str]] = {
    'биология': [
        'резоносом', 'клетка', 'мембрана', 'митоз', 'ДНК', 'феррум', 'σ_e',
        'BMR', 'гестац', 'беремен', 'плод', 'матка', 'плацент', 'эмбрион',
        'Акме', 'проводим', 'меридиан', 'φ-поле', 'фредерит', 'резофаз',
        'пьезо', 'органелл', 'кровь', 'метаболизм', 'АТФ', 'размнож',
    ],
    'астрономия': [
        'P³', 'ΔΣ', 'φ-поле', 'фредерит', 'Этерия', 'Земля', 'транзит',
        'M1', 'M2', 'параллакс', 'орбит', 'Кеплер', 'синодическ',
        'конъюнкц', '33', 'окно', 'П³', 'W=0', 'проективн',
    ],
    'география': [
        'Сектор', 'Северный тракт', 'Аурелия', 'Бездна', 'регион',
        'Хрустальные', 'Платинов', 'Проклятые', 'Империя', 'Леса Востока',
        'карта', 'координат', 'широта', 'долгота', 'маршрут',
    ],
    'культивация': [
        'Сфера', 'сфер', 'Мнемар', 'Архимаг', 'Демоническ', 'Запредельн',
        'Теневая', 'Архисфер', 'Дракон', 'Манас', 'Абсолют', 'Демпфер',
        'Хаос', 'Порядок', 'Синтез', 'σ_e', 'культив', 'формообразов',
    ],
    'навигация': [
        'P³', 'Протока', 'Киевские ворота', 'Одесса', 'транзит',
        'Вениамин', 'Алексей', 'Ольга', 'Крона', 'Архитекторы',
        'T-0', 'T-16', 'T-22', 'ладонь', 'Драконья матрица',
    ],
}

# ─── v6.1: AUTO-THEME DETECTION ─────────────────────────────────────────
# Авто-определение темы на основе содержимого документа.
# Работает по принципу "bag of stems": считаем совпадения подстрок из
# словаря каждой темы в первых N символах текста. Тема с макс. счётом
# объявляется детектированной. Если счёт 0 по всем темам → 'general'.

def _is_short_or_numeric_token(word: str) -> bool:
    """v6.1.1: True если слово требует word-boundary match.

    Это короткие токены (≤3 char) и/или содержащие цифры/спецсимволы:
    '33', 'M1', 'M2', 'T-0', 'W=0', 'P³', 'П³', 'σ_e' и т.д.
    Для них substring match даёт false positives: '33' матчится в '1933',
    'M1' в 'M16', 'T-0' в 'T-001' и т.п.
    """
    if len(word) <= 3:
        return True
    # Любая цифра в слове → тоже требуем границу (M1, T-0, 33)
    if any(ch.isdigit() for ch in word):
        return True
    return False


def _count_word_matches(sample: str, word: str) -> int:
    """v6.1.1: считает совпадения слова в sample с правильной стратегией.

    Для коротких/числовых токенов (33, M1, T-0) — word-boundary regex.
    Для длинных основ (культив, фредерит, Кеплер) — substring count
    (чтобы матчило инфлектированные формы: культивация, культивируя).
    """
    w = word.lower()
    if _is_short_or_numeric_token(word):
        # Word-boundary для коротких/числовых
        # Экранируем спецсимволы regex (например 'T-0', 'W=0', 'P³')
        try:
            pattern = r'\b' + re.escape(w) + r'\b'
            return len(re.findall(pattern, sample))
        except re.error:
            return sample.count(w)
    else:
        return sample.count(w)


# v6.1.1: пороги уверенности для --theme auto (защита от false positives).
# Тема принимается если:
#   (a) distinct_words >= MIN_DISTINCT_WORDS, ИЛИ
#   (b) total_score >= MIN_TOTAL_SCORE
# Где distinct_words — сколько разных слов из словаря темы встретилось,
# а total_score — сумма всех совпадений.
THEME_MIN_DISTINCT_WORDS = 2
THEME_MIN_TOTAL_SCORE = 5


def detect_theme(text: str, sample_size: int = 20000,
                 apply_threshold: bool = True) -> str:
    """Авто-определение темы по содержимому текста.

    Args:
        text:              Полный текст документа (или значимый фрагмент).
        sample_size:       Сколько первых символов сканировать (для скорости на
                           больших файлах). 20 KB достаточно для надёжной классификации.
        apply_threshold:   v6.1.1: если True (default) — применять confidence
                           threshold (THEME_MIN_DISTINCT_WORDS / THEME_MIN_TOTAL_SCORE).
                           Если False — выбирать тему с макс. score даже при 1 совпадении
                           (полезно для directory mode, где агрегируются голоса многих
                           файлов и порог на каждый файл слишком строгий).

    Returns:
        Имя темы из THEMES ('биология' / 'астрономия' / ...).
        Если совпадений нет ни по одной теме → 'general'.

    v6.1.1: добавлен confidence threshold (THEME_MIN_DISTINCT_WORDS /
    THEME_MIN_TOTAL_SCORE) и word-boundary match для коротких/числовых
    токенов. Раньше единственное совпадение '33' (число!) детектировало
    'астрономию' на любом тексте, содержащем год/номер страницы.
    """
    if not text or not text.strip():
        return 'general'

    sample = text[:sample_size].lower()
    best_theme = 'general'
    best_score = 0
    best_distinct = 0

    for theme_name, vocab in THEMES.items():
        score = 0
        distinct = 0
        for word in vocab:
            n = _count_word_matches(sample, word)
            if n > 0:
                distinct += 1
            score += n
        # v6.1.1: confidence threshold (optional — отключается в directory mode)
        if apply_threshold:
            confident = (distinct >= THEME_MIN_DISTINCT_WORDS or
                         score >= THEME_MIN_TOTAL_SCORE)
        else:
            confident = score > 0
        if confident and score > best_score:
            best_score = score
            best_distinct = distinct
            best_theme = theme_name

    return best_theme


def detect_theme_with_scores(text: str, sample_size: int = 20000) -> Tuple[str, Dict[str, int], Dict[str, int]]:
    """То же что detect_theme, но дополнительно возвращает scoreboard всех тем.

    Полезно для отладки и для объяснения выбора в логах.

    v6.1.1: возвращает кортеж (theme, scores, distinct_counts). Scoreboard
    показывает оба: `астрономия: score=4, words=2`. Добавлен confidence
    threshold (THEME_MIN_DISTINCT_WORDS=2 / THEME_MIN_TOTAL_SCORE=5) — тема
    с 1 совпадением '33' теперь НЕ детектируется.
    """
    if not text or not text.strip():
        empty = {t: 0 for t in THEMES.keys()}
        return 'general', empty, empty

    sample = text[:sample_size].lower()
    scores: Dict[str, int] = {}
    distinct_counts: Dict[str, int] = {}
    for theme_name, vocab in THEMES.items():
        score = 0
        distinct = 0
        for word in vocab:
            n = _count_word_matches(sample, word)
            if n > 0:
                distinct += 1
            score += n
        scores[theme_name] = score
        distinct_counts[theme_name] = distinct

    # v6.1.1: выбираем лучшую тему с confidence threshold
    best_theme = 'general'
    best_score = 0
    for theme_name in THEMES.keys():
        score = scores[theme_name]
        distinct = distinct_counts[theme_name]
        confident = (distinct >= THEME_MIN_DISTINCT_WORDS or
                     score >= THEME_MIN_TOTAL_SCORE)
        if confident and score > best_score:
            best_score = score
            best_theme = theme_name

    return best_theme, scores, distinct_counts


def detect_theme_for_file(path: str) -> str:
    """Авто-определение темы по содержимому файла (включая EPUB/архивы)."""
    try:
        text = read_file(path)
        return detect_theme(text)
    except Exception:
        return 'general'


def detect_theme_for_directory(dir_path: str,
                                max_files_to_sample: int = 30) -> str:
    """Авто-определение темы по агрегированному содержимому директории.

    Сканирует первые max_files_to_sample текстовых файлов и выбирает
    доминирующую тему по голосованию файлов (не по суммарному счёту слов —
    так один огромный файл не перекрывает десятки маленьких).

    v6.1.1: per-file threshold отключён (apply_threshold=False), потому что
    в директории из 100+ файлов каждый отдельный файл может содержать только
    1-2 тематических слова (ниже порога), но если 30 файлов проголосовали за
    одну тему — это сильный агрегированный сигнал. Финальная проверка: тема
    принимается только если за неё проголосовало ≥2 файлов (AGGREGATE_THRESHOLD),
    иначе 'general' — это защищает от случая когда 1 файл из 30 случа́йно
    совпал.
    """
    AGGREGATE_VOTES_MIN = 2  # минимум файлов должны проголосовать за тему

    files = scan_directory(dir_path, list(TEXT_EXTENSIONS) + ['.epub'])
    if not files:
        return 'general'

    sample_files = files[:max_files_to_sample]
    votes: Dict[str, int] = {t: 0 for t in THEMES.keys()}

    for fpath in sample_files:
        try:
            text = read_file(fpath)
            # v6.1.1: relaxed threshold per-file (aggregate vote is the filter)
            theme = detect_theme(text, sample_size=5000, apply_threshold=False)
            if theme in votes:
                votes[theme] += 1
        except Exception:
            continue

    best = max(votes.items(), key=lambda x: x[1])
    if best[1] < AGGREGATE_VOTES_MIN:
        return 'general'
    return best[0]


TEXT_EXTENSIONS = ('.txt', '.md', '.markdown', '.html', '.htm', '.xhtml',
                   '.json', '.csv', '.rst', '.log', '.xml')
ARCHIVE_SUFFIXES = ('.zip', '.tar', '.tar.gz', '.tgz', '.gz')

# Расширения, которые НЕЛЬЗЯ streaming'ом читать как текст (v6 FIX)
BINARY_SUFFIXES = ('.epub', '.zip', '.tar', '.gz', '.tgz')

SUBCOMMANDS = {'analyze', 'grep', 'analyze_dir', 'diff', 'smart', 'api'}

# ════════════════════════════════════════════════════════════════════════
# МОДЕЛИ
# ════════════════════════════════════════════════════════════════════════

@dataclass
class Fragment:
    """v3: фрагмент текста с нормализованной ε, кластером, секцией."""
    position: int
    end_position: int
    text: str
    epsilon: float = 0.0
    normalized_epsilon: float = 0.0
    resonance: float = 0.0
    cluster_id: int = -1
    section: str = ""
    keyword_count: int = 0
    source_file: str = ""


@dataclass
class TextWindow:
    """v2: окно вокруг ключевого слова (для совместимости)."""
    index: int
    keyword: str
    position: int
    raw_text: str
    cleaned_text: str
    filtered_items: List[Tuple[str, str]] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)
    epsilon: float = 0.0
    normalized_epsilon: float = 0.0
    resonance: float = 0.0
    source_file: str = ""


@dataclass
class GrepResult:
    """v5: результат regex-поиска (замена grep)."""
    file: str
    line_number: int
    line: str
    match_start: int
    match_end: int
    before_context: List[str] = field(default_factory=list)
    after_context: List[str] = field(default_factory=list)

# ════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════════════════

def tokenize(text: str) -> List[str]:
    """Токенизация: lowercase, фильтр стоп-слов и шума, длина > 2."""
    raw = re.findall(r'[\w]+', text.lower(), re.UNICODE)
    return [t for t in raw
            if t not in STOPWORDS
            and t not in NOISE_WORDS
            and len(t) > 2]


def filter_pii(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Фильтрация PII (email, phone, card, IP, date, ID)."""
    filtered: List[Tuple[str, str]] = []
    cleaned = text
    for pattern, replacement in PII_PATTERNS:
        for m in re.finditer(pattern, cleaned):
            filtered.append((m.group(0), replacement))
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned, filtered


def word_rarity(word: str, total_words: int, counts: Counter) -> float:
    """Информационная ценность слова: -log(p)."""
    p = counts.get(word, 1) / max(total_words, 1)
    return -math.log(max(p, 1e-10))


def compute_epsilon_v4(tokens: List[str], cleaned_text: str, keyword: str,
                       counts: Counter, total_words: int,
                       kappa: float = 1.0) -> Tuple[float, int]:
    """v4/v5 формула ε с нормализацией по длине фрагмента.
    Возвращает (raw_epsilon, keyword_count).
    Нормализация по sqrt(len(unique)) убирает «миллионы» в больших окнах."""
    kw_lower = keyword.lower() if keyword else ""
    filt_tokens = [t for t in tokens if t != kw_lower] if kw_lower else tokens
    if not filt_tokens:
        return 0.0, 0
    unique = set(filt_tokens)
    d_sq = sum(word_rarity(t, total_words, counts) ** 2 for t in unique)
    len_norm = math.sqrt(len(unique)) if unique else 1.0
    kw_count = cleaned_text.lower().count(kw_lower) if kw_lower else 0
    kw_intensity = 1.0 + math.log1p(kw_count) if kw_count > 0 else 1.0
    emotion = sum(1.5 for t in filt_tokens if t in EMOTIONAL_MARKERS)
    epsilon = (kappa * kw_intensity * d_sq + emotion) / len_norm
    return epsilon, kw_count


def normalize_epsilons(values: List[float]) -> List[float]:
    """Нормализация списка ε в шкалу 0-100."""
    if not values:
        return []
    max_v = max(values)
    if max_v <= 0:
        return [0.0] * len(values)
    return [(v / max_v) * 100.0 for v in values]

# ════════════════════════════════════════════════════════════════════════
# СЕКЦИИ (v3)
# ════════════════════════════════════════════════════════════════════════

def detect_sections(text: str) -> List[Dict]:
    """Автоматическая детекция секций (заголовки MD, нумерованные, CAPS)."""
    sections: List[Dict] = []
    lines = text.split('\n')
    current_pos = 0
    for line in lines:
        stripped = line.strip()
        for pattern in SECTION_PATTERNS:
            if re.match(pattern, stripped, re.MULTILINE):
                title = re.sub(r'^[#\d\.\)\*\-\=]+[\s:]*', '', stripped).strip()
                if title and len(title) > 3:
                    sections.append({'title': title[:100], 'position': current_pos})
                break
        current_pos += len(line) + 1
    return sections


def get_section_for_position(sections: List[Dict], position: int) -> str:
    """Какая секция содержит данную позицию."""
    current = "Начало"
    for s in sections:
        if s['position'] <= position:
            current = s['title']
        else:
            break
    return current

# ════════════════════════════════════════════════════════════════════════
# ДЕДУПЛИКАЦИЯ И КЛАСТЕРИЗАЦИЯ (v3 + v4)
# ════════════════════════════════════════════════════════════════════════

def deduplicate_positions(positions: List[int],
                          min_distance: int = DEFAULT_OVERLAP_MERGE) -> List[int]:
    """Слияние близких позиций (одно вхождение не в нескольких окнах)."""
    if not positions:
        return []
    sorted_pos = sorted(positions)
    merged = [sorted_pos[0]]
    for pos in sorted_pos[1:]:
        if pos - merged[-1] >= min_distance:
            merged.append(pos)
    return merged


def cluster_fragments(fragments: List, sections: Optional[List[Dict]] = None,
                      adaptive: bool = True) -> List[List[int]]:
    """Кластеризация с адаптивным gap на основе секций и плотности ε.
    fragments — список Fragment или dict с position/end_position."""
    if not fragments:
        return []

    def get_pos(f):
        return f.position if hasattr(f, 'position') else f.get('position', 0)

    def get_end(f):
        if hasattr(f, 'end_position'):
            return f.end_position
        return f.get('end_position', get_pos(f))

    sorted_frags = sorted(enumerate(fragments), key=lambda x: get_pos(x[1]))

    if adaptive and sections:
        section_ends = [s['position'] for s in sections]

        def get_gap(frag_pos):
            for end in section_ends:
                if end > frag_pos:
                    return max(2000, int((end - frag_pos) * 0.3))
            return 5000
    elif adaptive:
        positions_list = [get_pos(f) for f in fragments]

        def get_gap(frag_pos):
            nearby = sum(1 for p in positions_list if abs(p - frag_pos) < 20000)
            return 3000 if nearby > 5 else 8000
    else:
        def get_gap(frag_pos):
            return 10000

    clusters: List[List[int]] = []
    current_cluster = [sorted_frags[0][0]]
    current_end = get_end(sorted_frags[0][1])

    for idx, frag in sorted_frags[1:]:
        gap = get_gap(get_pos(frag))
        if get_pos(frag) - current_end <= gap:
            current_cluster.append(idx)
            current_end = max(current_end, get_end(frag))
        else:
            clusters.append(current_cluster)
            current_cluster = [idx]
            current_end = get_end(frag)
    clusters.append(current_cluster)
    return clusters


def cluster_cross_file(fragments: List[Fragment], threshold: float = 0.25,
                       max_fragments: int = 80) -> List[List[int]]:
    """v4: кросс-файловая кластеризация по токеновой схожести (Jaccard).
    Группирует фрагменты из РАЗНЫХ файлов с похожим содержанием."""
    if not fragments:
        return []
    sorted_frags = sorted(enumerate(fragments),
                          key=lambda x: -x[1].normalized_epsilon)
    top_indices = [idx for idx, _ in sorted_frags[:max_fragments]]

    token_sets: Dict[int, set] = {}
    for idx in top_indices:
        token_sets[idx] = set(tokenize(fragments[idx].text))

    parent = {idx: idx for idx in top_indices}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    n = len(top_indices)
    for i in range(n):
        for j in range(i + 1, n):
            idx1, idx2 = top_indices[i], top_indices[j]
            ts1, ts2 = token_sets[idx1], token_sets[idx2]
            if not ts1 or not ts2:
                continue
            jaccard = len(ts1 & ts2) / len(ts1 | ts2)
            if jaccard >= threshold:
                union(idx1, idx2)

    groups: Dict[int, List[int]] = defaultdict(list)
    for idx in top_indices:
        groups[find(idx)].append(idx)
    return list(groups.values())

# ════════════════════════════════════════════════════════════════════════
# GREP KILLER (v5) — поиск, заменяющий grep/ripgrep
# ════════════════════════════════════════════════════════════════════════

def grep_search_file(
    filepath: str,
    pattern: str,
    ignore_case: bool = False,
    whole_word: bool = False,
    invert: bool = False,
    count_only: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    context_lines: int = 0,
    max_matches: int = 0,
    encoding: str = 'utf-8',
) -> List[GrepResult]:
    """Regex-поиск в файле. Полный аналог grep.
    Возвращает список GrepResult с номерами строк и контекстом.

    v6.1 FIX (Bug 7): для архивов (.zip/.tar/.tar.gz/.tgz/.gz/.epub) поиск
    выполняется по каждому члену архива отдельно, а в поле GrepResult.file
    записывается 'path::member_name' — это позволяет различить, в каком
    внутреннем файле найдено совпадение. Раньше архив открывался как текст
    в режиме errors='replace', и в вывод попадали PK-заголовки и deflate-байты.
    """
    # ─── v6.1: диспетчер для архивов ────────────────────────────────────
    p_lower = Path(filepath).name.lower()
    is_archive = (
        p_lower.endswith(ARCHIVE_SUFFIXES) or p_lower.endswith('.epub')
    )
    if is_archive:
        return _grep_search_archive(
            filepath, pattern,
            ignore_case=ignore_case, whole_word=whole_word, invert=invert,
            count_only=count_only,
            context_before=context_before, context_after=context_after,
            context_lines=context_lines, max_matches=max_matches,
        )

    try:
        with open(filepath, 'r', encoding=encoding, errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return []

    flags = re.IGNORECASE if ignore_case else 0
    if whole_word:
        pattern = r'\b' + pattern + r'\b'

    try:
        regex = re.compile(pattern, flags)
    except re.error:
        return []

    cb = max(context_before, context_lines)
    ca = max(context_after, context_lines)
    results: List[GrepResult] = []
    matched_count = 0

    for i, line in enumerate(lines):
        matches = list(regex.finditer(line))
        has_match = len(matches) > 0
        show = has_match if not invert else not has_match

        if show:
            if count_only:
                matched_count += 1
                continue

            for m in matches:
                result = GrepResult(
                    file=filepath,
                    line_number=i + 1,
                    line=line.rstrip('\n\r'),
                    match_start=m.start(),
                    match_end=m.end(),
                    before_context=[lines[j].rstrip('\n\r')
                                    for j in range(max(0, i - cb), i)],
                    after_context=[lines[j].rstrip('\n\r')
                                   for j in range(i + 1, min(len(lines), i + 1 + ca))],
                )
                results.append(result)

                if max_matches > 0 and len(results) >= max_matches:
                    return results

    if count_only:
        result = GrepResult(
            file=filepath,
            line_number=0,
            line=str(matched_count),
            match_start=0,
            match_end=0,
        )
        return [result]

    return results


def _grep_search_archive(
    archive_path: str,
    pattern: str,
    ignore_case: bool = False,
    whole_word: bool = False,
    invert: bool = False,
    count_only: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    context_lines: int = 0,
    max_matches: int = 0,
) -> List[GrepResult]:
    """v6.1: grep внутри архива — по каждому члену отдельно.

    Для .epub используем read_epub() (он сам парсит XHTML → текст).
    Для остальных архивов — read_archive() (возвращает [(member_name, text), ...]).
    В GrepResult.file кладём '{archive_path}::{member_name}'.
    """
    flags = re.IGNORECASE if ignore_case else 0
    if whole_word:
        pattern = r'\b' + pattern + r'\b'
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        return []

    cb = max(context_before, context_lines)
    ca = max(context_after, context_lines)
    results: List[GrepResult] = []
    matched_count = 0

    # Получаем список (member_name, text)
    p_lower = Path(archive_path).name.lower()
    if p_lower.endswith('.epub'):
        # EPUB — один сквозной текст из всех XHTML-членов
        text = read_epub(archive_path)
        members = [(Path(archive_path).stem, text)]
    else:
        members = read_archive(archive_path)

    for member_name, member_text in members:
        if not member_text:
            continue
        lines = member_text.splitlines()
        for i, line in enumerate(lines):
            matches = list(regex.finditer(line))
            has_match = len(matches) > 0
            show = has_match if not invert else not has_match

            if show:
                if count_only:
                    matched_count += 1
                    continue

                for m in matches:
                    result = GrepResult(
                        file=f"{archive_path}::{member_name}",
                        line_number=i + 1,
                        line=line,
                        match_start=m.start(),
                        match_end=m.end(),
                        before_context=[lines[j]
                                        for j in range(max(0, i - cb), i)],
                        after_context=[lines[j]
                                       for j in range(i + 1, min(len(lines), i + 1 + ca))],
                    )
                    results.append(result)

                    if max_matches > 0 and len(results) >= max_matches:
                        return results

    if count_only:
        result = GrepResult(
            file=archive_path,
            line_number=0,
            line=str(matched_count),
            match_start=0,
            match_end=0,
        )
        return [result]

    return results


def grep_search_directory(
    dir_path: str,
    pattern: str,
    ignore_case: bool = False,
    whole_word: bool = False,
    invert: bool = False,
    count_only: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    context_lines: int = 0,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
    recursive: bool = True,
    max_matches_per_file: int = 0,
    max_files: int = 0,
) -> Dict[str, List[GrepResult]]:
    """Рекурсивный поиск по директории. Полный аналог grep -r.
    Возвращает {путь_к_файлу: [GrepResult]}."""
    results: Dict[str, List[GrepResult]] = {}
    files_scanned = 0

    for root, dirs, files in os.walk(dir_path):
        if not recursive:
            dirs.clear()

        for fname in sorted(files):
            fpath = os.path.join(root, fname)

            if include and not fnmatch.fnmatch(fname, include):
                continue
            if exclude and fnmatch.fnmatch(fname, exclude):
                continue

            if not os.path.isfile(fpath):
                continue

            try:
                size = os.path.getsize(fpath)
                if size > 10 * 1024 * 1024:
                    continue
                if size == 0:
                    continue
            except OSError:
                continue

            file_results = grep_search_file(
                fpath, pattern, ignore_case, whole_word, invert,
                count_only, context_before, context_after, context_lines,
                max_matches_per_file,
            )

            if file_results:
                results[fpath] = file_results

            files_scanned += 1
            if max_files > 0 and files_scanned >= max_files:
                return results

    return results


def grep_format_text(results: Dict[str, List[GrepResult]],
                     show_filename: bool = True,
                     show_line_numbers: bool = True,
                     group_context: bool = False) -> str:
    """Форматирование результатов как grep (текст)."""
    lines = []
    for filepath, file_results in sorted(results.items()):
        for r in file_results:
            parts = []
            if show_filename:
                parts.append(f"\033[1;32m{filepath}\033[0m" if sys.stdout.isatty() else filepath)
            if show_line_numbers and r.line_number > 0:
                parts.append(f"\033[1;33m{r.line_number}\033[0m" if sys.stdout.isatty() else str(r.line_number))

            if group_context:
                if r.before_context:
                    for bc in r.before_context:
                        ctx_parts = []
                        if show_filename:
                            ctx_parts.append(filepath)
                        ctx_parts.append("-")
                        ctx_parts.append(bc)
                        lines.append("-".join(ctx_parts))
                match_line = "-".join(parts + [r.line]) if parts else r.line
                lines.append(match_line)
                if r.after_context:
                    for ac in r.after_context:
                        ctx_parts = []
                        if show_filename:
                            ctx_parts.append(filepath)
                        ctx_parts.append("-")
                        ctx_parts.append(ac)
                        lines.append("-".join(ctx_parts))
            else:
                full_line = "-".join(parts + [r.line]) if parts else r.line
                lines.append(full_line)

    return "\n".join(lines)


def grep_format_json(results: Dict[str, List[GrepResult]]) -> str:
    """Форматирование результатов как JSON."""
    output = []
    for filepath, file_results in sorted(results.items()):
        for r in file_results:
            output.append({
                'file': r.file,
                'line_number': r.line_number,
                'line': r.line,
                'match_start': r.match_start,
                'match_end': r.match_end,
            })
    return json.dumps(output, ensure_ascii=False, indent=2)

# ════════════════════════════════════════════════════════════════════════
# FULL-TEXT SEARCH (v3 + ИСПРАВЛЕНИЕ v4)
# ════════════════════════════════════════════════════════════════════════

def build_search_index(text: str) -> Dict[str, List[int]]:
    """Индекс: word → список СИМВОЛЬНЫХ позиций в тексте."""
    index: Dict[str, List[int]] = defaultdict(list)
    for m in re.finditer(r'[\w]+', text.lower(), re.UNICODE):
        word = m.group(0)
        if word not in STOPWORDS and len(word) > 2:
            index[word].append(m.start())
    return dict(index)


def search_in_text(text: str, query: str,
                   index: Optional[Dict[str, List[int]]] = None,
                   max_results: int = 20,
                   context_chars: int = 300) -> List[Dict]:
    """Full-text search. Возвращает фрагменты с позициями и оценкой.

    ИСПРАВЛЕНИЕ v4: для multi-word запросов строим контекстные окна
    и оцениваем долю query-слов, попавших в окно."""
    if not query or not query.strip():
        return []

    query_words = tokenize(query)
    if not query_words:
        raw_words = [w.lower() for w in re.findall(r'[\w]+', query, re.UNICODE) if w]
        if not raw_words:
            return _substring_search(text, query.lower(), max_results, context_chars)
        query_words = raw_words

    query_words_set = set(query_words)

    if index is None:
        index = build_search_index(text)

    all_positions: List[int] = []
    for qw in query_words_set:
        if qw in index:
            all_positions.extend(index[qw])
    if not all_positions:
        for qw in query_words_set:
            for m in re.finditer(re.escape(qw), text, re.IGNORECASE):
                all_positions.append(m.start())
    if not all_positions:
        return []

    results: List[Dict] = []
    used_regions: List[Tuple[int, int]] = []
    for pos in sorted(all_positions):
        if any(s <= pos <= e for s, e in used_regions):
            continue
        start = max(0, pos - context_chars // 2)
        end = min(len(text), pos + context_chars // 2)
        context = text[start:end]
        context_lower = context.lower()
        matched = sum(1 for qw in query_words_set if qw in context_lower)
        score = matched / len(query_words_set)
        display = re.sub(r'\s+', ' ', context).strip()
        if len(display) > 500:
            display = display[:500] + '...'
        results.append({
            'position': pos,
            'start': start,
            'end': end,
            'context': display,
            'score': round(score, 3),
            'matched_words': matched,
            'total_query_words': len(query_words_set),
        })
        used_regions.append((start, end))

    results.sort(key=lambda x: -x['score'])
    return results[:max_results]


def _substring_search(text: str, query_lower: str, max_results: int,
                      context_chars: int) -> List[Dict]:
    """Fallback: прямой substring search (для коротких/цифровых запросов)."""
    results: List[Dict] = []
    used: List[Tuple[int, int]] = []
    for m in re.finditer(re.escape(query_lower), text, re.IGNORECASE):
        pos = m.start()
        if any(s <= pos <= e for s, e in used):
            continue
        start = max(0, pos - context_chars // 2)
        end = min(len(text), pos + context_chars // 2)
        display = re.sub(r'\s+', ' ', text[start:end]).strip()
        results.append({
            'position': pos, 'start': start, 'end': end,
            'context': display, 'score': 1.0,
            'matched_words': 1, 'total_query_words': 1,
        })
        used.append((start, end))
    return results[:max_results]

# ════════════════════════════════════════════════════════════════════════
# РЕЗОНАНС (v2)
# ════════════════════════════════════════════════════════════════════════

def compute_resonance_series(epsilons: List[float],
                             phi_decay: float = 0.85) -> List[float]:
    """R[n] = Σ ε_i × φ^(n-i)."""
    n = len(epsilons)
    R = [0.0] * n
    for t in range(n):
        s = 0.0
        for i in range(t + 1):
            s += epsilons[i] * (phi_decay ** (t - i))
        R[t] = s
    return R


def compute_cross_resonance(epsilons: List[float],
                            phi_decay: float = 0.85) -> List[float]:
    """Кросс-файлный резонанс: R_t учитывает фрагменты из ВСЕХ файлов."""
    return compute_resonance_series(epsilons, phi_decay)

# ════════════════════════════════════════════════════════════════════════
# ЧТЕНИЕ ФАЙЛОВ (v2 + v4 archives)
# ════════════════════════════════════════════════════════════════════════

def read_file(path: str) -> str:
    """Универсальное чтение: .txt/.md/.json/.epub/.html + архивы (zip/tar/gz)."""
    p = Path(path)
    if not p.exists():
        return ""
    suffix = p.suffix.lower()
    name_lower = p.name.lower()

    if suffix == '.epub':
        return read_epub(path)
    if suffix == '.json':
        try:
            data = json.loads(p.read_text(encoding='utf-8', errors='replace'))
            return json_to_text(data)
        except Exception:
            return p.read_text(encoding='utf-8', errors='replace')
    if suffix == '.zip' or name_lower.endswith(('.tar', '.tar.gz', '.tgz')) or suffix == '.gz':
        parts = []
        for name, text in read_archive(path):
            parts.append(f"--- {name} ---\n{text}")
        return '\n\n'.join(parts)
    try:
        return p.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return ""


def read_epub(path: str) -> str:
    """Чтение EPUB (ZIP с XHTML внутри) без распаковки на диск."""
    text_parts: List[str] = []
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith(('.xhtml', '.html', '.htm')):
                    content = zf.read(name).decode('utf-8', errors='ignore')
                    text = re.sub(r'<[^>]+>', ' ', content)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        text_parts.append(text)
    except Exception as e:
        return f"[EPUB READ ERROR: {e}]"
    return '\n\n'.join(text_parts)


def read_archive(path: str) -> List[Tuple[str, str]]:
    """v4: чтение архивов ZIP/TAR/GZ без распаковки на диск.
    Возвращает список (inner_filename, text_content) для текстовых файлов внутри."""
    p = Path(path)
    name_lower = p.name.lower()
    result: List[Tuple[str, str]] = []
    try:
        if name_lower.endswith('.zip'):
            with zipfile.ZipFile(path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith(TEXT_EXTENSIONS):
                        try:
                            content = zf.read(name).decode('utf-8', errors='ignore')
                            if content.strip():
                                result.append((name, content))
                        except Exception:
                            pass
        elif name_lower.endswith(('.tar', '.tar.gz', '.tgz')):
            with tarfile.open(path, 'r:*') as tf:
                for member in tf.getmembers():
                    if member.isfile() and member.name.endswith(TEXT_EXTENSIONS):
                        try:
                            f = tf.extractfile(member)
                            if f:
                                content = f.read().decode('utf-8', errors='ignore')
                                if content.strip():
                                    result.append((member.name, content))
                        except Exception:
                            pass
        elif name_lower.endswith('.gz'):
            try:
                with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if content.strip():
                        result.append((p.stem, content))
            except Exception as e:
                sys.stderr.write(f"[GZ ERROR {path}: {e}]\n")
    except Exception as e:
        sys.stderr.write(f"[ARCHIVE ERROR {path}: {e}]\n")
    return result


def read_png_metadata(path: str) -> Dict:
    """Чтение метаданных PNG (имя файла как текст — без PIL)."""
    p = Path(path)
    return {
        'filename': p.name,
        'size_bytes': p.stat().st_size,
        'path': str(p),
        'text': p.stem.replace('_', ' '),
    }


def json_to_text(data: Any, depth: int = 0) -> str:
    """Рекурсивное извлечение текста из JSON."""
    if depth > 10:
        return ""
    parts: List[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            parts.append(str(k))
            parts.append(json_to_text(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            parts.append(json_to_text(item, depth + 1))
    elif isinstance(data, (str, int, float)):
        parts.append(str(data))
    return ' '.join(parts)


def scan_directory(dir_path: str, extensions: Optional[List[str]] = None) -> List[str]:
    """Рекурсивный обход директории. Поддержка составных суффиксов (.tar.gz)."""
    if extensions is None:
        extensions = list(TEXT_EXTENSIONS) + ['.epub', '.png'] + list(ARCHIVE_SUFFIXES)
    result: List[str] = []
    p = Path(dir_path)
    if p.is_file():
        return [str(p)]
    for f in sorted(p.rglob('*')):
        if f.is_file():
            name_lower = f.name.lower()
            for ext in extensions:
                if name_lower.endswith(ext):
                    result.append(str(f))
                    break
    return result

# ════════════════════════════════════════════════════════════════════════
# АНАЛИЗ: analyze_text (v3 + v2-совместимая сигнатура)
# ════════════════════════════════════════════════════════════════════════

def _build_fragments(text: str, keyword: str, window_size: int,
                     kappa: float, source_file: str,
                     counts: Counter, total_words: int,
                     sections: List[Dict]) -> Tuple[List[Fragment], int, int]:
    """Внутренняя: строит список Fragment для текста."""
    pattern = re.compile(re.escape(keyword), re.IGNORECASE) if keyword else None
    if pattern:
        raw_positions = [m.start() for m in pattern.finditer(text)]
    else:
        raw_positions = list(range(0, len(text), max(window_size, 1)))
    total_occurrences = len(raw_positions) if pattern else 0
    positions = deduplicate_positions(raw_positions)

    fragments: List[Fragment] = []
    for pos in positions:
        start = max(0, pos - window_size // 2)
        end = min(len(text), pos + window_size // 2)
        raw_text = text[start:end]
        cleaned, _ = filter_pii(raw_text)
        tokens = tokenize(cleaned)
        eps, kw_count = compute_epsilon_v4(tokens, cleaned, keyword,
                                            counts, total_words, kappa)
        section = get_section_for_position(sections, pos)
        fragments.append(Fragment(
            position=pos, end_position=end, text=cleaned,
            epsilon=eps, section=section, keyword_count=kw_count,
            source_file=source_file,
        ))
    return fragments, total_occurrences, len(positions)


def analyze_text(text: str, keyword: str, window_size: int = DEFAULT_WINDOW,
                 phi_decay: float = 0.85, kappa: float = 1.0, top_n: int = 10,
                 source_file: str = "") -> Dict:
    """v3 analyze_text с v2-совместимой сигнатурой.
    Нормализация ε 0-100, секции, кластеризация, резонанс."""
    if not keyword:
        keyword = ""
    file_size = len(text.encode('utf-8'))
    all_tokens = tokenize(text)
    counts = Counter(all_tokens)
    total_words = len(all_tokens)
    sections = detect_sections(text)

    fragments, total_occurrences, _ = _build_fragments(
        text, keyword, window_size, kappa, source_file, counts, total_words, sections
    )

    norm_eps = normalize_epsilons([f.epsilon for f in fragments])
    for f, ne in zip(fragments, norm_eps):
        f.normalized_epsilon = ne

    R = compute_resonance_series([f.normalized_epsilon for f in fragments], phi_decay)
    for f, r in zip(fragments, R):
        f.resonance = r

    clusters = cluster_fragments(fragments, sections=sections, adaptive=True)
    for ci, cm in enumerate(clusters):
        for mi in cm:
            fragments[mi].cluster_id = ci

    sorted_frags = sorted(fragments, key=lambda f: f.normalized_epsilon, reverse=True)
    top_frags = sorted_frags[:top_n] if top_n > 0 else sorted_frags

    top_output = []
    for i, f in enumerate(top_frags, 1):
        display = re.sub(r'\s+', ' ', f.text).strip()
        if len(display) > 1500:
            display = display[:1500] + '...'
        top_output.append({
            'rank': i, 'position': f.position, 'end_position': f.end_position,
            'epsilon': round(f.epsilon, 4),
            'normalized_epsilon': round(f.normalized_epsilon, 2),
            'resonance': round(f.resonance, 2),
            'cluster': f.cluster_id, 'section': f.section,
            'keyword_count': f.keyword_count,
            'source_file': f.source_file, 'text': display,
        })

    sections_output = []
    for s in sections:
        fc = sum(1 for f in fragments if f.section == s['title'])
        sections_output.append({'title': s['title'], 'position': s['position'],
                                'fragments': fc})

    fragments_out = []
    for f in fragments:
        display = re.sub(r'\s+', ' ', f.text).strip()
        if len(display) > 2000:
            display = display[:2000] + '...'
        fragments_out.append({
            'position': f.position, 'end_position': f.end_position,
            'epsilon': round(f.epsilon, 4),
            'normalized_epsilon': round(f.normalized_epsilon, 2),
            'resonance': round(f.resonance, 2),
            'cluster': f.cluster_id, 'section': f.section,
            'keyword_count': f.keyword_count,
            'source_file': f.source_file,
            'text': display,
        })

    summary = build_summary(keyword, source_file, file_size, total_words,
                            total_occurrences, len(fragments), len(clusters),
                            fragments, clusters, sections_output)

    return {
        'keyword': keyword, 'file_path': source_file, 'file_size': file_size,
        'total_characters': len(text), 'total_words': total_words,
        'total_occurrences': total_occurrences,
        'unique_fragments': len(fragments),
        'clusters': clusters,
        'top_fragments': top_output,
        'fragments': fragments_out,
        'sections': sections_output,
        'full_summary': summary,
        'search_index': build_search_index(text),
    }


def build_summary(keyword: str, source_file: str, file_size: int,
                  total_words: int, total_occurrences: int,
                  unique_fragments: int, cluster_count: int,
                  fragments: List[Fragment], clusters: List[List[int]],
                  sections: List[Dict]) -> str:
    """Текстовое резюме анализа."""
    lines: List[str] = []
    lines.append(f"=== АНАЛИЗ: \"{keyword}\" ===")
    lines.append("")
    lines.append(f"Файл: {source_file or '<текст>'}")
    lines.append(f"Размер: {file_size:,} байт ({file_size / 1024:.0f} КБ)")
    lines.append(f"Слов: {total_words:,}")
    lines.append("")
    lines.append("=== СТАТИСТИКА ===")
    lines.append(f"Вхождений \"{keyword}\": {total_occurrences}")
    lines.append(f"Уникальных фрагментов: {unique_fragments}")
    lines.append(f"Кластеров: {cluster_count}")
    lines.append("")

    if sections:
        active = [s for s in sections if s.get('fragments', 0) > 0]
        if active:
            lines.append("=== СЕКЦИИ С ВХОЖДЕНИЯМИ ===")
            for s in sorted(active, key=lambda x: -x['fragments'])[:10]:
                lines.append(f"  {s['title'][:60]:60s}  [{s['fragments']} фрагм.]")
            lines.append("")

    if clusters:
        lines.append("=== КЛАСТЕРЫ ===")
        for i, cluster in enumerate(clusters):
            eps_vals = []
            for j in cluster:
                if j < len(fragments):
                    f = fragments[j]
                    eps_vals.append(f.normalized_epsilon if hasattr(f, 'normalized_epsilon')
                                    else f.get('normalized_epsilon', 0))
            avg_eps = sum(eps_vals) / len(eps_vals) if eps_vals else 0
            lines.append(f"  Кластер {i}: {len(cluster)} фрагм., ср. ε={avg_eps:.1f}")
        lines.append("")

    top = sorted(fragments,
                 key=lambda f: -(f.normalized_epsilon if hasattr(f, 'normalized_epsilon')
                                 else f.get('normalized_epsilon', 0)))[:5]
    if top:
        lines.append("=== ТОП-5 ФРАГМЕНТОВ ===")
        for i, f in enumerate(top, 1):
            text_val = f.text if hasattr(f, 'text') else f.get('text', '')
            preview = re.sub(r'\s+', ' ', text_val)[:200]
            ne = f.normalized_epsilon if hasattr(f, 'normalized_epsilon') else f.get('normalized_epsilon', 0)
            cid = f.cluster_id if hasattr(f, 'cluster_id') else f.get('cluster', -1)
            sec = f.section if hasattr(f, 'section') else f.get('section', '')
            lines.append("")
            lines.append(f"#{i} ε={ne:.1f} | Кластер {cid} | {sec}")
            lines.append(f"  {preview}...")
    return "\n".join(lines)


def analyze_large_file(filepath: str, keyword: str,
                       window_size: int = DEFAULT_WINDOW,
                       chunk_size: int = DEFAULT_CHUNK_SIZE,
                       top_n: int = 10, progress: bool = True,
                       phi_decay: float = 0.85, kappa: float = 1.0) -> Dict:
    """Streaming 2-pass анализ для больших текстовых файлов (v3).
    Для маленьких файлов делегирует в analyze_text.

    КРИТИЧНОЕ ИСПРАВЛЕНИЕ v6: если файл имеет расширение .epub/.zip/.tar/.gz/.tgz,
    НЕ пытаемся streaming'ом читать его как текст (это даст мусор из бинарника).
    Вместо этого вызываем read_file() — он раскроет архив/EPUB правильно."""
    if not keyword:
        keyword = ""

    # ─── v6 FIX: расширение-проверка ПЕРЕД streaming ───────────────
    p_path = Path(filepath)
    suffix = p_path.suffix.lower()
    name_lower = p_path.name.lower()
    is_binary_archive = (
        suffix in ('.epub', '.zip', '.gz')
        or name_lower.endswith(('.tar', '.tar.gz', '.tgz'))
    )
    if is_binary_archive:
        if progress:
            sys.stderr.write(f"-> Архив/EPUB: читаем через read_file() (не streaming)\n")
        text = read_file(filepath)
        if not text.strip():
            sys.stderr.write(f"[WARN] Пустой текст из {filepath}\n")
        return analyze_text(text, keyword, window_size, phi_decay, kappa,
                            top_n, filepath)
    # ────────────────────────────────────────────────────────────────

    file_size = os.path.getsize(filepath)

    if file_size < chunk_size:
        text = read_file(filepath)
        return analyze_text(text, keyword, window_size, phi_decay, kappa,
                            top_n, filepath)

    if progress:
        sys.stderr.write(f"-> Файл: {file_size:,} байт ({file_size / 1024 / 1024:.1f} МБ)\n")
        sys.stderr.write("-> Проход 1/2: сбор статистики...\n")

    global_counts: Counter = Counter()
    global_total = 0
    all_raw_positions: List[int] = []
    buffer = ""
    char_offset = 0
    pattern = re.compile(re.escape(keyword), re.IGNORECASE) if keyword else None

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                if buffer.strip():
                    tokens = tokenize(buffer)
                    global_counts.update(tokens)
                    global_total += len(tokens)
                    if pattern:
                        for m in pattern.finditer(buffer):
                            all_raw_positions.append(char_offset + m.start())
                break
            buffer += data
            if len(buffer) >= chunk_size:
                chunk_text = buffer[:chunk_size]
                buffer = buffer[chunk_size - window_size:]
                tokens = tokenize(chunk_text)
                global_counts.update(tokens)
                global_total += len(tokens)
                if pattern:
                    for m in pattern.finditer(chunk_text):
                        all_raw_positions.append(char_offset + m.start())
                char_offset += chunk_size - window_size

    if progress:
        sys.stderr.write("-> Проход 2/2: анализ фрагментов...\n")

    positions = deduplicate_positions(all_raw_positions)

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        full_text = f.read()
    sections = detect_sections(full_text)

    all_fragments: List[Fragment] = []
    char_offset = 0
    buffer = ""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                if buffer.strip():
                    chunk_frags = _extract_fragments_streaming(
                        buffer, char_offset, keyword, window_size,
                        global_counts, global_total, sections
                    )
                    all_fragments.extend(chunk_frags)
                break
            buffer += data
            if len(buffer) >= chunk_size:
                chunk_text = buffer[:chunk_size]
                buffer = buffer[chunk_size - window_size:]
                chunk_frags = _extract_fragments_streaming(
                    chunk_text, char_offset, keyword, window_size,
                    global_counts, global_total, sections
                )
                all_fragments.extend(chunk_frags)
                char_offset += chunk_size - window_size

    norm_eps = normalize_epsilons([f.epsilon for f in all_fragments])
    for f, ne in zip(all_fragments, norm_eps):
        f.normalized_epsilon = ne

    R = compute_resonance_series([f.normalized_epsilon for f in all_fragments], phi_decay)
    for f, r in zip(all_fragments, R):
        f.resonance = r

    clusters = cluster_fragments(all_fragments, sections=sections, adaptive=True)
    for ci, cm in enumerate(clusters):
        for mi in cm:
            if mi < len(all_fragments):
                all_fragments[mi].cluster_id = ci

    if progress:
        sys.stderr.write(
            f"\r-> Готово: {len(all_fragments)} фрагм., "
            f"{len(all_raw_positions)} вхождений     \n"
        )

    sorted_frags = sorted(all_fragments, key=lambda f: f.normalized_epsilon, reverse=True)
    top_frags = sorted_frags[:top_n] if top_n > 0 else sorted_frags
    top_output = []
    for i, f in enumerate(top_frags, 1):
        display = re.sub(r'\s+', ' ', f.text).strip()
        if len(display) > 1500:
            display = display[:1500] + '...'
        top_output.append({
            'rank': i, 'position': f.position, 'end_position': f.end_position,
            'epsilon': round(f.epsilon, 4),
            'normalized_epsilon': round(f.normalized_epsilon, 2),
            'resonance': round(f.resonance, 2),
            'cluster': f.cluster_id, 'section': f.section,
            'keyword_count': f.keyword_count,
            'source_file': filepath, 'text': display,
        })

    sections_output = []
    for s in sections:
        fc = sum(1 for f in all_fragments if f.section == s['title'])
        sections_output.append({'title': s['title'], 'position': s['position'],
                                'fragments': fc})

    summary = build_summary(keyword, filepath, file_size, global_total,
                            len(all_raw_positions), len(all_fragments),
                            len(clusters), all_fragments, clusters, sections_output)

    return {
        'keyword': keyword, 'file_path': filepath, 'file_size': file_size,
        'total_characters': len(full_text), 'total_words': global_total,
        'total_occurrences': len(all_raw_positions),
        'unique_fragments': len(all_fragments),
        'clusters': clusters,
        'top_fragments': top_output,
        'sections': sections_output,
        'full_summary': summary,
        'search_index': build_search_index(full_text),
    }


def _extract_fragments_streaming(text: str, char_offset: int, keyword: str,
                                  window_size: int, global_counts: Counter,
                                  global_total: int,
                                  sections: List[Dict]) -> List[Fragment]:
    """Извлечение фрагментов из чанка с глобальными частотами (streaming)."""
    cleaned, _ = filter_pii(text)
    pattern = re.compile(re.escape(keyword), re.IGNORECASE) if keyword else None
    if pattern:
        positions = [m.start() for m in pattern.finditer(cleaned)]
    else:
        positions = list(range(0, len(cleaned), max(window_size, 1)))
    positions = deduplicate_positions(positions)
    fragments: List[Fragment] = []
    for pos in positions:
        start = max(0, pos - window_size // 2)
        end = min(len(cleaned), pos + window_size // 2)
        raw_text = cleaned[start:end]
        tokens = tokenize(raw_text)
        eps, kw_count = compute_epsilon_v4(tokens, raw_text, keyword,
                                            global_counts, global_total, 1.0)
        section = get_section_for_position(sections, char_offset + pos)
        display = re.sub(r'\s+', ' ', raw_text).strip()
        if len(display) > 1500:
            display = display[:1500] + '...'
        fragments.append(Fragment(
            position=char_offset + pos, end_position=char_offset + end,
            text=display, epsilon=eps, section=section,
            keyword_count=kw_count, source_file="",
        ))
    return fragments


def run_poler_analyzer(text: str, keyword: str, window_size: int = DEFAULT_WINDOW,
                       phi_decay: float = 0.85, kappa: float = 1.0,
                       top_n: int = 10, source_file: str = "") -> Dict:
    """v2-совместимый анализатор с v3 нормализацией ε.
    Возвращает v2-формат (windows/summary/top_by_epsilon) + v3 поля."""
    result = analyze_text(text, keyword, window_size, phi_decay, kappa,
                          top_n, source_file)
    top_eps = result['top_fragments']
    total_eps = sum(f['epsilon'] for f in top_eps)
    v2_summary = {
        'keyword': keyword,
        'total_text_length': result['total_characters'],
        'total_words': result['total_words'],
        'unique_words': len(result.get('search_index', {})),
        'total_windows': result['unique_fragments'],
        'total_pii': 0,
        'total_epsilon': total_eps,
        'avg_epsilon': total_eps / len(top_eps) if top_eps else 0,
        'avg_resonance': sum(f['resonance'] for f in top_eps) / len(top_eps) if top_eps else 0,
        'peak_epsilon': top_eps[0]['epsilon'] if top_eps else 0,
        'peak_epsilon_window': top_eps[0]['rank'] - 1 if top_eps else -1,
        'peak_resonance': top_eps[0]['resonance'] if top_eps else 0,
        'peak_resonance_window': top_eps[0]['rank'] - 1 if top_eps else -1,
        'source_file': source_file,
    }
    return {
        'keyword': keyword,
        'config': {'window_size': window_size, 'phi_decay': phi_decay,
                   'kappa': kappa, 'top_n': top_n},
        'windows': [{
            'index': i, 'keyword': keyword, 'position': f['position'],
            'cleaned_text': f['text'], 'epsilon': f['epsilon'],
            'normalized_epsilon': f['normalized_epsilon'],
            'resonance': f['resonance'], 'source_file': source_file,
            'section': f['section'], 'cluster': f['cluster'],
        } for i, f in enumerate(top_eps)],
        'summary': v2_summary,
        'phase_log': {
            'perception': result['total_occurrences'],
            'images': result['unique_fragments'],
            'pii_filtered': 0,
            'epsilon_computed': result['unique_fragments'],
            'resonance_computed': result['unique_fragments'],
        },
        'top_by_epsilon': top_eps,
        'top_by_resonance': sorted(top_eps, key=lambda x: -x.get('resonance', 0)),
        'source_file': source_file,
        'sections': result['sections'],
        'clusters': result['clusters'],
        'full_summary': result['full_summary'],
        'fragments': result['fragments'],
    }

# ════════════════════════════════════════════════════════════════════════
# АНАЛИЗ ДИРЕКТОРИИ (v2 + v3 + v4)
# ════════════════════════════════════════════════════════════════════════

def analyze_directory(dir_path: str, keyword: str,
                      window_size: int = DEFAULT_WINDOW,
                      phi_decay: float = 0.85, kappa: float = 1.0,
                      top_n: int = 5, cross_resonance: bool = False,
                      extensions: Optional[List[str]] = None,
                      include_images: bool = False,
                      quiet: bool = False) -> Dict:
    """Анализ всех файлов в директории по одному ключевому слову.
    v4: кросс-файловый резонанс + кросс-файловая кластеризация (Jaccard)."""
    files = scan_directory(dir_path, extensions)
    if not quiet:
        sys.stderr.write(f"Сканировано файлов: {len(files)}\n")

    all_fragments: List[Fragment] = []
    per_file_results: List[Dict] = []
    files_with_hits = 0
    processed = 0

    for fpath in files:
        processed += 1
        if not quiet and len(files) > 10 and (processed % max(1, len(files) // 20) == 0 or processed == len(files)):
            pct = processed * 100 // len(files)
            bar_len = 20
            filled = bar_len * processed // len(files)
            bar = '█' * filled + '░' * (bar_len - filled)
            sys.stderr.write(f"\r  [{bar}] {pct:3d}% ({processed}/{len(files)})")
            sys.stderr.flush()

        suffix = Path(fpath).suffix.lower()
        name_lower = Path(fpath).name.lower()

        # PNG — метаданные
        if suffix == '.png':
            if not include_images:
                continue
            meta = read_png_metadata(fpath)
            if keyword and keyword.lower() in meta['text'].lower():
                frag = Fragment(
                    position=0, end_position=0, text=meta['text'],
                    epsilon=10.0, normalized_epsilon=10.0,
                    source_file=fpath, section="(image)",
                )
                all_fragments.append(frag)
                files_with_hits += 1
                per_file_results.append({
                    'file': fpath,
                    'summary': {
                        'total_windows': 1, 'total_epsilon': 10.0,
                        'peak_epsilon': 10.0, 'peak_resonance': 0.0,
                    }
                })
            continue

        # Архивы — раскрываем виртуальные файлы
        is_archive = (suffix == '.zip' or
                      name_lower.endswith(('.tar', '.tar.gz', '.tgz')) or
                      suffix == '.gz')
        if is_archive:
            for inner_name, inner_text in read_archive(fpath):
                if not inner_text.strip():
                    continue
                virtual_path = f"{fpath}::{inner_name}"
                result = analyze_text(inner_text, keyword, window_size,
                                      phi_decay, kappa, top_n=0,
                                      source_file=virtual_path)
                if result['unique_fragments'] > 0:
                    for fd in result['fragments']:
                        frag = Fragment(
                            position=fd['position'], end_position=fd['end_position'],
                            text=fd.get('text', ''), epsilon=fd['epsilon'],
                            normalized_epsilon=fd['normalized_epsilon'],
                            resonance=fd.get('resonance', 0),
                            section=fd['section'], keyword_count=fd['keyword_count'],
                            source_file=virtual_path,
                        )
                        all_fragments.append(frag)
                    files_with_hits += 1
                    per_file_results.append({
                        'file': virtual_path,
                        'summary': {
                            'total_windows': result['unique_fragments'],
                            'total_epsilon': sum(f['epsilon'] for f in result['fragments']),
                            'peak_epsilon': max((f['epsilon'] for f in result['fragments']), default=0),
                            'peak_resonance': max((f['resonance'] for f in result['fragments']), default=0),
                        }
                    })
            continue

        # Текстовые файлы
        text = read_file(fpath)
        if not text.strip():
            continue
        result = analyze_text(text, keyword, window_size, phi_decay, kappa,
                              top_n=0, source_file=fpath)
        if result['unique_fragments'] > 0:
            files_with_hits += 1
            for fd in result['fragments']:
                frag = Fragment(
                    position=fd['position'], end_position=fd['end_position'],
                    text=fd.get('text', ''), epsilon=fd['epsilon'],
                    normalized_epsilon=fd['normalized_epsilon'],
                    resonance=fd.get('resonance', 0),
                    section=fd['section'], keyword_count=fd['keyword_count'],
                    source_file=fpath,
                )
                all_fragments.append(frag)
            per_file_results.append({
                'file': fpath,
                'summary': {
                    'total_windows': result['unique_fragments'],
                    'total_epsilon': sum(f['epsilon'] for f in result['fragments']),
                    'peak_epsilon': max((f['epsilon'] for f in result['fragments']), default=0),
                    'peak_resonance': max((f['resonance'] for f in result['fragments']), default=0),
                }
            })

    if not quiet and len(files) > 10:
        sys.stderr.write("\n")

    # Кросс-файловый резонанс (v2)
    if cross_resonance and all_fragments:
        cross_R = compute_cross_resonance(
            [f.normalized_epsilon for f in all_fragments], phi_decay
        )
        for f, r in zip(all_fragments, cross_R):
            f.resonance = r

    # Кросс-файловая кластеризация (v4 — Jaccard по токенам)
    cross_clusters = []
    if len(all_fragments) > 1:
        cross_clusters = cluster_cross_file(all_fragments, threshold=0.25,
                                            max_fragments=80)

    # Глобальный топ по ε
    all_sorted = sorted(all_fragments, key=lambda f: f.normalized_epsilon, reverse=True)
    top_global = all_sorted[:top_n] if top_n > 0 else all_sorted

    top_output = []
    for i, f in enumerate(top_global, 1):
        display = re.sub(r'\s+', ' ', f.text).strip()
        if len(display) > 2000:
            display = display[:2000] + '...'
        top_output.append({
            'rank': i, 'epsilon': round(f.epsilon, 4),
            'normalized_epsilon': round(f.normalized_epsilon, 2),
            'resonance': round(f.resonance, 2),
            'source_file': f.source_file,
            'section': f.section,
            'keyword_count': f.keyword_count,
            'position': f.position,
            'cleaned_text': display,
        })

    cross_clusters_out = []
    for ci, group in enumerate(cross_clusters):
        files_in = set()
        for idx in group:
            if idx < len(all_fragments):
                files_in.add(all_fragments[idx].source_file)
        if len(files_in) >= 2:
            cross_clusters_out.append({
                'cluster_id': ci,
                'size': len(group),
                'files': list(files_in)[:10],
                'avg_epsilon': round(
                    sum(all_fragments[idx].normalized_epsilon for idx in group
                        if idx < len(all_fragments)) / len(group), 2
                ),
            })

    return {
        'keyword': keyword,
        'directory': dir_path,
        'files_scanned': len(files),
        'files_with_hits': files_with_hits,
        'total_windows': len(all_fragments),
        'cross_resonance': cross_resonance,
        'cross_clusters': cross_clusters_out,
        'top_by_epsilon': top_output,
        'per_file_results': per_file_results,
    }

# ════════════════════════════════════════════════════════════════════════
# DIFF (v2)
# ════════════════════════════════════════════════════════════════════════

def diff_files(file1: str, file2: str, keyword,
               window_size: int = DEFAULT_WINDOW) -> Dict:
    """Сравнение двух файлов по ключевому слову (v2 + v6.1 FIX Bug 6).

    v6.1: keyword может быть строкой ИЛИ списком строк. Если список —
    diff выполняется по каждому ключу отдельно и результаты агрегируются
    (суммарные вхождения и ε). Это исправляет Bug 6: раньше
    `diff -k "POLER,SCF,DM"` искал всю строку как одно слово → 0 совпадений.
    """
    # v6.1: нормализуем keyword в список
    if isinstance(keyword, str):
        keywords = [keyword] if keyword else []
    else:
        keywords = list(keyword)

    if not keywords:
        return {
            'keyword': '',
            'file1': file1, 'file2': file2,
            'file1_windows': 0, 'file2_windows': 0,
            'file1_epsilon': 0.0, 'file2_epsilon': 0.0,
            'delta_windows': 0, 'delta_epsilon': 0.0,
            'file1_top': [], 'file2_top': [],
            'per_keyword': [],
        }

    text1 = read_file(file1)
    text2 = read_file(file2)

    total_f1_windows = 0
    total_f2_windows = 0
    total_f1_eps = 0.0
    total_f2_eps = 0.0
    all_f1_top: List[Dict] = []
    all_f2_top: List[Dict] = []
    per_keyword: List[Dict] = []

    for kw in keywords:
        r1 = analyze_text(text1, kw, window_size, source_file=file1)
        r2 = analyze_text(text2, kw, window_size, source_file=file2)
        f1_eps = sum(f['epsilon'] for f in r1['fragments']) if r1['fragments'] else 0
        f2_eps = sum(f['epsilon'] for f in r2['fragments']) if r2['fragments'] else 0
        total_f1_windows += r1['unique_fragments']
        total_f2_windows += r2['unique_fragments']
        total_f1_eps += f1_eps
        total_f2_eps += f2_eps
        # Сохраняем топ-1 для каждого ключевого слова (агрегированный топ)
        if r1['top_fragments']:
            all_f1_top.append(r1['top_fragments'][0])
        if r2['top_fragments']:
            all_f2_top.append(r2['top_fragments'][0])
        per_keyword.append({
            'keyword': kw,
            'file1_windows': r1['unique_fragments'],
            'file2_windows': r2['unique_fragments'],
            'file1_epsilon': f1_eps,
            'file2_epsilon': f2_eps,
            'delta_windows': r2['unique_fragments'] - r1['unique_fragments'],
            'delta_epsilon': f2_eps - f1_eps,
        })

    # Сортируем агрегированный топ по ε (по убыванию)
    all_f1_top.sort(key=lambda f: -f.get('normalized_epsilon', 0))
    all_f2_top.sort(key=lambda f: -f.get('normalized_epsilon', 0))

    return {
        'keyword': ', '.join(keywords),
        'keywords_list': keywords,
        'file1': file1,
        'file2': file2,
        'file1_windows': total_f1_windows,
        'file2_windows': total_f2_windows,
        'file1_epsilon': total_f1_eps,
        'file2_epsilon': total_f2_eps,
        'delta_windows': total_f2_windows - total_f1_windows,
        'delta_epsilon': total_f2_eps - total_f1_eps,
        'file1_top': all_f1_top[:3],
        'file2_top': all_f2_top[:3],
        'per_keyword': per_keyword,
    }

# ════════════════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ════════════════════════════════════════════════════════════════════════

def _fmt(n: float, digits: int = 2) -> str:
    return f'{n:,.{digits}f}'


def _clean_for_display(text: str, max_chars: int = 3000) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_chars:
        cut = text.rfind(' ', 0, max_chars)
        text = text[:cut if cut != -1 else max_chars] + ' …'
    return text


def _highlight_md(text: str, keyword: str) -> str:
    if not keyword:
        return text
    return re.sub(f'({re.escape(keyword)})', r'**\1**', text, flags=re.IGNORECASE)


def format_text(result: Dict) -> str:
    """Текстовый вывод (v3 — возвращает full_summary)."""
    if isinstance(result, dict):
        return result.get('full_summary', '')
    return str(result)


def format_json(result: Any) -> str:
    """JSON-вывод (без search_index — он большой)."""
    if isinstance(result, dict):
        output = {k: v for k, v in result.items() if k != 'search_index'}
        return json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if isinstance(result, list):
        cleaned = []
        for r in result:
            if isinstance(r, dict):
                cleaned.append({k: v for k, v in r.items() if k != 'search_index'})
            else:
                cleaned.append(r)
        return json.dumps(cleaned, ensure_ascii=False, indent=2, default=str)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def format_markdown(result: Dict) -> str:
    """MD-вывод для одного файла (v3 + v2 поля)."""
    r = result
    lines = [f"# POLER[n] v{__version__} — Анализ «{r.get('keyword', '')}»", ""]
    lines.append(f"> Файл: `{r.get('file_path', '')}` "
                 f"({r.get('file_size', 0):,} байт)")
    lines.append(f"> Слов: {r.get('total_words', 0):,} · "
                 f"Вхождений: {r.get('total_occurrences', 0)} · "
                 f"Фрагментов: {r.get('unique_fragments', 0)}")
    lines.append("")
    if r.get('sections'):
        active = [s for s in r['sections'] if s.get('fragments', 0) > 0]
        if active:
            lines.append("## Секции с вхождениями")
            lines.append("")
            for s in sorted(active, key=lambda x: -x.get('fragments', 0))[:15]:
                lines.append(f"- **{s['title'][:50]}** — {s.get('fragments', 0)} фрагм.")
            lines.append("")
    if r.get('top_fragments'):
        lines.append("## Топ фрагментов")
        lines.append("")
        for f in r['top_fragments']:
            lines.append(f"### #{f.get('rank', '?')} "
                         f"ε={f.get('normalized_epsilon', 0):.1f} "
                         f"R={f.get('resonance', 0):.1f}")
            lines.append(f"> Позиция: {f.get('position', 0):,}")
            if f.get('section'):
                lines.append(f"> Секция: {f['section']}")
            if f.get('cluster', -1) >= 0:
                lines.append(f"> Кластер: {f['cluster']}")
            lines.append("")
            lines.append("```")
            display = re.sub(r'\s+', ' ', f.get('text', ''))[:1500]
            lines.append(_highlight_md(display, r.get('keyword', '')))
            lines.append("```")
            lines.append("")
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def format_directory_text(result: Dict) -> str:
    """Текстовый вывод для директории."""
    lines = [f"POLER[n] v{__version__} — Анализ директории", ""]
    lines.append(f"Директория: {result['directory']}")
    lines.append(f"Ключевое слово: «{result['keyword']}»")
    lines.append(f"Файлов просканировано: {result['files_scanned']}")
    lines.append(f"Файлов с совпадениями: {result['files_with_hits']}")
    lines.append(f"Всего фрагментов: {result['total_windows']}")
    lines.append(f"Кросс-резонанс: {'ДА' if result['cross_resonance'] else 'НЕТ'}")
    if result.get('cross_clusters'):
        lines.append(f"Кросс-файловых кластеров: {len(result['cross_clusters'])}")
    lines.append("")
    lines.append("=== ТОП ФРАГМЕНТОВ ===")
    for w in result['top_by_epsilon']:
        lines.append(f"  #{w['rank']} ε={w['normalized_epsilon']:.1f} "
                     f"R={w.get('resonance', 0):.1f}  {Path(w['source_file']).name}")
        text = re.sub(r'\s+', ' ', w.get('cleaned_text', ''))[:200]
        lines.append(f"     {text}")
        lines.append("")
    if result.get('cross_clusters'):
        lines.append("=== КРОСС-ФАЙЛОВЫЕ КЛАСТЕРЫ ===")
        for c in result['cross_clusters'][:10]:
            lines.append(f"  Кластер {c['cluster_id']}: {c['size']} фрагм., "
                         f"ср.ε={c['avg_epsilon']:.1f}, файлов: {len(c['files'])}")
        lines.append("")
    return "\n".join(lines)


def format_directory_markdown(result: Dict) -> str:
    """MD-отчёт для мультфайлного анализа (v2 + v4 cross_clusters)."""
    lines: List[str] = []
    lines.append(f"# POLER[n] v{__version__} — Анализ директории «{result['keyword']}»")
    lines.append("")
    lines.append(f"> Сканировано файлов: {result['files_scanned']} · "
                 f"С совпадениями: {result['files_with_hits']} · "
                 f"Всего фрагментов: {result['total_windows']} · "
                 f"Кросс-резонанс: {'ДА' if result['cross_resonance'] else 'НЕТ'}")
    lines.append(f"> Директория: `{result['directory']}`")
    lines.append("")

    lines.append(f"## Топ-{len(result['top_by_epsilon'])} фрагментов по ε")
    lines.append("")
    for w in result['top_by_epsilon']:
        lines.append(f"### {w['rank']}. "
                     f"ε={_fmt(w['normalized_epsilon'], 1)} · "
                     f"R={_fmt(w.get('resonance', 0), 1)}")
        lines.append(f"> Файл: `{w['source_file']}`")
        if w.get('section'):
            lines.append(f"> Секция: {w['section']}")
        lines.append("")
        text = _clean_for_display(w['cleaned_text'], 1500)
        highlighted = _highlight_md(text, result['keyword'])
        lines.append("```")
        lines.append(highlighted)
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    if result.get('cross_clusters'):
        lines.append("## Кросс-файловые кластеры")
        lines.append("")
        lines.append("| Кластер | Фрагментов | Ср. ε | Файлов |")
        lines.append("|---------|-----------|-------|--------|")
        for c in result['cross_clusters'][:15]:
            lines.append(f"| {c['cluster_id']} | {c['size']} | "
                         f"{_fmt(c['avg_epsilon'], 1)} | {len(c['files'])} |")
        lines.append("")

    lines.append("## Пофайловая сводка")
    lines.append("")
    lines.append("| Файл | Вхождений | Σ ε | Peak ε | Peak R[n] |")
    lines.append("|------|-----------|-----|--------|-----------|")
    for r in sorted(result['per_file_results'],
                    key=lambda x: -x['summary']['total_epsilon'] if x['summary'] else 0):
        s = r['summary']
        if s:
            fname = r['file']
            if '::' in fname:
                fname = fname.split('::')
                fname = f"{Path(fname[0]).name}::{fname[1]}"
            else:
                fname = Path(fname).name
            lines.append(f"| `{fname}` | {s['total_windows']} | "
                         f"{_fmt(s['total_epsilon'], 0)} | "
                         f"{_fmt(s['peak_epsilon'], 0)} | "
                         f"{_fmt(s['peak_resonance'], 0)} |")
    lines.append("")
    return "\n".join(lines)


def format_diff_markdown(result: Dict) -> str:
    """MD-отчёт для diff-режима (v2)."""
    lines: List[str] = []
    lines.append(f"# POLER[n] v{__version__} Diff — «{result['keyword']}»")
    lines.append("")
    lines.append("| Параметр | Файл 1 | Файл 2 | Δ |")
    lines.append("|------|------|------|---|")
    lines.append(f"| Файл | `{Path(result['file1']).name}` | "
                 f"`{Path(result['file2']).name}` | |")
    lines.append(f"| Вхождений | {result['file1_windows']} | "
                 f"{result['file2_windows']} | {result['delta_windows']:+d} |")
    lines.append(f"| Σ ε | {_fmt(result['file1_epsilon'], 0)} | "
                 f"{_fmt(result['file2_epsilon'], 0)} | "
                 f"{_fmt(result['delta_epsilon'], 0)} |")
    lines.append("")
    if result.get('file1_top'):
        lines.append("## Топ Файла 1")
        lines.append("")
        for f in result['file1_top']:
            lines.append(f"- ε={f.get('normalized_epsilon', 0):.1f} "
                         f"@ {f.get('position', 0):,}: "
                         f"{re.sub(r'\\s+', ' ', f.get('text', ''))[:200]}...")
        lines.append("")
    if result.get('file2_top'):
        lines.append("## Топ Файла 2")
        lines.append("")
        for f in result['file2_top']:
            lines.append(f"- ε={f.get('normalized_epsilon', 0):.1f} "
                         f"@ {f.get('position', 0):,}: "
                         f"{re.sub(r'\\s+', ' ', f.get('text', ''))[:200]}...")
        lines.append("")
    return "\n".join(lines)


def format_search_results(results: List[Dict], query: str, fmt: str = 'text') -> str:
    """Форматирование результатов full-text search."""
    if fmt == 'json':
        return json.dumps(results, ensure_ascii=False, indent=2)
    lines = [f"=== ПОИСК: \"{query}\" ===", ""]
    lines.append(f"Найдено результатов: {len(results)}")
    lines.append("")
    for i, r in enumerate(results, 1):
        lines.append(f"#{i} [score={r['score']}] позиция={r['position']:,} "
                     f"(слов: {r['matched_words']}/{r['total_query_words']})")
        lines.append(f"  {r['context'][:300]}")
        lines.append("")
    return "\n".join(lines)

# ════════════════════════════════════════════════════════════════════════
# HTTP API (v4 + v5 grep endpoints)
# ════════════════════════════════════════════════════════════════════════

def _read_body(handler: BaseHTTPRequestHandler) -> Dict:
    length = int(handler.headers.get('Content-Length', 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw)


def _send_json(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')
    handler.send_header('Content-Length', len(body))
    handler.end_headers()
    handler.wfile.write(body)


class PolerHandler(BaseHTTPRequestHandler):
    """HTTP API handler: /analyze, /analyze_many, /search, /grep, /grep_dir, /health."""

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            _send_json(self, {
                'status': 'ok',
                'version': __version__,
                'time': datetime.now().isoformat(),
            })
        elif path == '/' or path == '':
            _send_json(self, {
                'name': 'POLER[n] API',
                'version': __version__,
                'endpoints': {
                    'POST /analyze': 'Анализ файла или текста',
                    'POST /analyze_many': 'Пакетный анализ файлов',
                    'POST /search': 'Полнотекстовый поиск',
                    'POST /grep': 'Regex-поиск в файле (замена grep)',
                    'POST /grep_dir': 'Рекурсивный regex-поиск по директории',
                    'GET /health': 'Статус сервера',
                },
            })
        else:
            _send_json(self, {'error': 'Not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = _read_body(self)
        except Exception as e:
            _send_json(self, {'error': f'Invalid JSON: {e}'}, 400)
            return
        if path == '/analyze':
            self._handle_analyze(body)
        elif path == '/analyze_many':
            self._handle_analyze_many(body)
        elif path == '/search':
            self._handle_search(body)
        elif path == '/grep':
            self._handle_grep(body)
        elif path == '/grep_dir':
            self._handle_grep_dir(body)
        else:
            _send_json(self, {'error': 'Not found'}, 404)

    def _handle_analyze(self, body: Dict):
        keyword = body.get('keyword')
        if not keyword:
            _send_json(self, {'error': 'keyword required'}, 400)
            return
        window = int(body.get('window', DEFAULT_WINDOW))
        top = int(body.get('top', 10))
        try:
            if body.get('file'):
                fpath = body['file']
                if not os.path.exists(fpath):
                    _send_json(self, {'error': f'File not found: {fpath}'}, 404)
                    return
                result = analyze_large_file(fpath, keyword, window,
                                            top_n=top, progress=False)
            elif body.get('text'):
                result = analyze_text(body['text'], keyword, window,
                                      top_n=top, source_file='<text>')
            else:
                _send_json(self, {'error': 'file or text required'}, 400)
                return
            _send_json(self, {
                'keyword': result['keyword'],
                'file': result.get('file_path', '<text>'),
                'total_occurrences': result['total_occurrences'],
                'unique_fragments': result['unique_fragments'],
                'clusters': result.get('clusters', []),
                'top_fragments': result['top_fragments'],
                'sections': result.get('sections', []),
                'summary': result['full_summary'],
            })
        except Exception as e:
            _send_json(self, {'error': str(e)}, 500)

    def _handle_analyze_many(self, body: Dict):
        files = body.get('files', [])
        keyword = body.get('keyword')
        if not keyword:
            _send_json(self, {'error': 'keyword required'}, 400)
            return
        window = int(body.get('window', DEFAULT_WINDOW))
        top = int(body.get('top', 5))
        results: List[Dict] = []
        errors: List[Dict] = []
        for fpath in files:
            if not os.path.exists(fpath):
                errors.append({'file': fpath, 'error': 'not found'})
                continue
            try:
                r = analyze_large_file(fpath, keyword, window,
                                       top_n=top, progress=False)
                results.append({
                    'file': fpath,
                    'occurrences': r['total_occurrences'],
                    'fragments': r['unique_fragments'],
                    'top_fragments': r['top_fragments'][:3],
                })
            except Exception as e:
                errors.append({'file': fpath, 'error': str(e)})
        results.sort(key=lambda x: -x['occurrences'])
        _send_json(self, {
            'keyword': keyword,
            'files_analyzed': len(results),
            'files_failed': len(errors),
            'results': results,
            'errors': errors,
        })

    def _handle_search(self, body: Dict):
        query = body.get('query')
        if not query:
            _send_json(self, {'error': 'query required'}, 400)
            return
        try:
            if body.get('file'):
                fpath = body['file']
                if not os.path.exists(fpath):
                    _send_json(self, {'error': f'File not found: {fpath}'}, 404)
                    return
                text = read_file(fpath)
            elif body.get('text'):
                text = body['text']
            else:
                _send_json(self, {'error': 'file or text required'}, 400)
                return
            index = build_search_index(text)
            results = search_in_text(text, query, index)
            _send_json(self, {
                'query': query,
                'results': results,
                'count': len(results),
            })
        except Exception as e:
            _send_json(self, {'error': str(e)}, 500)

    def _handle_grep(self, body: Dict):
        """v5: regex-поиск в одном файле или тексте."""
        pattern = body.get('pattern') or body.get('query')
        if not pattern:
            _send_json(self, {'error': 'pattern required'}, 400)
            return
        try:
            text = body.get('text', '')
            filepath = body.get('file')
            if filepath and os.path.exists(filepath):
                results = grep_search_file(
                    filepath, pattern,
                    ignore_case=body.get('ignore_case', False),
                    whole_word=body.get('whole_word', False),
                    invert=body.get('invert', False),
                    count_only=body.get('count', False),
                    context_before=body.get('context_before', 0),
                    context_after=body.get('context_after', 0),
                    context_lines=body.get('context', 0),
                    max_matches=body.get('max_matches', 0),
                )
                _send_json(self, {
                    'pattern': pattern,
                    'file': filepath,
                    'matches': len(results),
                    'results': [
                        {'line_number': r.line_number, 'line': r.line,
                         'before': r.before_context, 'after': r.after_context}
                        for r in results
                    ],
                })
            elif text:
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                                 delete=False, encoding='utf-8') as f:
                    f.write(text)
                    tmp = f.name
                try:
                    results = grep_search_file(tmp, pattern,
                                                body.get('ignore_case', False))
                    _send_json(self, {
                        'pattern': pattern,
                        'matches': len(results),
                        'results': [
                            {'line_number': r.line_number, 'line': r.line}
                            for r in results
                        ],
                    })
                finally:
                    os.unlink(tmp)
            else:
                _send_json(self, {'error': 'file or text required'}, 400)
        except Exception as e:
            _send_json(self, {'error': str(e)}, 500)

    def _handle_grep_dir(self, body: Dict):
        """v5: рекурсивный regex-поиск по директории."""
        pattern = body.get('pattern') or body.get('query')
        dir_path = body.get('directory') or body.get('dir')
        if not pattern or not dir_path:
            _send_json(self, {'error': 'pattern and directory required'}, 400)
            return
        if not os.path.isdir(dir_path):
            _send_json(self, {'error': f'Not a directory: {dir_path}'}, 400)
            return
        try:
            results = grep_search_directory(
                dir_path, pattern,
                ignore_case=body.get('ignore_case', False),
                whole_word=body.get('whole_word', False),
                invert=body.get('invert', False),
                count_only=body.get('count', False),
                context_before=body.get('context_before', 0),
                context_after=body.get('context_after', 0),
                context_lines=body.get('context', 0),
                include=body.get('include'),
                exclude=body.get('exclude'),
                recursive=body.get('recursive', True),
            )
            total_matches = sum(len(v) for v in results.values())
            output = {
                'pattern': pattern,
                'directory': dir_path,
                'files_with_matches': len(results),
                'total_matches': total_matches,
                'results': {},
            }
            for fpath, fres in results.items():
                output['results'][fpath] = [
                    {'line_number': r.line_number, 'line': r.line,
                     'before': r.before_context, 'after': r.after_context}
                    for r in fres
                ]
            _send_json(self, output)
        except Exception as e:
            _send_json(self, {'error': str(e)}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}\n")


def run_api_server(port: int = DEFAULT_API_PORT) -> None:
    """Запуск HTTP API сервера."""
    server = HTTPServer(('0.0.0.0', port), PolerHandler)
    print(f"POLER[n] v{__version__} — HTTP API")
    print(f"http://0.0.0.0:{port}")
    print("Endpoints:")
    print("  POST /analyze      — анализ файла/текста")
    print("  POST /analyze_many — пакетный анализ")
    print("  POST /search       — полнотекстовый поиск")
    print("  POST /grep         — regex-поиск (замена grep)")
    print("  POST /grep_dir     — рекурсивный regex-поиск по папке")
    print("  GET  /health       — статус сервера")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        server.server_close()

# ════════════════════════════════════════════════════════════════════════
# PYTHON API
# ════════════════════════════════════════════════════════════════════════

class PolerAnalyzer:
    """Объединённый Python API (v2 + v3 + v4 + v5)."""

    def __init__(self, window: int = DEFAULT_WINDOW, phi: float = 0.85,
                 kappa: float = 1.0, top: int = 10):
        self.window = window
        self.phi = phi
        self.kappa = kappa
        self.top = top

    def analyze_file(self, filepath: str, keyword: str) -> Dict:
        """Анализ одного файла (авто-streaming для больших)."""
        return analyze_large_file(filepath, keyword, self.window,
                                  top_n=self.top, progress=False,
                                  phi_decay=self.phi, kappa=self.kappa)

    def analyze_text(self, text: str, keyword: str) -> Dict:
        """Анализ текстовой строки."""
        return analyze_text(text, keyword, self.window, self.phi,
                            self.kappa, self.top)

    def analyze_directory(self, dir_path: str, keyword: str,
                          cross_resonance: bool = False,
                          include_images: bool = False) -> Dict:
        """Анализ всей директории."""
        return analyze_directory(dir_path, keyword, self.window, self.phi,
                                 self.kappa, self.top, cross_resonance,
                                 include_images=include_images)

    def analyze_epub(self, epub_path: str, keyword: str) -> Dict:
        """Анализ EPUB файла."""
        text = read_epub(epub_path)
        return analyze_text(text, keyword, self.window, self.phi,
                            self.kappa, self.top, epub_path)

    def search(self, text: str, query: str) -> List[Dict]:
        """Полнотекстовый поиск."""
        index = build_search_index(text)
        return search_in_text(text, query, index)

    def grep(self, filepath: str, pattern: str, **kwargs) -> List[GrepResult]:
        """v5: regex-поиск в файле (замена grep)."""
        return grep_search_file(filepath, pattern, **kwargs)

    def grep_dir(self, dir_path: str, pattern: str, **kwargs) -> Dict[str, List[GrepResult]]:
        """v5: рекурсивный regex-поиск по директории."""
        return grep_search_directory(dir_path, pattern, **kwargs)

    def diff(self, file1: str, file2: str, keyword: str) -> Dict:
        """Сравнение двух файлов."""
        return diff_files(file1, file2, keyword, self.window)

# ════════════════════════════════════════════════════════════════════════
# CLI (v6: subcommands + backward compat с v4)
# ════════════════════════════════════════════════════════════════════════

def _build_subcommand_parser() -> argparse.ArgumentParser:
    """Парсер с subcommands: analyze | grep | analyze_dir | diff | api."""
    parser = argparse.ArgumentParser(
        prog='poler6',
        description=f'POLER[n] v{__version__} — Final Unified Build',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Subcommands:
  analyze      Анализ ключевого слова в файле
  grep         Regex-поиск (замена grep/ripgrep)
  analyze_dir  Анализ директории (рекурсивный, с темами)
  diff         Сравнение двух файлов по ключевому слову
  api          Запустить HTTP API сервер

Обратная совместимость с v4:
  poler_v6.py ФАЙЛ -k "слово" --top 3
  poler_v6.py ДИР/ -r -k "слово" --top 3
  poler_v6.py ДИР/ -r --theme биология --top 3
  poler_v6.py ФАЙЛ -s "поисковый запрос"
  poler_v6.py --diff v1.md v2.md -k "сфер"
  poler_v6.py --api --port 8000

Примеры subcommands:
  poler_v6.py analyze download/37_Numerologiya.md -k "Багуа" --top 3
  poler_v6.py grep "Нокс" download/36_Inertny.md -n -C 1 --max-matches 3
  poler_v6.py analyze_dir download/ -r -k "Нокс" --top 3
  poler_v6.py analyze_dir download/ -r --theme "биология" --top 2
  poler_v6.py analyze "upload/Etheria_Geography_FULL.epub" -k "резоносом" --top 2
  poler_v6.py grep "баг округления" download/37_Numerologiya.md -n
  poler_v6.py diff f1.md f2.md -k "сфер" -f md
  poler_v6.py api --port 8000
""",
    )
    sub = parser.add_subparsers(dest='command')

    # analyze ФАЙЛ [-k] [-w] [--top] [-f text/json/md] [-o] [--quiet]
    p_analyze = sub.add_parser('analyze',
                                help='Анализ ключевого слова в файле')
    p_analyze.add_argument('input', help='Файл для анализа')
    p_analyze.add_argument('-k', '--keyword', default='', help='Ключевое слово')
    p_analyze.add_argument('-w', '--window', type=int, default=DEFAULT_WINDOW,
                            help=f'Размер окна (по умолч. {DEFAULT_WINDOW})')
    p_analyze.add_argument('--phi', type=float, default=0.85, help='Затухание R[n]')
    p_analyze.add_argument('--kappa', type=float, default=1.0, help='Интенсивность ε')
    p_analyze.add_argument('--top', type=int, default=10, help='Топ-N результатов')
    p_analyze.add_argument('-f', '--format', choices=['text', 'json', 'md'],
                            default='text', help='Формат вывода')
    p_analyze.add_argument('--theme', choices=list(THEMES.keys()) + ['auto'],
                            help='Автотема (биология/астрономия/география/...). '
                                 'v6.1: "auto" — определить тему по содержимому файла.')
    p_analyze.add_argument('-o', '--output', help='Сохранить в файл')
    p_analyze.add_argument('--quiet', action='store_true', help='Тихий режим')
    p_analyze.add_argument('--auto-chunk', action='store_true',
                            help='Авто-chunk: бить файл на кластеры, выводить по очереди')
    p_analyze.add_argument('--read-cluster', type=int, default=-1,
                            help='Прочитать только кластер N (0-indexed)')
    p_analyze.add_argument('--chunk-size', type=int, default=4000,
                            help='Макс. символов на кластер в auto-chunk (по умолч. 4000)')
    p_analyze.add_argument('--swap-file', default='',
                            help='Файл для swap (по умолч. /tmp/poler_swap_<pid>.json)')

    # grep "pattern" ФАЙЛ_ИЛИ_ДИР [-r] [-i] [-n] [-C N] [-A N] [-B N] [-c] [-v] [-w]
    #       [-f text/json] [--include GLOB] [--exclude GLOB] [--max-matches N]
    p_grep = sub.add_parser('grep',
                             help='Regex-поиск (замена grep/ripgrep)')
    p_grep.add_argument('pattern', help='Regex шаблон')
    p_grep.add_argument('input', help='Файл или директория')
    p_grep.add_argument('-r', '--recursive', action='store_true', default=True,
                         help='Рекурсивный обход (по умолчанию для директорий)')
    p_grep.add_argument('-i', '--ignore-case', action='store_true',
                         help='Без учёта регистра')
    p_grep.add_argument('-n', '--line-number', action='store_true', default=True,
                         help='Показать номера строк')
    p_grep.add_argument('--no-line-number', dest='line_number', action='store_false',
                         help='Скрыть номера строк')
    p_grep.add_argument('-C', '--context', type=int, default=0,
                         help='Контекст: N строк до и после')
    p_grep.add_argument('-A', '--after-context', type=int, default=0,
                         help='N строк после')
    p_grep.add_argument('-B', '--before-context', type=int, default=0,
                         help='N строк до')
    p_grep.add_argument('-c', '--count', action='store_true',
                         help='Только количество совпадений')
    p_grep.add_argument('-v', '--invert', action='store_true',
                         help='Инвертировать: строки БЕЗ совпадений')
    p_grep.add_argument('-w', '--whole-word', action='store_true',
                         help='Только целые слова')
    p_grep.add_argument('-f', '--format', choices=['text', 'json'], default='text',
                         help='Формат вывода')
    p_grep.add_argument('--include', help='Фильтр файлов (напр. "*.md")')
    p_grep.add_argument('--exclude', help='Исключить файлы (glob)')
    p_grep.add_argument('--max-matches', type=int, default=0,
                         help='Максимум совпадений на файл (0 = без лимита)')

    # analyze_dir ДИР [-r] [-k] [--theme ТЕМА] [--cross-resonance] [--top N]
    #           [--include-images] [-f text/json/md] [-o] [--quiet]
    p_adir = sub.add_parser('analyze_dir',
                             help='Анализ директории (рекурсивный, с темами)')
    p_adir.add_argument('input', help='Директория')
    p_adir.add_argument('-r', '--recursive', action='store_true', default=True,
                         help='Рекурсивный обход')
    p_adir.add_argument('-k', '--keyword', default='', help='Ключевое слово')
    p_adir.add_argument('--theme', choices=list(THEMES.keys()) + ['auto'],
                         help='Автотема (биология/астрономия/география/...). '
                              'v6.1: "auto" — определить тему по содержимому.')
    p_adir.add_argument('--cross-resonance', action='store_true',
                         help='Кросс-файловый резонанс')
    p_adir.add_argument('--top', type=int, default=5, help='Топ-N результатов')
    p_adir.add_argument('--include-images', action='store_true',
                         help='Включить PNG (по названию файла)')
    p_adir.add_argument('--multi', help='Несколько слов через запятую')
    p_adir.add_argument('--multi-mode',
                         choices=['phrase', 'all', 'any'], default='phrase',
                         help='v6.1: режим multi-word поиска. '
                              'phrase (по умолч.) — точная фраза; '
                              'all — все слова должны встретиться (set semantics); '
                              'any — любое из слов (union semantics)')
    p_adir.add_argument('-w', '--window', type=int, default=DEFAULT_WINDOW)
    p_adir.add_argument('--phi', type=float, default=0.85)
    p_adir.add_argument('--kappa', type=float, default=1.0)
    p_adir.add_argument('-f', '--format', choices=['text', 'json', 'md'],
                         default='text', help='Формат вывода')
    p_adir.add_argument('-o', '--output', help='Сохранить в файл')
    p_adir.add_argument('--quiet', action='store_true', help='Тихий режим')

    # diff Ф1 Ф2 [-k KEYWORD] [-f text/json/md]
    p_diff = sub.add_parser('diff', help='Сравнение двух файлов по ключевому слову')
    p_diff.add_argument('file1', help='Первый файл')
    p_diff.add_argument('file2', help='Второй файл')
    p_diff.add_argument('-k', '--keyword', default='', help='Ключевое слово')
    p_diff.add_argument('-w', '--window', type=int, default=DEFAULT_WINDOW)
    p_diff.add_argument('-f', '--format', choices=['text', 'json', 'md'],
                         default='text', help='Формат вывода')
    p_diff.add_argument('-o', '--output', help='Сохранить в файл')

    # api [--port 8000]
    p_api = sub.add_parser('api', help='Запустить HTTP API сервер')
    p_api.add_argument('--port', type=int, default=DEFAULT_API_PORT,
                        help=f'Порт (по умолч. {DEFAULT_API_PORT})')

    # smart ФАЙЛ [-k] [--top N] [--chunk-size S] [-f text/json] [--quiet]
    p_smart = sub.add_parser('smart',
                              help='Карта файла: секции + позиции + Read-команды')
    p_smart.add_argument('input', help='Файл (txt/md/epub/zip/tar)')
    p_smart.add_argument('-k', '--keyword', default='',
                          help='Ключевое слово (для подсчёта вхождений по секциям)')
    p_smart.add_argument('--top', type=int, default=5,
                          help='Топ-N секций по плотности ключевого слова')
    p_smart.add_argument('--chunk-size', type=int, default=4000,
                          help='Макс. символов на чанк (для разбиения больших секций)')
    p_smart.add_argument('-f', '--format', choices=['text', 'json'], default='text',
                          help='Формат вывода')
    p_smart.add_argument('--quiet', action='store_true', help='Тихий режим (только результат)')

    return parser


# ════════════════════════════════════════════════════════════════════════
# SMART FILE MAP — карта файла для AI
# ════════════════════════════════════════════════════════════════════════

def smart_file_map(filepath: str, keyword: str = "",
                    top_n: int = 5, max_chunk_chars: int = 4000) -> Dict:
    """
    Построить карту файла для AI.
    Не заменяет Read — направляет Read.
    
    Возвращает:
      - Секции файла (заголовок, позиция, размер, кол-во вхождений)
      - Топ-N секций по плотности ключевого слова
      - Read-инструкцию для каждой секции (offset + limit)
    """
    text = read_file(filepath)
    if not text or not text.strip():
        return {'error': 'empty file or cannot read', 'file': filepath}

    file_size = len(text.encode('utf-8'))
    lines = text.split('\n')
    total_lines = len(lines)
    sections = detect_sections(text)
    pattern = re.compile(re.escape(keyword), re.IGNORECASE) if keyword else None

    section_maps = []
    for i, section in enumerate(sections):
        start_pos = section['position']
        end_pos = sections[i + 1]['position'] if i + 1 < len(sections) else len(text)
        section_text = text[start_pos:end_pos]
        start_line = text[:start_pos].count('\n') + 1
        section_lines = section_text.count('\n') + 1
        kw_count = len(pattern.findall(section_text)) if pattern else 0
        char_count = len(section_text)

        # Если секция > max_chunk_chars — разбить по абзацам
        if char_count > max_chunk_chars:
            paragraphs = section_text.split('\n\n')
            current = []
            current_size = 0
            sub_idx = 0
            for para in paragraphs:
                if current_size + len(para) > max_chunk_chars and current:
                    sub_text = '\n\n'.join(current)
                    sub_start_line = start_line + section_text[:section_text.index(sub_text[:200])].count('\n') if sub_text[:200] in section_text else start_line
                    sub_lines = sub_text.count('\n') + 1
                    sub_kw = len(pattern.findall(sub_text)) if pattern else 0
                    sub_idx += 1
                    section_maps.append({
                        'id': len(section_maps),
                        'title': f"{section['title']} [{sub_idx}]",
                        'start_line': sub_start_line,
                        'line_count': sub_lines,
                        'char_count': len(sub_text),
                        'keyword_count': sub_kw,
                        'read_offset': sub_start_line,
                        'read_limit': sub_lines,
                    })
                    current = []
                    current_size = 0
                current.append(para)
                current_size += len(para)
            if current:
                sub_text = '\n\n'.join(current)
                sub_start_line = start_line + section_text[:section_text.index(sub_text[:200])].count('\n') if sub_text[:200] in section_text else start_line
                sub_lines = sub_text.count('\n') + 1
                sub_kw = len(pattern.findall(sub_text)) if pattern else 0
                sub_idx += 1
                section_maps.append({
                    'id': len(section_maps),
                    'title': f"{section['title']} [{sub_idx}]",
                    'start_line': sub_start_line,
                    'line_count': sub_lines,
                    'char_count': len(sub_text),
                    'keyword_count': sub_kw,
                    'read_offset': sub_start_line,
                    'read_limit': sub_lines,
                })
        else:
            section_maps.append({
                'id': len(section_maps),
                'title': section['title'],
                'start_line': start_line,
                'line_count': section_lines,
                'char_count': char_count,
                'keyword_count': kw_count,
                'read_offset': start_line,
                'read_limit': section_lines,
            })

    # Сортировка по плотности ключевого слова
    if keyword:
        section_maps.sort(key=lambda s: -s['keyword_count'])

    top_sections = section_maps[:top_n] if top_n > 0 else section_maps
    total_kw = sum(s['keyword_count'] for s in section_maps)

    # Оценка токенов
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    cyr_ratio = cyrillic / len(text) if text else 0
    est_tokens = len(text) // 2 if cyr_ratio > 0.5 else len(text) // 4 if cyr_ratio < 0.1 else len(text) // 3

    return {
        'file': filepath,
        'file_size': file_size,
        'file_size_human': f'{file_size / 1024:.0f} КБ',
        'total_lines': total_lines,
        'total_sections': len(section_maps),
        'keyword': keyword,
        'total_keyword_matches': total_kw,
        'estimated_tokens': est_tokens,
        'top_sections': top_sections,
        'all_sections': section_maps,
    }


def cmd_smart(args) -> None:
    """Subcommand: smart ФАЙЛ [-k] [--top] [--chunk-size] [-f] [--quiet]."""
    if not os.path.exists(args.input):
        sys.stderr.write(f"Файл не найден: {args.input}\n")
        sys.exit(1)

    result = smart_file_map(args.input, args.keyword, args.top, args.chunk_size)

    if 'error' in result:
        sys.stderr.write(f"Ошибка: {result['error']}\n")
        sys.exit(1)

    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Text format
    print(f"{'=' * 70}")
    print(f"  КАРТА ФАЙЛА: {args.input}")
    print(f"  Размер: {result['file_size_human']} | Строк: {result['total_lines']} | "
          f"~{result['estimated_tokens']:,} токенов")
    print(f"  Секций: {result['total_sections']}")
    if args.keyword:
        print(f"  Ключевое слово «{args.keyword}»: {result['total_keyword_matches']} вхождений")
    print(f"{'=' * 70}")
    print()

    # Все секции (кратко)
    print("ВСЕ СЕКЦИИ:")
    for s in result['all_sections']:
        kw_str = f" ⚑{s['keyword_count']}" if s['keyword_count'] > 0 else ""
        size_str = f"{s['char_count'] / 1024:.1f} КБ" if s['char_count'] > 1024 else f"{s['char_count']} Б"
        print(f"  #{s['id']:2d} {s['title'][:45]:<45s} | "
              f"стр.{s['start_line']:>5d}-{s['start_line'] + s['line_count'] - 1:<5d} | "
              f"{size_str:>8s}{kw_str}")
    print()

    # Топ-N (с Read-командами)
    top = result['top_sections']
    if args.keyword and top:
        print(f"ТОП-{len(top)} по «{args.keyword}» (с готовыми Read-командами):")
        print()
        for i, s in enumerate(top):
            size_str = f"{s['char_count'] / 1024:.1f} КБ" if s['char_count'] > 1024 else f"{s['char_count']} Б"
            print(f"  #{i + 1}: {s['title'][:50]}")
            print(f"      Строки: {s['start_line']}-{s['start_line'] + s['line_count'] - 1} | "
                  f"{size_str} | {s['keyword_count']} вхожд.")
            print(f"      → Read(\"{args.input}\", offset={s['read_offset']}, limit={s['read_limit']})")
            print()
    elif not args.keyword:
        print("Большие секции (> 4 КБ, требуют нескольких Read):")
        for s in result['all_sections']:
            if s['char_count'] > 4096:
                print(f"  {s['title'][:45]:<45s} | {s['char_count'] / 1024:.1f} КБ | "
                      f"Read(offset={s['read_offset']}, limit={s['read_limit']})")
        print()
        print("Для поиска по ключевому слову: poler_v6.py smart ФАЙЛ -k \"слово\"")


def cmd_analyze(args) -> None:
    """Subcommand: analyze ФАЙЛ [-k] [-w] [--top] [--theme] [--auto-chunk]
       [--read-cluster N] [--chunk-size S] [-f] [-o] [--quiet]."""
    if not os.path.exists(args.input):
        sys.stderr.write(f"Файл не найден: {args.input}\n")
        sys.exit(1)

    # --theme: запуск по словарю темы (аналог legacy --theme)
    # v6.1: --theme auto — определить тему по содержимому файла
    if hasattr(args, 'theme') and args.theme:
        if args.theme == 'auto':
            # v6.1: авто-определение темы по содержимому файла
            text_for_detect = read_file(args.input)
            detected, scores, distinct = detect_theme_with_scores(text_for_detect)
            if not args.quiet:
                # v6.1.1: показываем и score, и distinct words
                score_str = ', '.join(
                    f"{t}: score={s}, words={distinct[t]}" for t, s in
                    sorted(scores.items(), key=lambda x: -x[1])[:5]
                    if s > 0 or distinct[t] > 0
                ) or '(пусто)'
                sys.stderr.write(f"Тема «auto» → детектировано: «{detected}»\n")
                sys.stderr.write(f"  Scoreboard: {score_str}\n")
                sys.stderr.write(f"  Threshold: ≥{THEME_MIN_DISTINCT_WORDS} distinct слов "
                                 f"ИЛИ ≥{THEME_MIN_TOTAL_SCORE} total score\n")
            if detected == 'general':
                sys.stderr.write("  Нет уверенного совпадения ни с одной темой. Падение на обычный analyze.\n")
                # Падаем на обычный analyze без темы
                args.theme = None
            else:
                args.theme = detected
                if not args.quiet:
                    sys.stderr.write(f"  → используем словарь темы «{args.theme}» "
                                     f"({len(THEMES[args.theme])} слов)\n")

    if hasattr(args, 'theme') and args.theme:
        theme_words = THEMES.get(args.theme, [])
        if not args.quiet:
            sys.stderr.write(f"Тема «{args.theme}»: {len(theme_words)} слов\n")
        all_results = []
        for kw in theme_words:
            file_size = os.path.getsize(args.input)
            if file_size > DEFAULT_CHUNK_SIZE:
                r = analyze_large_file(args.input, kw, args.window,
                                       DEFAULT_CHUNK_SIZE, args.top,
                                       progress=False,
                                       phi_decay=args.phi, kappa=args.kappa)
            else:
                text = read_file(args.input)
                r = analyze_text(text, kw, args.window,
                                 args.phi, args.kappa, args.top,
                                 source_file=args.input)
            occ = r.get('total_occurrences', 0)
            frag = r.get('unique_fragments', 0)
            if not args.quiet:
                sys.stderr.write(f"  «{kw:<20s}»  вхождений={occ:4d}  фрагм.={frag:3d}\n")
            if occ > 0:
                all_results.append(r)
        # Вывод: топ по всем словам темы
        if all_results:
            best = max(all_results, key=lambda r: r.get('unique_fragments', 0))
            _emit(best, args.format, args.output, args.quiet)
        else:
            sys.stderr.write("Нет совпадений ни по одному слову темы.\n")
        return

    # Обычный analyze
    # v6.1.1 (Bug 6 ext): -k может содержать запятые → несколько keywords.
    # Если передано несколько, прогоняем analyze по каждому и объединяем
    # top_fragments (как в режиме темы). Один keyword — старый путь без overhead.
    raw_kw = args.keyword or ''
    keywords_list = [k.strip() for k in raw_kw.split(',') if k.strip()] if raw_kw else []
    if len(keywords_list) > 1:
        if not args.quiet:
            sys.stderr.write(f"  multi-keyword: {len(keywords_list)} слов "
                             f"({', '.join(keywords_list[:5])}"
                             f"{'...' if len(keywords_list) > 5 else ''})\n")
        all_results = []
        for kw in keywords_list:
            file_size = os.path.getsize(args.input)
            if file_size > DEFAULT_CHUNK_SIZE:
                r = analyze_large_file(args.input, kw, args.window,
                                       DEFAULT_CHUNK_SIZE, args.top,
                                       progress=False,
                                       phi_decay=args.phi, kappa=args.kappa)
            else:
                text = read_file(args.input)
                r = analyze_text(text, kw, args.window,
                                 args.phi, args.kappa, args.top,
                                 source_file=args.input)
            occ = r.get('total_occurrences', 0)
            if not args.quiet and occ > 0:
                sys.stderr.write(f"  «{kw:<20s}»  вхождений={occ:4d}\n")
            if occ > 0:
                all_results.append(r)
        if all_results:
            best = max(all_results, key=lambda r: r.get('unique_fragments', 0))
            _emit(best, args.format, args.output, args.quiet)
        else:
            sys.stderr.write("Нет совпадений ни по одному из keywords.\n")
        return

    single_kw = keywords_list[0] if keywords_list else ''
    file_size = os.path.getsize(args.input)
    if file_size > DEFAULT_CHUNK_SIZE:
        result = analyze_large_file(args.input, single_kw, args.window,
                                     DEFAULT_CHUNK_SIZE, args.top,
                                     progress=not args.quiet,
                                     phi_decay=args.phi, kappa=args.kappa)
    else:
        text = read_file(args.input)
        if not text.strip():
            sys.stderr.write("Ошибка: пустой файл\n")
            sys.exit(1)
        result = analyze_text(text, single_kw, args.window,
                               args.phi, args.kappa, args.top,
                               source_file=args.input)

    # --read-cluster N: вывести только содержимое кластера N
    if hasattr(args, 'read_cluster') and args.read_cluster >= 0:
        clusters = result.get('clusters', [])
        # Использовать top_fragments (доступны всегда) + fragments (если есть)
        all_frags = result.get('fragments', []) or result.get('top_fragments', [])
        cluster_n = args.read_cluster
        if cluster_n >= len(clusters):
            sys.stderr.write(f"Кластер {cluster_n} не существует (всего {len(clusters)})\n")
            sys.exit(1)
        indices = clusters[cluster_n]
        cluster_frags = [all_frags[i] for i in indices if i < len(all_frags)]
        output = {
            'cluster_id': cluster_n,
            'total_clusters': len(clusters),
            'fragments_in_cluster': len(cluster_frags),
            'fragments': cluster_frags,
        }
        _emit(output, args.format, args.output, args.quiet)
        return

    # --auto-chunk: автоматическое chunked-чтение по кластерам
    if hasattr(args, 'auto_chunk') and args.auto_chunk:
        _auto_chunk_output(result, args)
        return

    _emit(result, args.format, args.output, args.quiet)


def _auto_chunk_output(result: Dict, args) -> None:
    """Авто-chunk: бить результат на порции по кластерам.
    Каждая порция ≤ chunk_size символов.
    Излишек — в swap-файл. AI глотает столько сколько влезает."""
    clusters = result.get('clusters', [])
    # fragments доступны только в analyze_text; для analyze_large_file — только top_fragments
    fragments = result.get('fragments', [])
    top_frags = result.get('top_fragments', [])
    # Единый источник текста: fragments (полный) или top_fragments (топ-N)
    text_source = fragments if fragments else top_frags
    if not text_source:
        _emit(result, args.format, args.output, args.quiet)
        return

    # Построить cluster_texts из text_source
    frag_by_cluster = {}
    for f in text_source:
        ci = f.get('cluster', f.get('cluster_id', 0))
        frag_by_cluster.setdefault(ci, []).append(f.get('text', ''))

    cluster_texts = []
    for ci in range(len(clusters)):
        texts = frag_by_cluster.get(ci, [])
        combined = '\n\n---\n\n'.join(texts) if texts else ''
        cluster_texts.append({
            'cluster_id': ci,
            'fragment_count': len(texts),
            'text': combined,
            'char_count': len(combined),
        })
    total_clusters = len(clusters)
    chunk_size = getattr(args, 'chunk_size', 4000)
    swap_file = getattr(args, 'swap_file', '') or f'/tmp/poler_swap_{os.getpid()}.json'
    keyword = result.get('keyword', '')
    source = result.get('file_path', '')

    if not clusters:
        _emit(result, args.format, args.output, args.quiet)
        return

    # Собрать текст по кластерам
    cluster_texts = []
    for ci, indices in enumerate(clusters):
        texts = []
        for idx in indices:
            if idx < len(fragments):
                texts.append(fragments[idx].get('text', ''))
        combined = '\n\n---\n\n'.join(texts)
        cluster_texts.append({
            'cluster_id': ci,
            'fragment_count': len(indices),
            'text': combined,
            'char_count': len(combined),
        })

    # Группировка: пока сумма символов ≤ chunk_size — добавляем в порцию
    batches = []
    current_batch = []
    current_size = 0
    for ct in cluster_texts:
        if current_size + ct['char_count'] > chunk_size and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(ct)
        current_size += ct['char_count']
    if current_batch:
        batches.append(current_batch)

    total_batches = len(batches)

    # Вывод
    if not args.quiet:
        sys.stderr.write(f"=== AUTO-CHUNK ===\n")
        sys.stderr.write(f"Файл: {source}\n")
        sys.stderr.write(f"Ключевое слово: «{keyword}»\n")
        sys.stderr.write(f"Кластеров: {total_clusters}\n")
        sys.stderr.write(f"Порций: {total_batches} (по ≤{chunk_size} символов)\n")
        sys.stderr.write(f"Swap: {swap_file}\n\n")

    # Сохранить ВСЕ порции в swap
    swap_data = {
        'keyword': keyword,
        'source': source,
        'total_clusters': total_clusters,
        'total_batches': total_batches,
        'chunk_size': chunk_size,
        'batches': batches,
    }
    try:
        with open(swap_file, 'w', encoding='utf-8') as f:
            json.dump(swap_data, f, ensure_ascii=False, indent=2)
        if not args.quiet:
            sys.stderr.write(f"✓ Swap сохранён: {swap_file}\n")
    except Exception as e:
        sys.stderr.write(f"⚠ Swap не сохранён: {e}\n")

    # Вывести все порции
    for bi, batch in enumerate(batches):
        if not args.quiet:
            sys.stderr.write(f"\n--- Порция {bi+1}/{total_batches} ---\n")
        for ct in batch:
            print(f"[Кластер {ct['cluster_id']}/{total_clusters}] "
                  f"({ct['fragment_count']} фрагм., {ct['char_count']} симв.)")
            print(ct['text'][:chunk_size])
            print()

    if not args.quiet:
        sys.stderr.write(f"\n=== ИТОГ AUTO-CHUNK ===\n")
        sys.stderr.write(f"Порций выведено: {total_batches}\n")
        sys.stderr.write(f"Кластеров покрыто: {total_clusters}\n")
        sys.stderr.write(f"100% файла: {'ДА' if total_clusters > 0 else 'НЕТ'}\n")
        sys.stderr.write(f"Swap файл: {swap_file}\n")
        sys.stderr.write(f"Для чтения из swap: python3 -c \"import json; "
                          f"d=json.load(open('{swap_file}')); "
                          f"print(d['batches'][N])\"\n")


def cmd_grep(args) -> None:
    """Subcommand: grep "pattern" ФАЙЛ_ИЛИ_ДИР [...]"""
    if not args.input:
        sys.stderr.write("Нужен файл или директория\n")
        sys.exit(1)
    if not os.path.exists(args.input):
        sys.stderr.write(f"Не найдено: {args.input}\n")
        sys.exit(1)

    if os.path.isdir(args.input):
        results = grep_search_directory(
            args.input, args.pattern,
            ignore_case=args.ignore_case,
            whole_word=args.whole_word,
            invert=args.invert,
            count_only=args.count,
            context_before=args.before_context,
            context_after=args.after_context,
            context_lines=args.context,
            include=args.include,
            exclude=args.exclude,
            recursive=args.recursive,
            max_matches_per_file=args.max_matches,
        )
    else:
        file_results = grep_search_file(
            args.input, args.pattern,
            ignore_case=args.ignore_case,
            whole_word=args.whole_word,
            invert=args.invert,
            count_only=args.count,
            context_before=args.before_context,
            context_after=args.after_context,
            context_lines=args.context,
            max_matches=args.max_matches,
        )
        # v6.1: для архивов GrepResult.file уже содержит 'archive::member',
        # поэтому группируем по r.file чтобы в выводе видеть per-member префиксы.
        # Для обычных файлов все r.file == args.input — группировка даёт 1 ключ.
        if file_results:
            grouped: Dict[str, List[GrepResult]] = {}
            for r in file_results:
                grouped.setdefault(r.file, []).append(r)
            results = grouped
        else:
            results = {}

    if args.format == 'json':
        print(grep_format_json(results))
    else:
        group = bool(args.context or args.before_context or args.after_context)
        # v6.1: показываем filename всегда, если результатов больше 1
        # (включая случай archive::member1 + archive::member2)
        print(grep_format_text(results,
                               show_filename=os.path.isdir(args.input) or len(results) > 1,
                               show_line_numbers=args.line_number,
                               group_context=group))


def _filter_all_mode(results: List[Dict], dir_path: str) -> List[Dict]:
    """v6.1: фильтр для --multi-mode all.

    На входе: список результатов analyze_directory (по одному на каждое слово).
    На выходе: один объединённый результат, где per_file_results содержит
    ТОЛЬКО файлы, в которых нашлись ВСЕ слова из запроса.

    Стратегия:
      1. Собираем множество файлов с hits для каждого слова.
      2. Находим пересечение этих множеств = файлы, где есть все слова.
      3. В пер-base_result берём per_file_results и фильтруем по этому пересечению.
      4. Объединяем top_by_epsilon из всех слов, но только для файлов из пересечения.
    """
    if not results:
        return results

    # Шаг 1: files_with_hits per word
    files_per_word: List[set] = []
    for r in results:
        files = set()
        for pfr in r.get('per_file_results', []):
            if pfr.get('summary', {}).get('total_windows', 0) > 0:
                files.add(pfr['file'])
        files_per_word.append(files)

    if not files_per_word:
        return results

    # Шаг 2: пересечение
    common = files_per_word[0]
    for s in files_per_word[1:]:
        common = common & s

    if not common:
        return []  # ни один файл не содержит все слова

    # Шаг 3: строим объединённый результат на основе первого
    base = dict(results[0])  # shallow copy
    base['keyword'] = ' + '.join(r['keyword'] for r in results)
    base['multi_mode'] = 'all'
    base['all_words'] = [r['keyword'] for r in results]
    base['files_with_all_words'] = sorted(common)
    base['files_with_hits'] = len(common)

    # Фильтруем per_file_results: оставляем только файлы из common,
    # но из каждого забираем top_fragments из каждого результата (объединяем)
    merged_per_file = []
    for pfr in results[0].get('per_file_results', []):
        if pfr['file'] not in common:
            continue
        merged = dict(pfr)
        # Собираем top_fragments из всех результатов для этого файла
        all_tops = []
        for r in results:
            for pfr2 in r.get('per_file_results', []):
                if pfr2['file'] == pfr['file']:
                    all_tops.extend(pfr2.get('top_fragments', []))
                    break
        # Сортируем по epsilon (нормализованной) по убыванию
        all_tops.sort(key=lambda f: -f.get('normalized_epsilon', 0))
        merged['top_fragments'] = all_tops[:10]
        merged_per_file.append(merged)
    base['per_file_results'] = merged_per_file

    # Шаг 4: объединяем top_by_epsilon, фильтруем по common
    merged_top = []
    for r in results:
        for f in r.get('top_by_epsilon', []):
            if f.get('source_file', f.get('file', '')) in common or not common:
                merged_top.append(f)
    merged_top.sort(key=lambda f: -f.get('normalized_epsilon', 0))
    base['top_by_epsilon'] = merged_top[:50]

    return [base]


def cmd_analyze_dir(args) -> None:
    """Subcommand: analyze_dir ДИР [-r] [-k] [--theme] [...]

    v6.1: --theme auto — определить доминирующую тему по содержимому директории.
    v6.1: --multi-mode {phrase,all,any} — управляет как парсить --multi.
    """
    if not os.path.isdir(args.input):
        sys.stderr.write(f"Не директория: {args.input}\n")
        sys.exit(1)

    # v6.1: --theme auto
    if args.theme == 'auto':
        detected = detect_theme_for_directory(args.input)
        if not args.quiet:
            sys.stderr.write(f"Тема «auto» → детектировано: «{detected}»\n")
        if detected == 'general':
            sys.stderr.write("  Нет совпадений ни с одной темой. Требуется -k или --multi.\n")
            args.theme = None
        else:
            args.theme = detected

    # v6.1: --multi-mode {phrase,all,any}
    # phrase (default): split --multi on commas, each item is one literal phrase
    # all/any: split --multi on whitespace, treat as set of individual words
    multi_mode = getattr(args, 'multi_mode', 'phrase') or 'phrase'

    # Определение списка ключевых слов
    # v6.1.1 (Bug 6 ext): -k тоже поддерживает запятые (как --multi в phrase mode).
    keywords = []
    if args.keyword:
        keywords = [k.strip() for k in args.keyword.split(',') if k.strip()]
    if args.multi:
        if multi_mode == 'phrase':
            # Старое поведение: comma-separated, каждая запятая — отдельный keyword
            keywords = [k.strip() for k in args.multi.split(',') if k.strip()]
        else:
            # v6.1: all/any — split на whitespace, каждый токен — отдельное слово
            keywords = [w.strip() for w in re.split(r'\s+', args.multi) if w.strip()]
    elif args.theme:
        keywords = THEMES[args.theme]
        if not args.quiet:
            sys.stderr.write(f"Тема «{args.theme}»: {len(keywords)} слов\n")
    if not keywords:
        sys.stderr.write("Нужно указать -k KEYWORD, --multi или --theme\n")
        sys.exit(1)

    if not args.quiet and len(keywords) > 1 and args.keyword and not args.theme and not args.multi:
        sys.stderr.write(f"  multi-keyword (-k): {len(keywords)} слов "
                         f"({', '.join(keywords[:5])}"
                         f"{'...' if len(keywords) > 5 else ''})\n")

    if not args.quiet and multi_mode != 'phrase' and args.multi:
        sys.stderr.write(f"  multi-mode={multi_mode}: {len(keywords)} отдельных слов "
                         f"({', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''})\n")

    exts = list(TEXT_EXTENSIONS) + ['.epub']
    if args.include_images:
        exts.append('.png')
    exts += list(ARCHIVE_SUFFIXES)

    all_results = []
    for kw in keywords:
        if not args.quiet:
            sys.stderr.write(f"→ Анализ «{kw}» по директории...\n")
        result = analyze_directory(
            args.input, kw, args.window, args.phi, args.kappa,
            args.top, args.cross_resonance, exts,
            include_images=args.include_images, quiet=args.quiet,
        )
        all_results.append(result)

    # v6.1: в режиме "all" оставляем только файлы, где ВСЕ слова нашли совпадения
    if multi_mode == 'all' and len(all_results) > 1:
        all_results = _filter_all_mode(all_results, args.input)
        if not args.quiet:
            sys.stderr.write(f"  [all-mode] оставлено результатов: {len(all_results)}\n")

    if args.format == 'json':
        output = format_json(all_results if len(all_results) > 1 else all_results[0])
    elif args.format == 'md':
        if len(all_results) == 1:
            output = format_directory_markdown(all_results[0])
        else:
            output = '\n\n---\n\n'.join(format_directory_markdown(r) for r in all_results)
    else:
        if len(all_results) == 1:
            output = format_directory_text(all_results[0])
        else:
            parts = [format_directory_text(r) for r in all_results]
            output = ('\n\n' + '=' * 60 + '\n\n').join(parts)

    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        if not args.quiet:
            sys.stderr.write(f"✓ Сохранено: {args.output}\n")
    else:
        print(output)


def cmd_diff(args) -> None:
    """Subcommand: diff Ф1 Ф2 [-k KEYWORD] [-f text/json/md].

    v6.1 FIX (Bug 6): -k "POLER,SCF,DM" теперь парсится как 3 отдельных
    ключевых слова, diff выполняется по каждому, результаты агрегируются.
    """
    if not os.path.exists(args.file1):
        sys.stderr.write(f"Файл 1 не найден: {args.file1}\n")
        sys.exit(1)
    if not os.path.exists(args.file2):
        sys.stderr.write(f"Файл 2 не найден: {args.file2}\n")
        sys.exit(1)

    # v6.1: парсим запятые в -k
    kw_raw = args.keyword or ''
    keywords = [k.strip() for k in kw_raw.split(',') if k.strip()]
    result = diff_files(args.file1, args.file2, keywords or '', args.window)

    if args.format == 'md':
        output = format_diff_markdown(result)
    elif args.format == 'json':
        output = format_json(result)
    else:
        output = _format_diff_text(result)
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        sys.stderr.write(f"✓ Сохранено: {args.output}\n")
    else:
        print(output)


def _format_diff_text(result: Dict) -> str:
    """Текстовый вывод diff. v6.1: показывает per-keyword breakdown если ключей >1."""
    lines = [f"=== DIFF: «{result['keyword']}» ===", ""]
    lines.append(f"Файл 1: {result['file1']} — {result['file1_windows']} вхожд., "
                 f"Σε={_fmt(result['file1_epsilon'], 0)}")
    lines.append(f"Файл 2: {result['file2']} — {result['file2_windows']} вхожд., "
                 f"Σε={_fmt(result['file2_epsilon'], 0)}")
    lines.append(f"Δ: {result['delta_windows']:+d} вхожд., "
                 f"Δε={_fmt(result['delta_epsilon'], 0)}")

    # v6.1: per-keyword breakdown (только если ключей больше одного)
    per_kw = result.get('per_keyword', [])
    if len(per_kw) > 1:
        lines.append("")
        lines.append("По ключевым словам:")
        lines.append(f"  {'Ключевое слово':<25s}  {'Ф1':>6s}  {'Ф2':>6s}  "
                     f"{'Δвх':>6s}  {'Δε':>8s}")
        for pk in per_kw:
            lines.append(f"  {pk['keyword'][:25]:<25s}  "
                         f"{pk['file1_windows']:>6d}  "
                         f"{pk['file2_windows']:>6d}  "
                         f"{pk['delta_windows']:>+6d}  "
                         f"{_fmt(pk['delta_epsilon'], 0):>8s}")
    return "\n".join(lines)


def cmd_api(args) -> None:
    """Subcommand: api [--port N]."""
    run_api_server(args.port)


def _emit(result: Any, fmt: str, output: Optional[str], quiet: bool) -> None:
    """Универсальный вывод результата в нужном формате."""
    if fmt == 'json':
        text = format_json(result)
    elif fmt == 'md':
        text = format_markdown(result) if isinstance(result, dict) and 'top_fragments' in result \
               else format_text(result)
    else:
        text = format_text(result)
    if output:
        Path(output).write_text(text, encoding='utf-8')
        if not quiet:
            sys.stderr.write(f"✓ Сохранено: {output}\n")
    else:
        print(text)


def _legacy_v4_cli(argv: List[str]) -> None:
    """Обратная совместимость: старый CLI v4 (если первый аргумент не subcommand).
    Поддерживает все флаги v4: -k, -s, --multi, --theme, -r, --cross-resonance,
    --diff, --include-images, -w, --phi, --kappa, --top, -f, -o, --quiet,
    --api, --port, --stdin, --version."""
    parser = argparse.ArgumentParser(
        prog='poler6',
        description=f'POLER[n] v{__version__} — Legacy v4 CLI mode',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Это старый (v4-совместимый) режим. Используйте subcommands для нового API:
  poler_v6.py analyze ФАЙЛ -k KW
  poler_v6.py grep PATTERN ФАЙЛ
  poler_v6.py analyze_dir ДИР -k KW
  poler_v6.py diff Ф1 Ф2 -k KW
  poler_v6.py api

Примеры legacy:
  poler_v6.py file.md -k "Багуа" --top 3
  poler_v6.py download/ -r -k "Нокс" --top 3
  poler_v6.py download/ -r --theme биология --top 3
  poler_v6.py file.md -s "поисковый запрос"
  poler_v6.py --diff v1.md v2.md -k "сфер"
  poler_v6.py --api --port 8000
""",
    )
    parser.add_argument('input', nargs='?', help='Файл или директория')
    parser.add_argument('--stdin', action='store_true', help='Читать из stdin')
    parser.add_argument('-k', '--keyword', default='', help='Ключевое слово')
    parser.add_argument('-s', '--search', help='Full-text search запрос')
    parser.add_argument('--multi', help='Несколько слов через запятую')
    parser.add_argument('--multi-mode',
                        choices=['phrase', 'all', 'any'], default='phrase',
                        help='v6.1: режим multi-word поиска (phrase/all/any)')
    parser.add_argument('--theme', choices=list(THEMES.keys()) + ['auto'],
                        help='Автотема (биология, астрономия, и т.д.). '
                             'v6.1: "auto" — определить по содержимому.')
    parser.add_argument('-r', '--recursive', action='store_true',
                        help='Рекурсивный обход директории')
    parser.add_argument('--cross-resonance', action='store_true',
                        help='Кросс-файлный резонанс (с -r)')
    parser.add_argument('--diff', nargs=2, metavar=('FILE1', 'FILE2'),
                        help='Сравнить два файла по ключевому слову')
    parser.add_argument('--include-images', action='store_true',
                        help='Включить PNG (по названию файла)')
    parser.add_argument('-w', '--window', type=int, default=DEFAULT_WINDOW,
                        help=f'Размер окна (по умолч. {DEFAULT_WINDOW})')
    parser.add_argument('--phi', type=float, default=0.85, help='Затухание R[n]')
    parser.add_argument('--kappa', type=float, default=1.0, help='Интенсивность ε')
    parser.add_argument('--top', type=int, default=10, help='Топ-N результатов')
    parser.add_argument('-f', '--format', choices=['text', 'json', 'md'],
                        default='text', help='Формат вывода (по умолч. text)')
    parser.add_argument('-o', '--output', help='Сохранить в файл')
    parser.add_argument('--quiet', action='store_true', help='Тихий режим')
    parser.add_argument('--api', action='store_true',
                        help='Запустить HTTP API сервер')
    parser.add_argument('--port', type=int, default=DEFAULT_API_PORT,
                        help=f'Порт для API (по умолч. {DEFAULT_API_PORT})')
    parser.add_argument('--version', action='version',
                        version=f'POLER[n] v{__version__}')

    args = parser.parse_args(argv)

    # --- API РЕЖИМ ---
    if args.api:
        run_api_server(args.port)
        return

    quiet = args.quiet

    # --- DIFF РЕЖИМ ---
    if args.diff:
        # v6.1 FIX Bug 6: парсим запятые в -k
        kw_raw = args.keyword or ''
        keywords = [k.strip() for k in kw_raw.split(',') if k.strip()]
        result = diff_files(args.diff[0], args.diff[1], keywords or '', args.window)
        if args.format == 'md':
            output = format_diff_markdown(result)
        elif args.format == 'json':
            output = format_json(result)
        else:
            output = _format_diff_text(result)
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            if not quiet:
                sys.stderr.write(f"✓ Сохранено: {args.output}\n")
        else:
            print(output)
        return

    # --- SEARCH РЕЖИМ ---
    if args.search:
        if args.stdin:
            text = sys.stdin.read()
        elif args.input:
            text = read_file(args.input)
        else:
            sys.stderr.write("Нужен файл или --stdin для search\n")
            sys.exit(1)
        index = build_search_index(text)
        results = search_in_text(text, args.search, index)
        output = format_search_results(results, args.search, args.format)
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            if not quiet:
                sys.stderr.write(f"✓ Сохранено: {args.output}\n")
        else:
            print(output)
        return

    # --- ОПРЕДЕЛЕНИЕ КЛЮЧЕВЫХ СЛОВ ---
    # v6.1: --theme auto и --multi-mode {phrase,all,any}
    if args.theme == 'auto':
        if args.recursive and args.input and os.path.isdir(args.input):
            detected = detect_theme_for_directory(args.input)
        elif args.input and os.path.exists(args.input):
            detected = detect_theme_for_file(args.input)
        else:
            detected = 'general'
        if not quiet:
            sys.stderr.write(f"Тема «auto» → детектировано: «{detected}»\n")
        if detected == 'general':
            sys.stderr.write("  Нет совпадений ни с одной темой. Требуется -k или --multi.\n")
            args.theme = None
        else:
            args.theme = detected

    multi_mode = getattr(args, 'multi_mode', 'phrase') or 'phrase'
    # v6.1.1 (Bug 6 ext): -k тоже поддерживает запятые во всех путях вызова
    keywords = []
    if args.keyword:
        keywords = [k.strip() for k in args.keyword.split(',') if k.strip()]
    if args.multi:
        if multi_mode == 'phrase':
            keywords = [k.strip() for k in args.multi.split(',') if k.strip()]
        else:
            # v6.1: all/any — split на whitespace
            keywords = [w.strip() for w in re.split(r'\s+', args.multi) if w.strip()]
    elif args.theme:
        keywords = THEMES[args.theme]
        if not quiet:
            sys.stderr.write(f"Тема «{args.theme}»: {len(keywords)} слов\n")
    if not keywords:
        sys.stderr.write("Нужно указать -k KEYWORD, --multi, --theme или -s QUERY\n")
        sys.exit(1)

    if not quiet and len(keywords) > 1 and args.keyword and not args.theme and not args.multi:
        sys.stderr.write(f"  multi-keyword (-k): {len(keywords)} слов "
                         f"({', '.join(keywords[:5])}"
                         f"{'...' if len(keywords) > 5 else ''})\n")

    # --- RECURSIVE (директория) ---
    if args.recursive and args.input:
        all_results = []
        for kw in keywords:
            if not quiet:
                sys.stderr.write(f"→ Анализ «{kw}» по директории...\n")
            exts = list(TEXT_EXTENSIONS) + ['.epub']
            if args.include_images:
                exts.append('.png')
            exts += list(ARCHIVE_SUFFIXES)
            result = analyze_directory(
                args.input, kw, args.window, args.phi, args.kappa,
                args.top, args.cross_resonance, exts,
                include_images=args.include_images, quiet=quiet,
            )
            all_results.append(result)

        # v6.1: --multi-mode all — фильтр файлов где все слова встретились
        if multi_mode == 'all' and len(all_results) > 1:
            all_results = _filter_all_mode(all_results, args.input)
            if not quiet:
                sys.stderr.write(f"  [all-mode] оставлено результатов: {len(all_results)}\n")

        if args.format == 'json':
            output = format_json(all_results if len(all_results) > 1 else all_results[0])
        elif args.format == 'md':
            if len(all_results) == 1:
                output = format_directory_markdown(all_results[0])
            else:
                output = '\n\n---\n\n'.join(format_directory_markdown(r) for r in all_results)
        else:
            if len(all_results) == 1:
                output = format_directory_text(all_results[0])
            else:
                parts = []
                for r in all_results:
                    parts.append(format_directory_text(r))
                output = '\n\n' + '=' * 60 + '\n\n'.join(parts)

        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            if not quiet:
                sys.stderr.write(f"✓ Сохранено: {args.output}\n")
        else:
            print(output)
        return

    # --- ОБЫЧНЫЙ РЕЖИМ (один файл или stdin) ---
    if args.stdin:
        text = sys.stdin.read()
        source_file = '<stdin>'
    elif args.input:
        if os.path.isdir(args.input):
            sys.stderr.write("Указана директория. Используйте -r для рекурсивного обхода.\n")
            sys.exit(1)
        text = read_file(args.input)
        source_file = args.input
    else:
        parser.print_help()
        return

    if not text.strip():
        sys.stderr.write("Ошибка: пустой ввод\n")
        sys.exit(1)

    # Multi-режим
    if len(keywords) > 1:
        results = []
        for kw in keywords:
            if not quiet:
                sys.stderr.write(f"→ Анализ «{kw}»...\n")
            r = analyze_text(text, kw, args.window, args.phi, args.kappa,
                             args.top, source_file)
            results.append(r)
        if args.format == 'json':
            output = format_json(results)
        elif args.format == 'md':
            output = _format_multi_markdown(results, source_file)
        else:
            lines = [f"POLER[n] v{__version__} — Мульти-анализ", ""]
            for r in results:
                lines.append(f"  «{r['keyword']:20s}»  "
                             f"вхождений={r['total_occurrences']:4d}  "
                             f"фрагм.={r['unique_fragments']:4d}")
            output = "\n".join(lines)
    else:
        file_size = os.path.getsize(source_file) if source_file != '<stdin>' and os.path.exists(source_file) else len(text.encode('utf-8'))
        if file_size > DEFAULT_CHUNK_SIZE and source_file != '<stdin>':
            result = analyze_large_file(source_file, keywords[0], args.window,
                                        DEFAULT_CHUNK_SIZE, args.top,
                                        progress=not quiet,
                                        phi_decay=args.phi, kappa=args.kappa)
        else:
            result = analyze_text(text, keywords[0], args.window, args.phi,
                                  args.kappa, args.top, source_file)
        if args.format == 'json':
            output = format_json(result)
        elif args.format == 'md':
            output = format_markdown(result)
        else:
            output = format_text(result)

    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        if not quiet:
            sys.stderr.write(f"✓ Сохранено: {args.output}\n")
    else:
        print(output)


def _format_multi_markdown(results: List[Dict], source_file: str = '') -> str:
    """MD с всеми ключевыми словами."""
    lines = [f"# POLER[n] v{__version__} — Карта документа", ""]
    lines.append(f"> {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
                 f"Цикл: ℘ → O → L → ε → R[n]")
    if source_file:
        lines.append(f"> Файл: `{source_file}`")
    lines.append("")
    valid = [r for r in results if r.get('unique_fragments', 0) > 0]
    if not valid:
        lines.append("Ничего не найдено.")
        return "\n".join(lines)
    lines.append("## Рейтинг по Σε")
    lines.append("")
    lines.append("| # | Слово | Вхождений | Фрагментов | Peak ε | Peak R |")
    lines.append("|---|-------|-----------|------------|--------|--------|")
    sorted_results = sorted(valid, key=lambda r: -sum(f['epsilon'] for f in r['fragments']))
    for i, r in enumerate(sorted_results, 1):
        total_eps = sum(f['epsilon'] for f in r['fragments'])
        peak_eps = max((f['normalized_epsilon'] for f in r['top_fragments']), default=0)
        peak_r = max((f['resonance'] for f in r['top_fragments']), default=0)
        lines.append(f"| {i} | **{r['keyword']}** | {r['total_occurrences']} | "
                     f"{r['unique_fragments']} | {_fmt(peak_eps, 1)} | {_fmt(peak_r, 1)} |")
    lines.append("")
    for r in sorted_results:
        kw = r['keyword']
        lines.append(f"## «{kw}»")
        lines.append("")
        for j, f in enumerate(r['top_fragments'][:3], 1):
            lines.append(f"### Фрагмент {j} — "
                         f"ε={f['normalized_epsilon']:.1f} · "
                         f"R={f['resonance']:.1f} · {f.get('section', '')}")
            lines.append("")
            cleaned = _clean_for_display(f['text'], 2000)
            for line in _highlight_md(cleaned, kw).split('\n'):
                lines.append(f"> {line}" if line.strip() else ">")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Главная точка входа. Subcommands или legacy v4 CLI (обратная совместимость)."""
    argv = sys.argv[1:]

    # Нет аргументов → показываем help
    if not argv:
        parser = _build_subcommand_parser()
        parser.print_help()
        return

    # Если первый аргумент — subcommand, используем новый CLI
    if argv[0] in SUBCOMMANDS:
        parser = _build_subcommand_parser()
        args = parser.parse_args(argv)
        if args.command == 'analyze':
            cmd_analyze(args)
        elif args.command == 'grep':
            cmd_grep(args)
        elif args.command == 'analyze_dir':
            cmd_analyze_dir(args)
        elif args.command == 'diff':
            cmd_diff(args)
        elif args.command == 'smart':
            cmd_smart(args)
        elif args.command == 'api':
            cmd_api(args)
        return

    # Иначе — legacy v4 CLI (первый аргумент трактуется как путь к файлу/директории)
    # или один из --api/--diff/--version флагов
    _legacy_v4_cli(argv)


if __name__ == '__main__':
    main()
