#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POLER[Ψ] v3.0 — Ядро Смысла (Meaning Core)
============================================
Архитектура Super Z:
  poler_enhanced.py = Ядро Смысла — превращает данные в знание
  super_z_core.py   = Нервная система — маршрутизация задач
  super_z_bridge.py  = Руки и Глаза — взаимодействие с миром
  grab              = Пылесос — сбор данных (без понимания)

КЛЮЧЕВОЕ ИЗМЕНЕНИЕ v3.0:
  1. THEMES → ДИНАМИЧЕСКИЙ РЕЖИМ (авто-обнаружение из документа)
  2. Ручной режим: пользователь задаёт домен → ядро подстраивается
  3. Нет фиксированных словарей — домены определяются ИЗ ТЕКСТА
  4. Авто-извлечение ключевых слов через epsilon-ранжирование

ПРОТОТИП: Python → будущая миграция на Rust/Zig/C
  - epsilon-energy → Rust: f64 SIMD векторизация
  - resonance cascade → Zig: compile-time гарантии памяти
  - TF-IDF авто-темы → C: максимальная скорость для больших корпусов

НОВОЕ v3.0:
  - auto_discover_themes(): извлечение доменов из самого текста
  - auto_extract_keywords(): epsilon-ранжирование всех значимых слов
  - build_veins(): построение семантических вен навигации
  - SemanticVein: структура для навигации по документу
  - Ручной режим: custom_themes= для пользовательских доменов
"""

import argparse
import json
import math
import re
import sys
import os
import zipfile
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Tuple, Dict, Optional, Any

__version__ = "3.0.0"
__author__ = "POLER[n] Studio + Super Z"

# ═══════════════════════════════════════════════════════════════════════
# ℘ — ШАБЛОНЫ ВОСПРИЯТИЯ (из оригинала)
# ═══════════════════════════════════════════════════════════════════════

PII_PATTERNS: List[Tuple[str, str]] = [
    (r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL]'),
    (r'\+?\d{1,3}[-.\s]?\(?\d{2,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}', '[PHONE]'),
    (r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}', '[CARD]'),
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]'),
    (r'\b\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\b', '[DATE]'),
    (r'(?<!\d)\d{10,12}(?!\d)', '[ID]'),
    (r'[А-ЯІЇЄҐA-Z][а-яіїєґa-z]+(?:\s+[А-ЯІЇЄҐA-Z][а-яіїєґa-z]+)+', '[NAME]'),
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
    'важно', 'критично', 'угроза', 'смысл', 'сущность', 'важный', 'значимый',
    'проблема', 'глубокий', 'фундаментальный', 'кризис', 'риск',
    'ответственность', 'сознание', 'реальность', 'истина', 'бытие',
    'important', 'critical', 'threat', 'meaning', 'crisis', 'risk',
    'responsibility', 'consciousness', 'reality', 'truth', 'existence',
    'essence', 'fundamental', 'deep',
    'история', 'сила', 'власть', 'закон', 'порядок', 'хаос', 'магия',
    'культура', 'религия', 'политика', 'экономика', 'война', 'мир',
}

# ═══════════════════════════════════════════════════════════════════════
# v3.0: ДИНАМИЧЕСКИЕ ТЕМЫ — ОБЛАСТИ ЗНАНИЙ (не фиксированные!)
# ═══════════════════════════════════════════════════════════════════════
#
# Принцип: ТЕМЫ больше НЕ фиксированы. Документы бывают РАЗНЫЕ.
# Есть только "семена" (seed patterns) для авто-распознавания домена.
# Пользователь может добавить СВОИ через custom_themes=.
#
# Вектор миграции: эти словари → Rust enum с Pattern Matching
# ═══════════════════════════════════════════════════════════════════════

DOMAIN_SEEDS: Dict[str, List[str]] = {
    # Семена для авто-распознавания — НЕ исчерпывающие словари
    # Каждая область — 5-10 маркеров, đủ чтобы распознать домен
    'physics': [
        'hamiltonian', 'lagrangian', 'quantum', 'eigenvalue', 'wavefunction',
        'schrodinger', 'heisenberg', 'maxwell', 'tensor', 'hilbert',
        'гамильтониан', 'лагранжиан', 'квантовый', 'собственное', 'волновая',
    ],
    'mathematics': [
        'theorem', 'proof', 'lemma', 'corollary', 'isomorphism',
        'homomorphism', 'topology', 'manifold', 'integral', 'derivative',
        'теорема', 'доказательство', 'лемма', 'следствие', 'изоморфизм',
    ],
    'computer_science': [
        'algorithm', 'complexity', 'recursion', 'hash', 'graph',
        'binary', 'pointer', 'compiler', 'runtime', 'protocol',
        'алгоритм', 'сложность', 'рекурсия', 'хеш', 'граф',
    ],
    'economics': [
        'inflation', 'gdp', 'fiscal', 'monetary', 'supply',
        'demand', 'equilibrium', 'utility', 'arbitrage', 'portfolio',
        'инфляция', 'ввп', 'фискальн', 'монетарн', 'спрос',
    ],
    'biology': [
        'protein', 'genome', 'enzyme', 'mitosis', 'membrane',
        'ribosome', 'nucleotide', 'organelle', 'phenotype', 'genotype',
        'белок', 'геном', 'фермент', 'митоз', 'мембран',
    ],
    'chemistry': [
        'catalyst', 'molecule', 'reaction', 'bond', 'ion',
        'oxidation', 'reduction', 'polymer', 'solvent', 'isomer',
        'катализатор', 'молекул', 'реакци', 'связь', 'окислени',
    ],
    'medicine': [
        'diagnosis', 'therapy', 'symptom', 'pathology', 'clinical',
        'pharmacology', 'prognosis', 'etiology', 'morbidity', 'mortality',
        'диагноз', 'терапи', 'симптом', 'патологи', 'клиническ',
    ],
    'linguistics': [
        'morphology', 'syntax', 'semantics', 'phonology', 'pragmatics',
        'lexeme', 'corpus', 'etymology', 'dialect', 'phoneme',
        'морфологи', 'синтаксис', 'семантик', 'фонолог', 'прагматик',
    ],
    'history': [
        'dynasty', 'empire', 'revolution', 'colonial', 'medieval',
        'artifact', 'civilization', 'archaeology', 'chronicle', 'epoch',
        'династи', 'импери', 'революци', 'средневеков', 'хроник',
    ],
    'law': [
        'statute', 'jurisdiction', 'precedent', 'litigation', 'contract',
        'liability', 'amendment', 'ordinance', 'verdict', 'tribunal',
        'закон', 'юрисдикци', 'прецедент', 'иск', 'договор',
    ],
    # Семена для security/code-review домена — позволяют POLER правильно
    # распознавать опасные паттерны в исходном коде (eval, exec, subprocess,
    # pickle, SQL injection и т.д.). Раньше они попадали в 'general'.
    'security': [
        'eval', 'exec', 'compile', '__import__', 'getattr', 'setattr',
        'subprocess', 'shell', 'os.system', 'popen', 'spawn',
        'pickle', 'loads', 'load', 'marshal', 'shelve',
        'sql', 'insert', 'select', 'update', 'delete', 'drop',
        'password', 'pwd', 'secret', 'token', 'credential',
        'inject', 'xss', 'csrf', 'rce', 'ssrf',
        'try', 'except', 'finally', 'raise', 'assert',
        'global', 'nonlocal',
        'injection', 'vulnerability', 'exploit', 'sanitize', 'escape',
        'уязвим', 'инъекци', 'парол', 'токен', 'секрет',
    ],
}

# ═══════════════════════════════════════════════════════════════════════
# v3.0: СЕМАНТИЧЕСКАЯ ВЕНА — структура навигации
# ═══════════════════════════════════════════════════════════════════════
# "Вены" — это пути навигации по документу, созданные POLER Core.
# Без них — документ просто текст. С ними — живая карта смысла.
#
# Миграция: SemanticVein → Rust struct с Cow<str> и Zero-copy
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SemanticVein:
    """Семантическая вена — путь навигации через документ."""
    keyword: str               # Ключевое слово-маркер
    domain: str                # Область знания (auto-discovered)
    epsilon_peak: float        # Пик epsilon-energy
    resonance_integral: float  # Интеграл резонанса
    positions: List[int]       # Позиции в тексте
    top_fragment: str          # Лучший фрагмент (самый высокий ε)
    source_file: str           # Файл-источник
    confidence: float = 0.0    # Уверенность в домене [0..1]

# ═══════════════════════════════════════════════════════════════════════
# O — ОБРАЗ (из оригинала)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TextWindow:
    index: int
    keyword: str
    position: int
    raw_text: str
    cleaned_text: str
    filtered_items: List[Tuple[str, str]] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)
    epsilon: float = 0.0
    resonance: float = 0.0
    source_file: str = ""

# ═══════════════════════════════════════════════════════════════════════
# L — ЛОГИКА (из оригинала)
# ═══════════════════════════════════════════════════════════════════════

def filter_pii(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    filtered: List[Tuple[str, str]] = []
    cleaned = text
    for pattern, replacement in PII_PATTERNS:
        for m in re.finditer(pattern, cleaned):
            filtered.append((m.group(0), replacement))
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned, filtered

def tokenize(text: str) -> List[str]:
    raw = re.findall(r'[\w]+', text.lower(), re.UNICODE)
    return [t for t in raw
            if t not in STOPWORDS
            and t not in NOISE_WORDS
            and len(t) > 2]

# ═══════════════════════════════════════════════════════════════════════
# ε — ЭНЕРГИЯ (из оригинала)
# ═══════════════════════════════════════════════════════════════════════

def word_rarity(word: str, total_words: int, counts: Counter) -> float:
    p = counts.get(word, 1) / max(total_words, 1)
    return -math.log(max(p, 1e-10))

def compute_epsilon(window: TextWindow, keyword: str,
                    counts: Counter, total_words: int,
                    kappa: float = 1.0) -> float:
    kw_lower = keyword.lower()
    tokens = [t for t in window.tokens if t != kw_lower]
    if not tokens:
        return 0.0
    unique = set(tokens)
    d_squared = sum(word_rarity(t, total_words, counts) ** 2 for t in unique)
    kw_count = window.cleaned_text.lower().count(kw_lower)
    kw_intensity = 1.0 + math.log1p(kw_count)
    emotion_bonus = sum(1.5 for t in tokens if t in EMOTIONAL_MARKERS)
    return kappa * kw_intensity * d_squared + emotion_bonus

# ═══════════════════════════════════════════════════════════════════════
# R[n] — РЕЗОНАНС (из оригинала + кросс-файлный v2.0)
# ═══════════════════════════════════════════════════════════════════════

def compute_resonance_series(epsilons: List[float], phi_decay: float = 0.85) -> List[float]:
    n = len(epsilons)
    R = [0.0] * n
    for t in range(n):
        s = 0.0
        for i in range(t + 1):
            s += epsilons[i] * (phi_decay ** (t - i))
        R[t] = s
    return R

def compute_cross_resonance(all_windows: List[TextWindow], phi_decay: float = 0.85) -> List[float]:
    """Кросс-файлный резонанс. R_t учитывает фрагменты из ВСЕХ файлов."""
    n = len(all_windows)
    R = [0.0] * n
    for t in range(n):
        s = 0.0
        for i in range(t + 1):
            s += all_windows[i].epsilon * (phi_decay ** (t - i))
        R[t] = s
    return R

# ═══════════════════════════════════════════════════════════════════════
# v3.0: АВТО-ОБНАРУЖЕНИЕ ДОМЕНОВ ИЗ ТЕКСТА
# ═══════════════════════════════════════════════════════════════════════
# Принцип: не мы говорим документу, кто он — документ говорит нам.
# Семена (DOMAIN_SEEDS) — только для первого касания.
# Реальные ключевые слова извлекаются ИЗ текста через epsilon-ранжирование.
#
# Миграция: → Rust fn detect_domain(text: &str) -> Vec<DomainScore>
# ═══════════════════════════════════════════════════════════════════════

def detect_domains(text: str, custom_seeds: Optional[Dict[str, List[str]]] = None) -> List[Dict]:
    """
    Авто-обнаружение областей знания в тексте.
    Возвращает список доменов с confidence score.
    
    Алгоритм:
    1. Считаем совпадения с seed-маркерами каждого домена
    2. Нормализуем по размеру seed-словаря
    3. Ранжируем по confidence
    
    Returns:
        [{'domain': str, 'hits': int, 'seed_size': int, 'confidence': float, 'matched': [str]}]
    """
    text_lower = text.lower()
    seeds = dict(DOMAIN_SEEDS)
    if custom_seeds:
        seeds.update(custom_seeds)
    
    results = []
    for domain, markers in seeds.items():
        matched = []
        for marker in markers:
            if marker.lower() in text_lower:
                matched.append(marker)
        
        if matched:
            # confidence = (доля совпавших семян) × log(1 + количество совпадений)
            hit_ratio = len(matched) / len(markers)
            confidence = hit_ratio * math.log1p(len(matched))
            results.append({
                'domain': domain,
                'hits': len(matched),
                'seed_size': len(markers),
                'confidence': min(confidence, 1.0),
                'matched': matched,
            })
    
    # Сортируем по confidence
    results.sort(key=lambda x: -x['confidence'])
    return results

def auto_extract_keywords(
    text: str,
    top_n: int = 20,
    min_freq: int = 2,
    min_length: int = 4,
) -> List[Dict]:
    """
    Авто-извлечение ключевых слов из текста через epsilon-подобное ранжирование.
    
    Алгоритм:
    1. Токенизация и подсчёт частот
    2. rarity = -log(p) — редкие слова важнее
    3. Фильтруем: min_freq, min_length, не stopword, не noise
    4. Ранжируем по rarity × freq (баланс редкости и частоты)
    
    Returns:
        [{'word': str, 'freq': int, 'rarity': float, 'score': float}]
    """
    all_tokens = tokenize(text)
    counts = Counter(all_tokens)
    total = len(all_tokens)
    
    if total == 0:
        return []
    
    candidates = []
    for word, freq in counts.items():
        if freq < min_freq:
            continue
        if len(word) < min_length:
            continue
        if word in STOPWORDS or word in NOISE_WORDS:
            continue
        
        rarity = -math.log(freq / total)
        # score = rarity × log(freq) — баланс: редкое И частое
        score = rarity * math.log1p(freq)
        
        candidates.append({
            'word': word,
            'freq': freq,
            'rarity': rarity,
            'score': score,
        })
    
    candidates.sort(key=lambda x: -x['score'])
    return candidates[:top_n]

def auto_discover_themes(
    text: str,
    top_keywords: int = 10,
    custom_seeds: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    """
    ПОЛНЫЙ ЦИКЛ АВТО-ОБНАРУЖЕНИЯ:
    1. Распознаём домены из текста
    2. Извлекаем ключевые слова через epsilon-ранжирование
    3. Связываем ключевые слова с доменами
    
    Returns:
        {
            'domains': [...],
            'keywords': [...],
            'theme_map': {domain: [keywords]},
        }
    """
    domains = detect_domains(text, custom_seeds)
    keywords = auto_extract_keywords(text, top_n=top_keywords)
    
    # Строим карту: домен → ключевые слова
    theme_map: Dict[str, List[str]] = {}
    for d in domains:
        domain_name = d['domain']
        theme_map[domain_name] = d['matched'][:]
    
    # Добавляем авто-ключевые слова к доменам
    for kw in keywords:
        word = kw['word']
        # Проверяем, к какому домену относится
        assigned = False
        for d in domains:
            if any(seed in word or word in seed for seed in d['matched']):
                if d['domain'] not in theme_map:
                    theme_map[d['domain']] = []
                if word not in theme_map[d['domain']]:
                    theme_map[d['domain']].append(word)
                assigned = True
                break
        if not assigned:
            # Не привязано к домену → "general"
            if 'general' not in theme_map:
                theme_map['general'] = []
            theme_map['general'].append(word)
    
    return {
        'domains': domains,
        'keywords': keywords,
        'theme_map': theme_map,
    }

# ═══════════════════════════════════════════════════════════════════════
# v3.0: ПОСТРОЕНИЕ СЕМАНТИЧЕСКИХ ВЕН
# ═══════════════════════════════════════════════════════════════════════
# Вены — это результат работы POLER Core. Без них документ — 
# просто набор символов. С ними — живая навигационная карта.
#
# Миграция: build_veins() → Rust: параллельная обработка через rayon
# ═══════════════════════════════════════════════════════════════════════

def build_veins(
    text: str,
    keywords: Optional[List[str]] = None,
    custom_seeds: Optional[Dict[str, List[str]]] = None,
    window_size: int = 5000,
    phi_decay: float = 0.85,
    kappa: float = 1.0,
    top_n: int = 5,
    source_file: str = "",
) -> Dict:
    """
    Построение семантических вен документа.
    
    Если keywords=None — автоматически извлекает из текста.
    Если custom_seeds — использует пользовательские домены.
    
    Returns:
        {
            'veins': [SemanticVein],
            'domains': [...],
            'theme_map': {...},
            'navigation_map': {keyword: {positions, peak_epsilon, ...}},
        }
    """
    # Шаг 1: Авто-обнаружение тем
    discovery = auto_discover_themes(text, top_keywords=20, custom_seeds=custom_seeds)
    domains = discovery['domains']
    theme_map = discovery['theme_map']
    
    # Шаг 2: Определяем ключевые слова для анализа
    if keywords is None:
        # Авто-режим: берём top ключевые слова
        keywords = [kw['word'] for kw in discovery['keywords']]
    else:
        # Ручной режим: пользователь задал ключевые слова
        # Но всё равно обогащаем из авто-обнаружения
        auto_kws = [kw['word'] for kw in discovery['keywords'][:5]]
        for akw in auto_kws:
            if akw not in keywords:
                keywords.append(akw)
    
    if not keywords:
        return {
            'veins': [],
            'domains': domains,
            'theme_map': theme_map,
            'navigation_map': {},
            'source_file': source_file,
        }
    
    # Шаг 3: Запускаем POLER для каждого ключевого слова
    all_tokens = tokenize(text)
    counts = Counter(all_tokens)
    total_words = len(all_tokens)
    
    veins: List[SemanticVein] = []
    navigation_map: Dict[str, Dict] = {}
    
    for keyword in keywords:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        positions = [m.start() for m in pattern.finditer(text)]
        
        if not positions:
            continue
        
        # Создаём окна
        windows: List[TextWindow] = []
        for idx, pos in enumerate(positions):
            start = max(0, pos - window_size // 2)
            end = min(len(text), pos + window_size // 2)
            raw = text[start:end]
            cleaned, filtered = filter_pii(raw)
            tokens = tokenize(cleaned)
            windows.append(TextWindow(
                index=idx, keyword=keyword, position=pos,
                raw_text=raw, cleaned_text=cleaned,
                filtered_items=filtered, tokens=tokens,
                source_file=source_file,
            ))
        
        # Epsilon-energy
        for w in windows:
            w.epsilon = compute_epsilon(w, keyword, counts, total_words, kappa)
        
        # Resonance
        R = compute_resonance_series([w.epsilon for w in windows], phi_decay)
        for w, r in zip(windows, R):
            w.resonance = r
        
        # Топ по epsilon
        top_windows = sorted(windows, key=lambda w: w.epsilon, reverse=True)[:top_n]
        
        # Определяем домен для этого ключевого слова
        best_domain = 'general'
        best_confidence = 0.0
        for d in domains:
            if keyword.lower() in [m.lower() for m in d['matched']]:
                best_domain = d['domain']
                best_confidence = d['confidence']
                break
        
        # Создаём вену
        if top_windows:
            vein = SemanticVein(
                keyword=keyword,
                domain=best_domain,
                epsilon_peak=top_windows[0].epsilon,
                resonance_integral=sum(w.resonance for w in windows),
                positions=positions,
                top_fragment=_clean_for_display(top_windows[0].cleaned_text, 2000),
                source_file=source_file,
                confidence=best_confidence,
            )
            veins.append(vein)
        
        # Карта навигации
        navigation_map[keyword] = {
            'positions': positions,
            'count': len(positions),
            'peak_epsilon': top_windows[0].epsilon if top_windows else 0,
            'avg_epsilon': sum(w.epsilon for w in windows) / len(windows) if windows else 0,
            'total_resonance': sum(w.resonance for w in windows),
            'domain': best_domain,
            'confidence': best_confidence,
        }
    
    # Сортируем вены по epsilon_peak
    veins.sort(key=lambda v: -v.epsilon_peak)
    
    return {
        'veins': [
            {
                'keyword': v.keyword,
                'domain': v.domain,
                'epsilon_peak': v.epsilon_peak,
                'resonance_integral': v.resonance_integral,
                'positions': v.positions,
                'top_fragment': v.top_fragment,
                'source_file': v.source_file,
                'confidence': v.confidence,
            }
            for v in veins
        ],
        'domains': domains,
        'theme_map': theme_map,
        'navigation_map': navigation_map,
        'source_file': source_file,
        'stats': {
            'total_keywords': len(keywords),
            'total_veins': len(veins),
            'total_positions': sum(len(v.positions) for v in veins),
        }
    }

# ═══════════════════════════════════════════════════════════════════════
# ЧТЕНИЕ ФАЙЛОВ (TXT, MD, JSON, EPUB, PNG)
# ═══════════════════════════════════════════════════════════════════════

def read_file(path: str) -> str:
    """Читает текст из файла. Поддержка: .txt, .md, .json, .epub"""
    p = Path(path)
    if not p.exists():
        return ""
    
    suffix = p.suffix.lower()
    
    if suffix == '.epub':
        return read_epub(path)
    elif suffix == '.json':
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            return json_to_text(data)
        except:
            return p.read_text(encoding='utf-8')
    else:
        try:
            return p.read_text(encoding='utf-8')
        except:
            return ""

def read_epub(path: str) -> str:
    """Читает текст из EPUB (ZIP с XHTML внутри)."""
    text_parts = []
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('.xhtml') or name.endswith('.html'):
                    content = zf.read(name).decode('utf-8', errors='ignore')
                    text = re.sub(r'<[^>]+>', ' ', content)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        text_parts.append(text)
    except Exception as e:
        return f"[EPUB READ ERROR: {e}]"
    return '\n\n'.join(text_parts)

def json_to_text(data: Any, depth: int = 0) -> str:
    """Рекурсивно извлекает текст из JSON."""
    if depth > 10:
        return ""
    parts = []
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

def read_png_metadata(path: str) -> Dict:
    """Читает метаданные PNG."""
    p = Path(path)
    return {
        'filename': p.name,
        'size_bytes': p.stat().st_size,
        'path': str(p),
        'text': p.stem.replace('_', ' '),
    }

def scan_directory(dir_path: str, extensions: List[str] = None) -> List[str]:
    """Рекурсивно обходит директорию."""
    if extensions is None:
        extensions = ['.md', '.txt', '.json', '.epub', '.html', '.png', '.pdf']
    result = []
    p = Path(dir_path)
    if p.is_file():
        return [str(p)]
    for f in sorted(p.rglob('*')):
        if f.is_file() and f.suffix.lower() in extensions:
            result.append(str(f))
    return result

# ═══════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ЦИКЛ POLER[Ψ] (v3.0 — с венами)
# ═══════════════════════════════════════════════════════════════════════

def run_poler_analyzer(
    text: str,
    keyword: str,
    window_size: int = 20000,
    phi_decay: float = 0.85,
    kappa: float = 1.0,
    top_n: int = 10,
    source_file: str = "",
) -> Dict:
    """Полный цикл POLER[Ψ] для одного ключевого слова."""
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    positions = [m.start() for m in pattern.finditer(text)]

    if not positions:
        return {
            'keyword': keyword,
            'windows': [],
            'summary': None,
            'phase_log': {'perception': 0, 'images': 0, 'pii_filtered': 0,
                          'epsilon_computed': 0, 'resonance_computed': 0},
            'top_by_epsilon': [],
            'top_by_resonance': [],
            'source_file': source_file,
        }

    all_tokens = tokenize(text)
    counts = Counter(all_tokens)
    total_words = len(all_tokens)

    windows: List[TextWindow] = []
    total_pii = 0

    for idx, pos in enumerate(positions):
        start = max(0, pos - window_size // 2)
        end = min(len(text), pos + window_size // 2)
        raw = text[start:end]
        cleaned, filtered = filter_pii(raw)
        total_pii += len(filtered)
        tokens = tokenize(cleaned)
        windows.append(TextWindow(
            index=idx, keyword=keyword, position=pos,
            raw_text=raw, cleaned_text=cleaned,
            filtered_items=filtered, tokens=tokens,
            source_file=source_file,
        ))

    for w in windows:
        w.epsilon = compute_epsilon(w, keyword, counts, total_words, kappa)

    R = compute_resonance_series([w.epsilon for w in windows], phi_decay)
    for w, r in zip(windows, R):
        w.resonance = r

    top_by_eps = sorted(windows, key=lambda w: w.epsilon, reverse=True)[:top_n]
    top_by_R = sorted(windows, key=lambda w: w.resonance, reverse=True)[:top_n]

    total_eps = sum(w.epsilon for w in windows)
    avg_eps = total_eps / len(windows) if windows else 0
    avg_R = sum(w.resonance for w in windows) / len(windows) if windows else 0

    summary = {
        'keyword': keyword,
        'total_text_length': len(text),
        'total_words': total_words,
        'unique_words': len(counts),
        'total_windows': len(windows),
        'total_pii': total_pii,
        'total_epsilon': total_eps,
        'avg_epsilon': avg_eps,
        'avg_resonance': avg_R,
        'peak_epsilon': top_by_eps[0].epsilon if top_by_eps else 0,
        'peak_epsilon_window': top_by_eps[0].index if top_by_eps else -1,
        'peak_resonance': top_by_R[0].resonance if top_by_R else 0,
        'peak_resonance_window': top_by_R[0].index if top_by_R else -1,
        'source_file': source_file,
    }

    return {
        'keyword': keyword,
        'config': {'window_size': window_size, 'phi_decay': phi_decay,
                   'kappa': kappa, 'top_n': top_n},
        'windows': [
            {'index': w.index, 'keyword': w.keyword, 'position': w.position,
             'cleaned_text': w.cleaned_text,
             'filtered_items': [{'original': o, 'replacement': r} for o, r in w.filtered_items],
             'tokens': w.tokens,
             'epsilon': w.epsilon, 'resonance': w.resonance,
             'source_file': w.source_file}
            for w in windows
        ],
        'summary': summary,
        'phase_log': {
            'perception': len(positions), 'images': len(windows),
            'pii_filtered': total_pii, 'epsilon_computed': len(windows),
            'resonance_computed': len(windows),
        },
        'top_by_epsilon': [
            {'index': w.index, 'position': w.position,
             'epsilon': w.epsilon, 'resonance': w.resonance,
             'cleaned_text': w.cleaned_text,
             'pii_count': len(w.filtered_items),
             'source_file': w.source_file}
            for w in top_by_eps
        ],
        'top_by_resonance': [
            {'index': w.index, 'position': w.position,
             'epsilon': w.epsilon, 'resonance': w.resonance,
             'cleaned_text': w.cleaned_text,
             'pii_count': len(w.filtered_items),
             'source_file': w.source_file}
            for w in top_by_R
        ],
    }

# ═══════════════════════════════════════════════════════════════════════
# МУЛЬТИФАЙЛНЫЙ АНАЛИЗАТОР (с венами v3.0)
# ═══════════════════════════════════════════════════════════════════════

def analyze_directory(
    dir_path: str,
    keyword: str,
    window_size: int = 5000,
    phi_decay: float = 0.85,
    kappa: float = 1.0,
    top_n: int = 5,
    cross_resonance: bool = False,
    extensions: List[str] = None,
) -> Dict:
    """Анализирует все файлы в директории по одному ключевому слову."""
    files = scan_directory(dir_path, extensions)
    results_per_file = []
    all_windows: List[TextWindow] = []
    
    for fpath in files:
        suffix = Path(fpath).suffix.lower()
        
        if suffix == '.png':
            meta = read_png_metadata(fpath)
            if keyword.lower() in meta['text'].lower():
                all_windows.append(TextWindow(
                    index=len(all_windows), keyword=keyword, position=0,
                    raw_text=meta['text'], cleaned_text=meta['text'],
                    filtered_items=[], tokens=[],
                    epsilon=10.0, resonance=0.0,
                    source_file=fpath,
                ))
            continue
        
        text = read_file(fpath)
        if not text.strip():
            continue
        
        result = run_poler_analyzer(
            text, keyword, window_size, phi_decay, kappa, top_n, source_file=fpath
        )
        
        if result['summary']:
            results_per_file.append(result)
            for w in result['windows']:
                tw = TextWindow(
                    index=len(all_windows), keyword=keyword, position=w['position'],
                    raw_text=w['cleaned_text'], cleaned_text=w['cleaned_text'],
                    filtered_items=[], tokens=w['tokens'],
                    epsilon=w['epsilon'], resonance=0.0,
                    source_file=fpath,
                )
                all_windows.append(tw)
    
    if cross_resonance and all_windows:
        cross_R = compute_cross_resonance(all_windows, phi_decay)
        for w, r in zip(all_windows, cross_R):
            w.resonance = r
    
    all_windows_sorted = sorted(all_windows, key=lambda w: w.epsilon, reverse=True)[:top_n]
    
    return {
        'keyword': keyword,
        'directory': dir_path,
        'files_scanned': len(files),
        'files_with_hits': len(results_per_file),
        'total_windows': len(all_windows),
        'cross_resonance': cross_resonance,
        'top_by_epsilon': [
            {'epsilon': w.epsilon, 'resonance': w.resonance,
             'source_file': w.source_file,
             'cleaned_text': w.cleaned_text[:2000]}
            for w in all_windows_sorted
        ],
        'per_file_results': [
            {'file': r.get('source_file', r.get('summary', {}).get('source_file', '')), 
             'summary': r['summary']}
            for r in results_per_file
        ],
    }

# ═══════════════════════════════════════════════════════════════════════
# DIFF РЕЖИМ
# ═══════════════════════════════════════════════════════════════════════

def diff_files(file1: str, file2: str, keyword: str, window_size: int = 3000) -> Dict:
    """Сравнивает два файла по ключевому слову."""
    text1 = read_file(file1)
    text2 = read_file(file2)
    
    r1 = run_poler_analyzer(text1, keyword, window_size, source_file=file1)
    r2 = run_poler_analyzer(text2, keyword, window_size, source_file=file2)
    
    s1 = r1['summary'] or {}
    s2 = r2['summary'] or {}
    
    return {
        'keyword': keyword,
        'file1': file1,
        'file2': file2,
        'file1_windows': s1.get('total_windows', 0),
        'file2_windows': s2.get('total_windows', 0),
        'file1_epsilon': s1.get('total_epsilon', 0),
        'file2_epsilon': s2.get('total_epsilon', 0),
        'delta_windows': s2.get('total_windows', 0) - s1.get('total_windows', 0),
        'delta_epsilon': s2.get('total_epsilon', 0) - s1.get('total_epsilon', 0),
        'file1_top': r1['top_by_epsilon'][:3],
        'file2_top': r2['top_by_epsilon'][:3],
    }

# ═══════════════════════════════════════════════════════════════════════
# PYTHON API (v3.0 — с венами и авто-темами)
# ═══════════════════════════════════════════════════════════════════════

class PolerAnalyzer:
    """
    Python API для POLER[Ψ] v3.0.
    
    Использование:
        analyzer = PolerAnalyzer()
        
        # Авто-режим: ключевые слова из текста
        result = analyzer.build_veins(text)
        
        # Ручной режим: пользовательские ключевые слова
        result = analyzer.build_veins(text, keywords=['quantum', 'Hamiltonian'])
        
        # С пользовательскими доменами
        result = analyzer.build_veins(text, custom_seeds={'my_domain': ['marker1', 'marker2']})
        
        # Классический анализ
        result = analyzer.analyze_text(text, keyword='quantum')
    """
    
    def __init__(self, window: int = 5000, phi: float = 0.85, kappa: float = 1.0, top: int = 10):
        self.window = window
        self.phi = phi
        self.kappa = kappa
        self.top = top
    
    def build_veins(
        self,
        text: str,
        keywords: Optional[List[str]] = None,
        custom_seeds: Optional[Dict[str, List[str]]] = None,
        source_file: str = "",
    ) -> Dict:
        """Построение семантических вен (главный метод v3.0)."""
        return build_veins(
            text, keywords, custom_seeds,
            self.window, self.phi, self.kappa, self.top, source_file,
        )
    
    def analyze_file(self, filepath: str, keyword: str) -> Dict:
        """Анализ одного файла."""
        text = read_file(filepath)
        return run_poler_analyzer(text, keyword, self.window, self.phi, self.kappa, self.top, filepath)
    
    def analyze_directory(self, dir_path: str, keyword: str, cross_resonance: bool = False) -> Dict:
        """Анализ всей директории."""
        return analyze_directory(dir_path, keyword, self.window, self.phi, self.kappa, self.top, cross_resonance)
    
    def analyze_text(self, text: str, keyword: str) -> Dict:
        """Анализ текстовой строки."""
        return run_poler_analyzer(text, keyword, self.window, self.phi, self.kappa, self.top)
    
    def analyze_epub(self, epub_path: str, keyword: str) -> Dict:
        """Анализ EPUB файла."""
        text = read_epub(epub_path)
        return run_poler_analyzer(text, keyword, self.window, self.phi, self.kappa, self.top, epub_path)
    
    def diff(self, file1: str, file2: str, keyword: str) -> Dict:
        """Сравнение двух файлов."""
        return diff_files(file1, file2, keyword, self.window)
    
    def discover_themes(self, text: str, custom_seeds: Optional[Dict[str, List[str]]] = None) -> Dict:
        """Только авто-обнаружение тем (без полного анализа)."""
        return auto_discover_themes(text, top_keywords=self.top, custom_seeds=custom_seeds)

# ═══════════════════════════════════════════════════════════════════════
# ФОРМАТЫ ВЫВОДА (v3.0 — с венами)
# ═══════════════════════════════════════════════════════════════════════

def _fmt(n: float, digits: int = 2) -> str:
    return f'{n:,.{digits}f}'

def _clean_for_display(text: str, max_chars: int = 3000) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_chars:
        cut = text.rfind(' ', 0, max_chars)
        text = text[:cut if cut != -1 else max_chars] + ' ...'
    return text

def _highlight_md(text: str, keyword: str) -> str:
    if not keyword:
        return text
    return re.sub(f'({re.escape(keyword)})', r'**\1**', text, flags=re.IGNORECASE)

def format_veins_markdown(result: Dict) -> str:
    """v3.0: MD-отчёт для семантических вен."""
    lines = []
    lines.append('# POLER[Ψ] v3.0 — Семантические Вены')
    lines.append('')
    lines.append(f'> {datetime.now().strftime("%Y-%m-%d %H:%M")} · '
                 f'Цикл: ℘ → O → L → ε → R[n] → Вены')
    if result.get('source_file'):
        lines.append(f'> Файл: `{result["source_file"]}`')
    lines.append('')
    
    # Домены
    domains = result.get('domains', [])
    if domains:
        lines.append('## Обнаруженные домены')
        lines.append('')
        lines.append('| Домен | Совпадений | Confidence | Маркеры |')
        lines.append('|-------|------------|------------|---------|')
        for d in domains:
            markers_str = ', '.join(d['matched'][:3])
            if len(d['matched']) > 3:
                markers_str += f' (+{len(d["matched"])-3})'
            lines.append(f'| {d["domain"]} | {d["hits"]} | {_fmt(d["confidence"])} | {markers_str} |')
        lines.append('')
    
    # Вены
    veins = result.get('veins', [])
    if veins:
        lines.append('## Семантические Вены')
        lines.append('')
        lines.append('| # | Ключевое слово | Домен | ε peak | R integral | Confidence |')
        lines.append('|---|----------------|-------|--------|------------|------------|')
        for i, v in enumerate(veins, 1):
            lines.append(f'| {i} | **{v["keyword"]}** | {v["domain"]} | '
                        f'{_fmt(v["epsilon_peak"], 0)} | {_fmt(v["resonance_integral"], 0)} | '
                        f'{_fmt(v["confidence"])} |')
        lines.append('')
        
        # Топ фрагменты
        for i, v in enumerate(veins[:5], 1):
            lines.append(f'### Вена {i}: «{v["keyword"]}» ({v["domain"]})')
            lines.append(f'> ε peak: {_fmt(v["epsilon_peak"], 0)} · '
                        f'R integral: {_fmt(v["resonance_integral"], 0)} · '
                        f'Позиций: {len(v["positions"])}')
            lines.append('')
            text = _clean_for_display(v['top_fragment'], 1500)
            highlighted = _highlight_md(text, v['keyword'])
            lines.append('```')
            lines.append(highlighted)
            lines.append('```')
            lines.append('')
            lines.append('---')
            lines.append('')
    else:
        lines.append('*Вены не найдены — текст не содержит значимых маркеров.*')
    
    # Карта навигации
    nav = result.get('navigation_map', {})
    if nav:
        lines.append('## Карта навигации')
        lines.append('')
        for kw, info in sorted(nav.items(), key=lambda x: -x[1].get('peak_epsilon', 0)):
            lines.append(f'- **{kw}** ({info["domain"]}): '
                        f'{info["count"]} позиций, '
                        f'ε peak={_fmt(info["peak_epsilon"], 0)}, '
                        f'R total={_fmt(info["total_resonance"], 0)}')
        lines.append('')
    
    return '\n'.join(lines)

def format_directory_markdown(result: Dict) -> str:
    """MD-отчёт для мультфайлного анализа."""
    lines = []
    lines.append(f'# POLER[Ψ] Анализ директории — «{result["keyword"]}»')
    lines.append('')
    lines.append(f'> Сканировано файлов: {result["files_scanned"]} · '
                 f'С совпадениями: {result["files_with_hits"]} · '
                 f'Всего окон: {result["total_windows"]} · '
                 f'Кросс-резонанс: {"ДА" if result["cross_resonance"] else "НЕТ"}')
    lines.append(f'> Директория: `{result["directory"]}`')
    lines.append('')
    
    lines.append(f'## Топ-{len(result["top_by_epsilon"])} фрагментов по ε')
    lines.append('')
    for i, w in enumerate(result['top_by_epsilon'], 1):
        lines.append(f'### {i}. ε={_fmt(w["epsilon"], 0)} · R={_fmt(w["resonance"], 0)}')
        lines.append(f'> Файл: `{w["source_file"]}`')
        lines.append('')
        text = _clean_for_display(w['cleaned_text'], 1500)
        highlighted = _highlight_md(text, result['keyword'])
        lines.append('```')
        lines.append(highlighted)
        lines.append('```')
        lines.append('')
        lines.append('---')
        lines.append('')
    
    lines.append('## Пофайловая сводка')
    lines.append('')
    lines.append('| Файл | Вхождений | Σ ε | Peak ε | Peak R[n] |')
    lines.append('|------|-----------|-----|--------|-----------|')
    for r in sorted(result['per_file_results'], 
                    key=lambda x: -x['summary']['total_epsilon'] if x['summary'] else 0):
        s = r['summary']
        if s:
            fname = Path(r['file']).name
            lines.append(f'| `{fname}` | {s["total_windows"]} | '
                        f'{_fmt(s["total_epsilon"], 0)} | '
                        f'{_fmt(s["peak_epsilon"], 0)} | '
                        f'{_fmt(s["peak_resonance"], 0)} |')
    lines.append('')
    
    return '\n'.join(lines)

def format_diff_markdown(result: Dict) -> str:
    """MD-отчёт для diff-режима."""
    lines = []
    lines.append(f'# POLER[Ψ] Diff — «{result["keyword"]}»')
    lines.append('')
    lines.append(f'| Параметр | Файл 1 | Файл 2 | Δ |')
    lines.append(f'|------|------|------|---|')
    lines.append(f'| Файл | `{Path(result["file1"]).name}` | `{Path(result["file2"]).name}` | |')
    lines.append(f'| Вхождений | {result["file1_windows"]} | {result["file2_windows"]} | {result["delta_windows"]:+d} |')
    lines.append(f'| Σ ε | {_fmt(result["file1_epsilon"], 0)} | {_fmt(result["file2_epsilon"], 0)} | {_fmt(result["delta_epsilon"], 0)} |')
    lines.append('')
    return '\n'.join(lines)

def format_markdown(result: Dict) -> str:
    """MD-вывод (классический)."""
    cfg = result['config']
    summary = result['summary']
    kw = result['keyword']
    
    lines = [f'# POLER[Ψ] Анализ «{kw}»', '']
    lines.append(f'> Цикл: ℘ → O → L → ε → R[n] · {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    if result.get('source_file'):
        lines.append(f'> Файл: `{result["source_file"]}`')
    lines.append('')
    
    if not result['windows']:
        lines.append(f'«{kw}» не найдено.')
        return '\n'.join(lines)
    
    lines.append(f'## Топ-{len(result["top_by_epsilon"])} по ε')
    lines.append('')
    for i, w in enumerate(result['top_by_epsilon'], 1):
        lines.append(f'### {i}. ε={_fmt(w["epsilon"])} · R={_fmt(w["resonance"])}')
        if w.get('source_file'):
            lines.append(f'> Файл: `{w["source_file"]}`')
        lines.append('')
        text = _clean_for_display(w['cleaned_text'], 2000)
        lines.append('```')
        lines.append(_highlight_md(text, kw))
        lines.append('```')
        lines.append('')
        lines.append('---')
        lines.append('')
    
    return '\n'.join(lines)

def format_json(result: Dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════════════════════════════════
# CLI v3.0
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog='poler3',
        description=f'POLER[Ψ] v{__version__} — Ядро Смысла',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('input', nargs='?', help='Файл или директория')
    parser.add_argument('--stdin', action='store_true', help='Читать из stdin')
    parser.add_argument('-k', '--keyword', default='', help='Ключевое слово')
    parser.add_argument('--multi', help='Несколько слов через запятую')
    parser.add_argument('--theme', choices=list(DOMAIN_SEEDS.keys()),
                        help='Автотема (seed-домен)')
    # v3.0: новый режим вен
    parser.add_argument('--veins', action='store_true',
                        help='Построить семантические вены (авто-режим)')
    parser.add_argument('--custom-seeds', type=str, default=None,
                        help='JSON файл с пользовательскими domain seeds')
    parser.add_argument('-w', '--window', type=int, default=20000, help='Размер окна')
    parser.add_argument('--phi', type=float, default=0.85, help='Затухание R[n]')
    parser.add_argument('--kappa', type=float, default=1.0, help='Интенсивность ε')
    parser.add_argument('--top', type=int, default=10, help='Топ-N окон')
    parser.add_argument('-f', '--format', choices=['ascii', 'md', 'json'], default='ascii')
    parser.add_argument('-o', '--output', help='Файл для сохранения')
    
    parser.add_argument('-r', '--recursive', action='store_true', 
                        help='Рекурсивно сканировать директорию')
    parser.add_argument('--cross-resonance', action='store_true',
                        help='Кросс-файлный резонанс (с --recursive)')
    parser.add_argument('--diff', nargs=2, metavar=('FILE1', 'FILE2'),
                        help='Сравнить два файла по ключевому слову')
    parser.add_argument('--include-images', action='store_true',
                        help='Включить PNG (по названию файла)')
    parser.add_argument('--version', action='version', version=f'POLER[Ψ] v{__version__}')
    
    args = parser.parse_args()
    fmt = args.format
    
    # Загружаем пользовательские seeds если есть
    custom_seeds = None
    if args.custom_seeds:
        try:
            with open(args.custom_seeds, 'r', encoding='utf-8') as f:
                custom_seeds = json.load(f)
        except Exception as e:
            sys.stderr.write(f'Ошибка загрузки custom seeds: {e}\n')
            return
    
    # --- DIFF РЕЖИМ ---
    if args.diff:
        result = diff_files(args.diff[0], args.diff[1], args.keyword, args.window)
        output = format_diff_markdown(result) if fmt == 'md' else format_json(result)
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            sys.stderr.write(f'Сохранено: {args.output}\n')
        else:
            print(output)
        return
    
    # --- THEME РЕЖИМ (seed домен) ---
    keywords = [args.keyword] if args.keyword else []
    if args.multi:
        keywords = [k.strip() for k in args.multi.split(',') if k.strip()]
    elif args.theme:
        keywords = DOMAIN_SEEDS[args.theme]
        sys.stderr.write(f'Домен «{args.theme}»: {len(keywords)} seed-маркеров\n')
    
    # --- VENAS РЕЖИМ (v3.0) ---
    if args.veins:
        if args.stdin:
            text = sys.stdin.read()
            source_file = '<stdin>'
        elif args.input:
            if Path(args.input).is_dir():
                # Директория — собираем текст из всех файлов
                sys.stderr.write(f'Сбор текста из директории: {args.input}\n')
                files = scan_directory(args.input)
                parts = []
                for fpath in files:
                    t = read_file(fpath)
                    if t.strip():
                        parts.append(t)
                text = '\n\n'.join(parts)
                source_file = args.input
            else:
                text = read_file(args.input)
                source_file = args.input
        else:
            sys.stderr.write('Ошибка: укажите файл/директорию или --stdin\n')
            return
        
        if not text.strip():
            sys.stderr.write('Ошибка: пустой ввод\n')
            return
        
        result = build_veins(
            text,
            keywords=keywords if keywords else None,
            custom_seeds=custom_seeds,
            window_size=args.window,
            phi_decay=args.phi,
            kappa=args.kappa,
            top_n=args.top,
            source_file=source_file,
        )
        
        if fmt == 'json':
            output = format_json(result)
        elif fmt == 'md':
            output = format_veins_markdown(result)
        else:
            # ASCII сводка вен
            lines = [f'POLER[Ψ] v{__version__} — Семантические Вены', '']
            lines.append(f'Домены: {", ".join(d["domain"] + f" ({_fmt(d["confidence"])})" for d in result["domains"])}')
            lines.append('')
            for i, v in enumerate(result['veins'], 1):
                lines.append(f'  {i}. [{v["domain"]}] {v["keyword"]:20s}  '
                            f'ε={_fmt(v["epsilon_peak"], 0):>12s}  '
                            f'R={_fmt(v["resonance_integral"], 0):>12s}  '
                            f'pos={len(v["positions"])}')
            output = '\n'.join(lines)
        
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            sys.stderr.write(f'Сохранено: {args.output}\n')
        else:
            print(output)
        return
    
    # --- RECURSIVE (директория) ---
    if args.recursive and args.input:
        all_results = []
        for kw in keywords:
            sys.stderr.write(f'Анализ «{kw}» по директории...\n')
            exts = ['.md', '.txt', '.json', '.epub', '.html']
            if args.include_images:
                exts.append('.png')
            result = analyze_directory(
                args.input, kw, args.window, args.phi, args.kappa,
                args.top, args.cross_resonance, exts
            )
            all_results.append(result)
        
        if fmt == 'json':
            output = format_json(all_results if len(all_results) > 1 else all_results[0])
        elif fmt == 'md':
            if len(all_results) == 1:
                output = format_directory_markdown(all_results[0])
            else:
                output = '\n\n---\n\n'.join(format_directory_markdown(r) for r in all_results)
        else:
            lines = [f'POLER[Ψ] v{__version__} — Скан директории', '']
            for r in all_results:
                lines.append(f'  «{r["keyword"]:20s}»  файлов={r["files_with_hits"]:3d}  '
                            f'окон={r["total_windows"]:4d}  '
                            f'топ ε={_fmt(r["top_by_epsilon"][0]["epsilon"], 0) if r["top_by_epsilon"] else "0"}')
            output = '\n'.join(lines)
        
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            sys.stderr.write(f'Сохранено: {args.output}\n')
        else:
            print(output)
        return
    
    # --- ОБЫЧНЫЙ РЕЖИМ (один файл или stdin) ---
    if args.stdin:
        text = sys.stdin.read()
        source_file = '<stdin>'
    elif args.input:
        text = read_file(args.input)
        source_file = args.input
    else:
        parser.print_help()
        return
    
    if not text.strip():
        sys.stderr.write('Ошибка: пустой ввод\n')
        return
    
    if not keywords or keywords == ['']:
        # Нет ключевого слова → авто-режим (вены)
        sys.stderr.write('Ключевое слово не задано → авто-режим (вены)\n')
        result = build_veins(text, custom_seeds=custom_seeds,
                           window_size=args.window, phi_decay=args.phi,
                           kappa=args.kappa, top_n=args.top,
                           source_file=source_file)
        if fmt == 'json':
            output = format_json(result)
        elif fmt == 'md':
            output = format_veins_markdown(result)
        else:
            lines = [f'POLER[Ψ] v{__version__} — Авто-анализ (вены)', '']
            for i, v in enumerate(result['veins'], 1):
                lines.append(f'  {i}. [{v["domain"]}] {v["keyword"]:20s}  '
                            f'ε={_fmt(v["epsilon_peak"], 0):>12s}')
            output = '\n'.join(lines)
    elif len(keywords) > 1:
        results = []
        for kw in keywords:
            sys.stderr.write(f'Анализ «{kw}»...\n')
            r = run_poler_analyzer(text, kw, args.window, args.phi, args.kappa, args.top, source_file)
            results.append(r)
        
        if fmt == 'json':
            output = format_json(results)
        elif fmt == 'md':
            output = format_multi_markdown_enhanced(results, source_file)
        else:
            lines = [f'POLER[Ψ] v{__version__} — Мульти-анализ', '']
            for r in results:
                if r['summary']:
                    s = r['summary']
                    lines.append(f'  «{r["keyword"]:15s}»  вхождений={s["total_windows"]:4d}  '
                                f'Σε={_fmt(s["total_epsilon"], 0):>15s}')
            output = '\n'.join(lines)
    else:
        result = run_poler_analyzer(text, keywords[0], args.window, args.phi, args.kappa, args.top, source_file)
        if fmt == 'md':
            output = format_markdown(result)
        elif fmt == 'json':
            output = format_json(result)
        else:
            output = format_ascii_simple(result)
    
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        sys.stderr.write(f'Сохранено: {args.output}\n')
    else:
        print(output)

def format_ascii_simple(result: Dict) -> str:
    """Упрощённый ASCII-вывод."""
    lines = [f'POLER[Ψ] v{__version__} — «{result["keyword"]}»', '']
    if not result['windows']:
        lines.append('(не найдено)')
        return '\n'.join(lines)
    s = result['summary']
    lines.append(f'Вхождений: {s["total_windows"]} | Σε: {_fmt(s["total_epsilon"], 0)} | '
                f'Peak ε: {_fmt(s["peak_epsilon"], 0)} | PII: {s["total_pii"]}')
    lines.append('')
    for i, w in enumerate(result['top_by_epsilon'][:5], 1):
        lines.append(f'  {i}. ε={_fmt(w["epsilon"])} R={_fmt(w["resonance"])}')
        text = _clean_for_display(w['cleaned_text'], 200)
        lines.append(f'     {text}')
        lines.append('')
    return '\n'.join(lines)

def format_multi_markdown_enhanced(results: List[Dict], source_file: str = '') -> str:
    """MD с всеми ключевыми словами + source_file."""
    lines = ['# POLER[Ψ] v3.0 — Карта документа', '']
    lines.append(f'> {datetime.now().strftime("%Y-%m-%d %H:%M")} · Цикл: ℘ → O → L → ε → R[n]')
    if source_file:
        lines.append(f'> Файл: `{source_file}`')
    lines.append('')
    
    valid = [r for r in results if r.get('summary')]
    if not valid:
        lines.append('Ничего не найдено.')
        return '\n'.join(lines)
    
    lines.append('## Рейтинг по Σε')
    lines.append('')
    lines.append('| # | Слово | Вхождений | Σ ε | Peak ε | Peak R[n] | PII |')
    lines.append('|---|-------|-----------|-----|--------|-----------|-----|')
    sorted_results = sorted(valid, key=lambda r: -r['summary']['total_epsilon'])
    for i, r in enumerate(sorted_results, 1):
        s = r['summary']
        lines.append(f'| {i} | **{r["keyword"]}** | {s["total_windows"]} | '
                     f'{_fmt(s["total_epsilon"], 0)} | {_fmt(s["peak_epsilon"], 0)} | '
                     f'{_fmt(s["peak_resonance"], 0)} | {s["total_pii"]} |')
    lines.append('')
    
    for r in sorted_results:
        kw = r['keyword']
        s = r['summary']
        lines.append(f'## «{kw}»')
        lines.append('')
        lines.append(f'> Вхождений: {s["total_windows"]} · Σε: {_fmt(s["total_epsilon"], 0)} · '
                     f'Peak ε: {_fmt(s["peak_epsilon"], 0)}')
        lines.append('')
        for j, w in enumerate(r['top_by_epsilon'][:3], 1):
            lines.append(f'### Фрагмент {j} — ε={_fmt(w["epsilon"], 0)} · R={_fmt(w["resonance"], 0)}')
            lines.append('')
            cleaned = _clean_for_display(w['cleaned_text'], 2000)
            for line in _highlight_md(cleaned, kw).split('\n'):
                lines.append(f'> {line}' if line.strip() else '>')
            lines.append('')
        lines.append('---')
        lines.append('')
    
    return '\n'.join(lines)


if __name__ == '__main__':
    main()
