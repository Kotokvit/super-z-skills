"""
topic_common — общие утилиты для topic_local.py и topic_llm.py.

Не имеет сторонних зависимостей (только Python stdlib + reusing poler_v6.read_file).

Что делает:
  - read_text(path)            — читает любой файл (.txt/.md/.epub/.zip/.tar.gz/.py/.rs/...)
                                 через poler_v6.read_file, который умеет парсить архивы.
  - is_code(text, path)        — эвристика: код это или естественный текст.
  - detect_language(path, text) — определение языка программирования по расширению + контенту.
  - split_into_chunks(text)    — разбиение на абзацы / предложения, не длиннее max_chars.
  - extract_function_names(text, lang) — имена функций/классов из кода (regex).
  - is_code_heuristic(text)    — численная оценка «код-ли это» от 0 до 1.

Импортируется обоими вариантами (local и llm) чтобы не дублировать логику.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Импортируем read_file из poler_v6 — он умеет EPUB/ZIP/TAR
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:
    from poler_v6 import read_file as _poler_read_file
    _HAS_POLER = True
except Exception:
    _HAS_POLER = False


# ════════════════════════════════════════════════════════════════════════
# LATEX STRIPPING — remove markup before TF-IDF analysis
# ════════════════════════════════════════════════════════════════════════

# LaTeX commands that produce no meaningful text (purely formatting)
_LATEX_NOISE_COMMANDS = {
    'qquad', 'quad', 'hfill', 'vfill', 'noindent', 'bigskip', 'medskip',
    'smallskip', 'bigbreak', 'medbreak', 'smallbreak', 'allowbreak',
    'linebreak', 'nolinebreak', 'pagebreak', 'nopagebreak', 'newpage',
    'clearpage', 'cleardoublepage', 'newline', '\\', 'par', 'indent',
    'centering', 'raggedright', 'raggedleft', 'normalsize', 'small',
    'footnotesize', 'scriptsize', 'tiny', 'large', 'Large', 'LARGE',
    'huge', 'Huge', 'rm', 'sf', 'tt', 'bf', 'it', 'sl', 'sc',
    'cal', 'mit', 'displaystyle', 'textstyle', 'scriptstyle',
    'left', 'right', 'middle', 'big', 'Big', 'bigg', 'Bigg',
    'begin', 'end',  # environment markers — we handle these separately
    'hline', 'cline', 'toprule', 'midrule', 'bottomrule',
    'frac', 'dfrac', 'tfrac', 'sqrt', 'root',
    'label', 'ref', 'eqref', 'cite', 'bibitem', 'index', 'glossary',
    'footnote', 'marginpar', 'not', 'phantom', 'hphantom', 'vphantom',
    'smash', 'boldsymbol', 'mathbf', 'mathcal', 'mathrm', 'mathbb',
    'mathfrak', 'mathsf', 'mathtt', 'mathit', 'mathop', 'mathbin',
    'mathrel', 'mathopen', 'mathclose', 'mathpunct', 'mathord',
    'overline', 'underline', 'overrightarrow', 'overleftarrow',
    'widetilde', 'widehat', 'overbrace', 'underbrace',
    'dot', 'ddot', 'bar', 'vec', 'hat', 'tilde', 'breve', 'check',
    'acute', 'grave', 'ddot',
    'textbf', 'textit', 'textsf', 'textsl', 'textsc', 'texttt',
    'textrm', 'emph', 'mbox', 'hbox', 'vbox',
    'input', 'include', 'includegraphics', 'bibliography',
    'bibliographystyle', 'usepackage', 'documentclass',
    'pagestyle', 'thispagestyle', 'setlength', 'addtolength',
    'setcounter', 'addtocounter', 'stepcounter', 'newcounter',
    'newcommand', 'renewcommand', 'providecommand', 'def', 'let',
    'newenvironment', 'renewenvironment',
    'newtheorem', 'theoremstyle',
    'title', 'author', 'date', 'thanks', 'maketitle', 'tableofcontents',
    'appendix', 'frontmatter', 'mainmatter', 'backmatter',
    'caption', 'subcaption', 'listoffigures', 'listoftables',
    'center', 'flushleft', 'flushright', 'minipage', 'parbox',
    'rule', 'hrule', 'vrule', 'strut', 'phantom',
}

_LATEX_ENVS_NOISE = {
    'figure', 'figure*', 'table', 'table*', 'tabular', 'tabular*',
    'array', 'tikzpicture', 'pspicture', 'picture',
    'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*',
    'gather', 'gather*', 'multline', 'multline*', 'eqnarray', 'eqnarray*',
    'thebibliography', 'bibliography',
}

_LATEX_ENVS_CONTENT = {
    'abstract', 'theorem', 'lemma', 'corollary', 'proposition',
    'definition', 'example', 'remark', 'proof', 'conjecture',
    'acknowledgments', 'acknowledgement',
}


def strip_latex(text: str) -> str:
    """Remove LaTeX markup, keeping readable prose text.

    Strategy:
      1. Remove comment lines (%...)
      2. Remove noise environments entirely (tikzpicture, figure, equation, tabular...)
      3. Remove \\command{...} where command is noise, keep {content} for content commands
      4. Remove $...$ and $$...$$ math mode
      5. Remove remaining \command (bare commands)
      6. Remove { } braces
      7. Collapse whitespace

    Returns stripped text suitable for TF-IDF analysis.
    """
    if not text:
        return text

    # 1. Remove comment lines (% to end of line, but not \%)
    text = re.sub(r'(?<!\\)%.*$', '', text, flags=re.MULTILINE)

    # 2. Remove noise environments entirely
    for env in _LATEX_ENVS_NOISE:
        # \begin{env} ... \end{env}
        pattern = re.compile(
            r'\\begin\s*\{' + re.escape(env) + r'\*?\}.*?\\end\s*\{' + re.escape(env) + r'\*?\}',
            re.DOTALL
        )
        text = pattern.sub(' ', text)

    # 3. For content environments, keep the body but remove \begin/\end markers
    for env in _LATEX_ENVS_CONTENT:
        text = re.sub(r'\\begin\s*\{' + re.escape(env) + r'\*?\}', ' ', text)
        text = re.sub(r'\\end\s*\{' + re.escape(env) + r'\*?\}', ' ', text)

    # 4. Remove math modes: $$...$$ then $...$
    text = re.sub(r'\$\$.*?\$\$', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\$.*?\$', ' ', text)

    # Also handle \(...\) and \[...\]
    text = re.sub(r'\\\(|\\\)', ' ', text)
    text = re.sub(r'\\\[.*?\\\]', ' ', text, flags=re.DOTALL)

    # 5. Remove \command{arg} — keep arg for text commands, remove for noise commands
    # First pass: noise commands — remove entire \cmd{arg}
    for cmd in _LATEX_NOISE_COMMANDS:
        # \cmd{...} — remove everything including braces content
        text = re.sub(r'\\' + re.escape(cmd) + r'\s*\{[^}]*\}', ' ', text)
        # \cmd without braces — remove
        text = re.sub(r'\\' + re.escape(cmd) + r'(?![a-zA-Z])', ' ', text)

    # Second pass: text commands that have meaningful content — keep the arg
    text_commands = {'section', 'subsection', 'subsubsection', 'chapter', 'paragraph',
                     'text', 'footnotetext', 'title', 'author'}
    for cmd in text_commands:
        # Replace \cmd{content} → content
        text = re.sub(r'\\' + re.escape(cmd) + r'\*?\s*\{([^}]*)\}', r'\1', text)

    # 6. Remove remaining \command (bare LaTeX commands)
    text = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?', ' ', text)

    # 7. Remove { } braces
    text = re.sub(r'[{}]', ' ', text)

    # 8. Remove leftover special chars
    text = re.sub(r'[~^_&]', ' ', text)

    # 9. Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def read_text(path: str) -> str:
    """Читает файл любого поддерживаемого типа → str.

    Использует poler_v6.read_file если доступен (поддержка .epub/.zip/.tar.gz).
    Иначе — обычное открытие в текстовом режиме с errors='replace'.
    """
    if _HAS_POLER:
        try:
            return _poler_read_file(path)
        except Exception:
            pass
    # Fallback: plain read
    encodings = ['utf-8', 'cp1251', 'latin-1']
    for enc in encodings:
        try:
            with open(path, 'r', encoding=enc, errors='strict') as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # Last resort
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


# ════════════════════════════════════════════════════════════════════════
# CODE vs PROSE DETECTION
# ════════════════════════════════════════════════════════════════════════

# Расширения файлов, которые ВСЕГДА код (overrides any heuristic)
_CODE_EXTENSIONS = {
    '.py', '.rs', '.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.hxx',
    '.java', '.kt', '.scala', '.groovy', '.js', '.ts', '.tsx', '.jsx',
    '.mjs', '.cjs', '.go', '.rb', '.php', '.swift', '.m', '.mm',
    '.pl', '.pm', '.lua', '.r', '.R', '.jl', '.ex', '.exs', '.erl',
    '.elm', '.hs', '.lhs', '.ml', '.mli', '.fs', '.fsi', '.fsx',
    '.clj', '.cljs', '.cljc', '.edn', '.lisp', '.scm', '.rkt',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
    '.sql', '.graphql', '.gql', '.proto', '.thrift',
    '.vim', '.el', '.tcl', '.awk', '.sed',
    '.toml', '.yaml', '.yml', '.json', '.ini', '.cfg', '.conf',
    '.xml', '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.dockerfile', '.makefile', '.cmake', '.ninja',
}

# Маркеры кода в содержимом
_CODE_INDICATORS = [
    r'^\s*def\s+\w+\s*\(',
    r'^\s*class\s+\w+',
    r'^\s*import\s+\w+',
    r'^\s*from\s+\w+\s+import',
    r'^\s*#include\s+[<"]',
    r'^\s*(public|private|protected)\s+',
    r'^\s*func\s+\w+\s*\(',
    r'^\s*fn\s+\w+\s*\(',
    r'^\s*function\s+\w+\s*\(',
    r'^\s*const\s+\w+\s*=',
    r'^\s*let\s+\w+\s*=',
    r'^\s*var\s+\w+\s*=',
    r'^\s*package\s+\w+',
    r'^\s*module\s+\w+',
    r'^\s*use\s+\w+::',
    r';\s*$',
    r'\{\s*$',
    r'^\s*\}\s*$',
    r'=>\s*\{?',
    r'->\s*\w+',
]

# Язык по расширению (для code-mode)
_EXT_TO_LANG = {
    '.py': 'Python', '.pyw': 'Python', '.pyx': 'Cython',
    '.rs': 'Rust',
    '.c': 'C', '.h': 'C',
    '.cpp': 'C++', '.cc': 'C++', '.cxx': 'C++',
    '.hpp': 'C++', '.hxx': 'C++',
    '.java': 'Java',
    '.kt': 'Kotlin', '.kts': 'Kotlin',
    '.scala': 'Scala', '.sc': 'Scala',
    '.groovy': 'Groovy',
    '.js': 'JavaScript', '.mjs': 'JavaScript', '.cjs': 'JavaScript',
    '.ts': 'TypeScript', '.tsx': 'TypeScript (React)',
    '.jsx': 'JavaScript (React)',
    '.go': 'Go',
    '.rb': 'Ruby',
    '.php': 'PHP',
    '.swift': 'Swift',
    '.m': 'Objective-C', '.mm': 'Objective-C++',
    '.pl': 'Perl', '.pm': 'Perl',
    '.lua': 'Lua',
    '.r': 'R', '.R': 'R',
    '.jl': 'Julia',
    '.ex': 'Elixir', '.exs': 'Elixir',
    '.erl': 'Erlang',
    '.hs': 'Haskell', '.lhs': 'Haskell (literate)',
    '.ml': 'OCaml', '.mli': 'OCaml (interface)',
    '.fs': 'F#', '.fsi': 'F# (sig)', '.fsx': 'F# (script)',
    '.clj': 'Clojure', '.cljs': 'ClojureScript',
    '.lisp': 'Common Lisp', '.scm': 'Scheme', '.rkt': 'Racket',
    '.sh': 'Shell', '.bash': 'Bash', '.zsh': 'Zsh',
    '.ps1': 'PowerShell',
    '.sql': 'SQL', '.graphql': 'GraphQL', '.gql': 'GraphQL',
    '.proto': 'Protocol Buffers',
    '.vim': 'Vim script', '.el': 'Emacs Lisp',
    '.tcl': 'Tcl',
}


def is_code_heuristic(text: str) -> float:
    """Возвращает долю строк, похожих на код (0.0–1.0).

    Используется когда расширение не однозначно (.txt, .md, без расширения).
    Порог > 0.20 = скорее код, < 0.05 = точно текст.
    """
    lines = text.splitlines()
    if len(lines) < 3:
        return 0.0
    code_lines = 0
    for line in lines:
        for pattern in _CODE_INDICATORS:
            if re.match(pattern, line):
                code_lines += 1
                break
    return code_lines / len(lines)


def is_code(text: str, path: Optional[str] = None,
            threshold: float = 0.20) -> bool:
    """True если это код (по расширению ИЛИ по содержимому)."""
    if path:
        ext = Path(path).suffix.lower()
        if ext in _CODE_EXTENSIONS:
            return True
    return is_code_heuristic(text) >= threshold


def detect_language(path: Optional[str], text: str) -> str:
    """Определяет язык программирования. Расширение优先; fallback на regex."""
    if path:
        ext = Path(path).suffix.lower()
        if ext in _EXT_TO_LANG:
            return _EXT_TO_LANG[ext]
        # Спец-имена файлов
        name = Path(path).name.lower()
        if name in {'dockerfile', 'containerfile'}:
            return 'Dockerfile'
        if name in {'makefile', 'gnumakefile'}:
            return 'Makefile'
        if name in {'cmakelists.txt'}:
            return 'CMake'
        if name.startswith('.') and name in {'.bashrc', '.zshrc', '.profile'}:
            return 'Shell config'

    # Fallback: regex patterns
    if re.search(r'^\s*def\s+\w+\s*\(.*\):\s*$', text, re.MULTILINE):
        return 'Python (by content)'
    if re.search(r'^\s*fn\s+\w+\s*\(.*\)\s*->', text, re.MULTILINE) or \
       re.search(r'^\s*use\s+\w+::', text, re.MULTILINE):
        return 'Rust (by content)'
    if re.search(r'^\s*#include\s+[<"]', text, re.MULTILINE):
        return 'C/C++ (by content)'
    if re.search(r'^\s*package\s+\w+\s*$', text, re.MULTILINE) and \
       re.search(r'^\s*func\s+\w+\s*\(', text, re.MULTILINE):
        return 'Go (by content)'
    if re.search(r'^\s*(public|private|protected)\s+(class|static|void|int|String)',
                 text, re.MULTILINE):
        return 'Java/C# (by content)'
    if re.search(r'^\s*function\s+\w+\s*\(', text, re.MULTILINE) or \
       re.search(r'^\s*const\s+\w+\s*=\s*\(', text, re.MULTILINE):
        return 'JavaScript (by content)'

    return 'Unknown (likely prose or unrecognised code)'


# ════════════════════════════════════════════════════════════════════════
# CHUNKING
# ════════════════════════════════════════════════════════════════════════

def split_into_chunks(text: str, max_chars: int = 1500,
                      min_chars: int = 80) -> List[str]:
    """Разбивает текст на смысловые куски.

    Стратегия:
      1. Сначала по двойным переводам строк (абзацы).
      2. Длинные абзацы режем по предложениям, не превышая max_chars.
      3. Слишком короткие куски (< min_chars) приклеиваем к следующему.

    Возвращает список непустых строк.
    """
    if not text or not text.strip():
        return []

    # 1. По абзацам
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    # 2. Длинные абзацы режем по предложениям
    final_chunks: List[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            final_chunks.append(p)
            continue
        # Режем по предложениям (точка/!/? + пробел)
        sentences = re.split(r'(?<=[.!?])\s+', p)
        buf = ""
        for sent in sentences:
            if len(buf) + len(sent) + 1 <= max_chars:
                buf = (buf + " " + sent).strip() if buf else sent
            else:
                if buf:
                    final_chunks.append(buf)
                # Если одно предложение длиннее max_chars — режем тупо по длине
                if len(sent) > max_chars:
                    for i in range(0, len(sent), max_chars):
                        final_chunks.append(sent[i:i + max_chars])
                    buf = ""
                else:
                    buf = sent
        if buf:
            final_chunks.append(buf)

    # 3. Склеиваем слишком короткие
    merged: List[str] = []
    for ch in final_chunks:
        if merged and len(ch) < min_chars:
            merged[-1] = (merged[-1] + " " + ch).strip()
        elif ch:
            merged.append(ch)

    return merged or ([text.strip()] if text.strip() else [])


# ════════════════════════════════════════════════════════════════════════
# CODE STRUCTURE EXTRACTION
# ════════════════════════════════════════════════════════════════════════

def extract_code_entities(text: str, lang: str = '') -> Dict[str, List[str]]:
    """Извлекает имена функций, классов, модулей из кода.

    Возвращает словарь:
      { 'classes': [...], 'functions': [...], 'imports': [...], 'constants': [...] }
    """
    result = {'classes': [], 'functions': [], 'imports': [], 'constants': []}

    # Classes (Python/Rust/Java/JS/etc.)
    for m in re.finditer(r'\bclass\s+(\w+)', text):
        result['classes'].append(m.group(1))

    # Functions
    # Python: def name( / Rust: fn name( / JS: function name( / Go: func name(
    # Java/C: returnType name(
    for m in re.finditer(r'\b(?:def|fn|func|function|void|int|float|double|str|String)\s+(\w+)\s*\(', text):
        name = m.group(1)
        if name not in {'if', 'for', 'while', 'switch', 'return', 'sizeof'}:
            result['functions'].append(name)

    # Imports
    for m in re.finditer(r'\b(?:import|use|include|#include|from)\s+([\w\.:/-]+)', text):
        result['imports'].append(m.group(1))

    # Top-level constants (Python/Rust/JS)
    for m in re.finditer(r'^\s*(?:const|static|let|final|readonly)\s+(\w+)', text, re.MULTILINE):
        result['constants'].append(m.group(1))

    # Dedupe preserving order, cap at 20 per category
    for k in result:
        seen = set()
        unique = []
        for item in result[k]:
            if item not in seen:
                seen.add(item)
                unique.append(item)
                if len(unique) >= 20:
                    break
        result[k] = unique

    return result


# ════════════════════════════════════════════════════════════════════════
# COMMON OUTPUT FORMAT
# ════════════════════════════════════════════════════════════════════════

def format_output_human(result: Dict) -> str:
    """Человекочитаемый вывод. result — словарь из detect_topics()."""
    lines = []
    if result.get('is_code'):
        lines.append(f"📦 КОД — язык: {result.get('language', '?')}")
        if result.get('overall_topic'):
            lines.append(f"🎯 Назначение: {result['overall_topic']}")
        entities = result.get('entities', {})
        if any(entities.values()):
            lines.append("")
            lines.append("Структура:")
            if entities.get('classes'):
                lines.append(f"  Классы:     {', '.join(entities['classes'][:10])}")
            if entities.get('functions'):
                lines.append(f"  Функции:    {', '.join(entities['functions'][:10])}")
            if entities.get('imports'):
                lines.append(f"  Импорты:    {', '.join(entities['imports'][:10])}")
            if entities.get('constants'):
                lines.append(f"  Константы:  {', '.join(entities['constants'][:10])}")
        return '\n'.join(lines)

    # Prose
    n_clusters = len(result.get('clusters', []))
    lines.append(f"📄 ТЕКСТ — разбит на {n_clusters} "
                 f"{'кластер' if n_clusters == 1 else 'кластера' if 2 <= n_clusters <= 4 else 'кластеров'}")
    if result.get('overall_topic'):
        lines.append(f"🎯 Общая тема: {result['overall_topic']}")
    lines.append("")
    for i, cl in enumerate(result.get('clusters', [])):
        lines.append(f"  Кластер {i+1} ({cl.get('size', '?')} фрагм.):")
        lines.append(f"    Тема: {cl.get('topic', '?')}")
        preview = cl.get('preview', '')[:200]
        if preview:
            lines.append(f"    Превью: {preview}{'...' if len(cl.get('preview', '')) > 200 else ''}")
        lines.append("")
    return '\n'.join(lines).rstrip()


def format_output_json(result: Dict) -> str:
    """JSON-вывод для агентов."""
    import json
    return json.dumps(result, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT (for testing)
# ════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование: python topic_common.py <файл>")
        sys.exit(1)
    path = sys.argv[1]
    text = read_text(path)
    print(f"File: {path}")
    print(f"Size: {len(text)} chars")
    print(f"is_code: {is_code(text, path)}")
    print(f"is_code_heuristic: {is_code_heuristic(text):.3f}")
    print(f"language: {detect_language(path, text)}")
    chunks = split_into_chunks(text)
    first_preview = chunks[0][:100] if chunks else 'EMPTY'
    print(f"chunks: {len(chunks)} (first: {first_preview!r})")
    if is_code(text, path):
        ents = extract_code_entities(text, detect_language(path, text))
        print(f"entities: {ents}")
