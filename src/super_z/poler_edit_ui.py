from __future__ import annotations

import argparse
import html
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .poler_edit import PolerEdit
from .poler_smart_interpreter import analyze_code as smart_analyze_code


# ============================================================================
#  HTML / CSS
# ============================================================================

_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolerEdit — анализ текста и кода</title>
<style>
:root {{
  --bg:#101418; --panel:#182027; --line:#31404a; --text:#e8eef0; --muted:#93a3aa;
  --accent:#62d5b0; --accent2:#7ab8ff; --warn:#f4b8bf; --soft:#2a3641;
  --bar-bg:#0d1216; --bar-fill:#62d5b0; --bar-fill-2:#7ab8ff;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:linear-gradient(135deg,#101418,#1d2930); color:var(--text); font:15px/1.55 system-ui,-apple-system,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:28px 18px 60px; }}
h1 {{ margin:0 0 6px; font-size:34px; letter-spacing:-0.5px; }}
h2 {{ margin:0 0 12px; font-size:20px; }}
h3 {{ margin:18px 0 8px; font-size:15px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; font-weight:600; }}
p {{ color:var(--muted); margin:0 0 8px; }}
.subtitle {{ color:var(--muted); margin-bottom:18px; font-size:14px; }}

.intro {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:18px; font-size:13px; color:var(--muted); }}
.intro strong {{ color:var(--text); }}
.intro details {{ margin-top:8px; }}
.intro summary {{ cursor:pointer; color:var(--accent2); user-select:none; }}

.presets {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
.preset {{ background:var(--soft); border:1px solid var(--line); color:var(--text); padding:7px 14px; border-radius:16px; font-size:13px; cursor:pointer; transition:all 0.15s; }}
.preset:hover {{ background:var(--accent); color:#08251d; border-color:var(--accent); }}

.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }}
section.field {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
label {{ display:block; color:var(--muted); margin:0 0 6px; font-size:13px; font-weight:500; }}
textarea {{ width:100%; min-height:240px; resize:vertical; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:10px; font:13px/1.5 'SF Mono',Consolas,Menlo,monospace; }}
input {{ width:100%; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px 10px; font:inherit; }}
button.submit {{ margin-top:12px; background:var(--accent); color:#08251d; border:0; border-radius:6px; padding:10px 22px; font-weight:700; font-size:15px; cursor:pointer; }}
button.submit:hover {{ background:#7ee5c2; }}
button.submit.smart {{ background:var(--accent2); margin-left:8px; }}
button.submit.smart:hover {{ background:#9bc8ff; }}
.button-row {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.mode-row {{ display:flex; gap:18px; margin-top:14px; padding:10px 12px; background:var(--bg); border:1px solid var(--line); border-radius:6px; flex-wrap:wrap; }}
.mode-opt {{ display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:13px; cursor:pointer; }}
.mode-opt input {{ cursor:pointer; }}

.result {{ margin-top:20px; }}
.summary-box {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:14px; }}
.summary-line {{ font-size:15px; margin-bottom:6px; }}
.summary-line strong {{ color:var(--accent); }}

.top-words {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }}
.word-chip {{ background:var(--bg); border:1px solid var(--line); border-radius:4px; padding:4px 10px; font-size:13px; }}
.word-chip .num {{ color:var(--accent2); margin-left:4px; font-weight:bold; }}

.bar {{ display:inline-block; width:140px; height:8px; background:var(--bar-bg); border-radius:4px; vertical-align:middle; margin:0 8px; overflow:hidden; }}
.bar > span {{ display:block; height:100%; background:var(--bar-fill); border-radius:4px; }}

.fragment {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:10px; }}
.fragment-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.fragment-num {{ color:var(--accent); font-weight:bold; font-size:13px; }}
.fragment-kw {{ background:var(--accent2); color:#08251d; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:bold; }}
.fragment-meta {{ color:var(--muted); font-size:12px; }}
.fragment-text {{ background:var(--bg); border-left:3px solid var(--accent); padding:10px 12px; border-radius:0 4px 4px 0; font:13px/1.5 'SF Mono',Consolas,Menlo,monospace; white-space:pre-wrap; max-height:300px; overflow:auto; }}
.fragment-text mark {{ background:rgba(122,184,255,0.3); color:var(--accent2); padding:1px 2px; border-radius:2px; font-weight:bold; }}
.fragment-text .line-num {{ color:var(--muted); display:inline-block; width:32px; text-align:right; padding-right:8px; user-select:none; }}

.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:6px; margin-top:10px; }}
.metric {{ background:var(--bg); padding:6px 9px; border-radius:4px; border:1px solid var(--line); }}
.metric-label {{ color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:0.5px; }}
.metric-value {{ color:var(--text); font-size:14px; font-weight:600; margin-top:2px; }}
.metric-value.green {{ color:var(--accent); }}
.metric-value.blue {{ color:var(--accent2); }}

.collapsible {{ margin-top:8px; }}
.collapsible summary {{ cursor:pointer; color:var(--muted); font-size:12px; user-select:none; padding:4px 0; }}
.collapsible summary:hover {{ color:var(--accent2); }}
.collapsible pre {{ background:var(--bg); padding:8px; border-radius:4px; font-size:11px; max-height:200px; overflow:auto; margin:6px 0; }}

.error-box {{ background:#2d1518; border:1px solid #6b2c33; color:var(--warn); padding:12px; border-radius:6px; margin-bottom:12px; white-space:pre-wrap; }}

.empty {{ text-align:center; padding:40px 20px; color:var(--muted); font-size:14px; }}

details.table-wrap {{ margin-top:10px; }}
details.table-wrap summary {{ cursor:pointer; color:var(--accent2); font-size:13px; margin-bottom:6px; user-select:none; }}
details.table-wrap table {{ border-collapse:collapse; width:100%; font-size:12px; }}
details.table-wrap th, details.table-wrap td {{ padding:5px 8px; border:1px solid var(--line); text-align:left; }}
details.table-wrap th {{ color:var(--muted); background:var(--bg); font-weight:600; }}
details.table-wrap tr:hover td {{ background:var(--soft); }}

@media (max-width:760px) {{
  .grid {{ grid-template-columns:1fr; }}
  h1 {{ font-size:26px; }}
  .bar {{ width:80px; }}
}}
</style>
</head>
<body><main>
<h1>PolerEdit</h1>
<p class="subtitle">Находит ключевые слова в тексте или коде и показывает, где и насколько они значимы.</p>

<div class="intro">
  <strong>Что делает POLER:</strong> ищет слово (или несколько) в тексте и для каждого находит «силу сигнала» (ε — epsilon)
  и «резонанс» (R — как часто и плотно слово встречается). Чем выше значения — тем важнее слово в этом тексте.
  <details>
    <summary>Подробнее о метриках</summary>
    <p style="margin-top:8px;">
      <strong>ε (epsilon)</strong> — «напряжение» вокруг слова. Считается по расстоянию между соседними появлениями.
      Высокое ε = слово встречается «кластерами», а не равномерно.<br>
      <strong>R (resonance)</strong> — сумма ε по всем появлениям. Грубо: «насколько слово заметно во всём тексте».<br>
      <strong>positions</strong> — символьные смещения, где слово найдено (для перехода в исходник).<br>
      <strong>domain</strong> — тематический домен (если POLER смог определить; иначе «general»).
    </p>
  </details>
</div>

<div class="presets">
  <button class="preset" onclick="loadPreset('code')">Пример: Python код</button>
  <button class="preset" onclick="loadPreset('text')">Пример: текст</button>
  <button class="preset" onclick="loadPreset('search')">Поиск одного слова</button>
</div>

<form method="post"><div class="grid">
<section class="field"><label for="query">Запит (ключове слово или слова через запятую)</label><input id="query" name="query" value="{query}" placeholder="Например: ИИ  или  риск, угроза, возможность"></section>
<section class="field"><label for="source">Файл / мова (необов'язково)</label><input id="source" name="source" value="{source}" placeholder="example.py, article.txt  (для кода добавляется синтаксическая проверка)"><label for="text" style="margin-top:10px;">Текст или код</label><textarea id="text" name="text" placeholder="Вставьте текст или код для анализа...">{text}</textarea></section>
</div>
<div class="mode-row">
  <label class="mode-opt"><input type="radio" name="mode" value="analyze" {chk_analyze}> Анализ (POLER v3.0)</label>
  <label class="mode-opt"><input type="radio" name="mode" value="smart" {chk_smart}> Smart-анализ Python (AST + правила + POLER)</label>
</div>
<div class="button-row">
  <button type="submit" class="submit">Анализировать</button>
  <button type="submit" name="mode" value="smart" class="submit smart">⚡ Smart-анализ Python</button>
</div>
</form>

{result}
</main>

<script>
const PRESETS = __PRESETS_JSON__;
function loadPreset(name) {{
  const p = PRESETS[name];
  document.getElementById('query').value = p.query;
  document.getElementById('source').value = p.source;
  document.getElementById('text').value = p.text;
}}
</script>
</body></html>"""


# Example presets injected into the page as JSON.
_PRESETS = {
    "code": {
        "query": "",
        "source": "example.py",
        "text": (
            "def calculate_total(items, tax_rate=0.2):\n"
            "    '''Calculate total with tax.'''\n"
            "    subtotal = sum(item.price for item in items)\n"
            "    tax = subtotal * tax_rate\n"
            "    return subtotal + tax\n\n"
            "class ShoppingCart:\n"
            "    def __init__(self):\n"
            "        self.items = []\n\n"
            "    def add(self, item):\n"
            "        self.items.append(item)\n\n"
            "    def total(self):\n"
            "        return calculate_total(self.items)"
        ),
    },
    "text": {
        "query": "",
        "source": "",
        "text": (
            "Искусственный интеллект меняет мир. ИИ способен обрабатывать огромные объемы данных. "
            "Многие боятся, что ИИ заменит людей. Но ИИ — это инструмент, а не замена. "
            "ИИ работает лучше всего там, где человек ставит задачу. "
            "Будущее за связкой человек + ИИ."
        ),
    },
    "search": {
        "query": "ИИ",
        "source": "",
        "text": (
            "Искусственный интеллект меняет мир. ИИ способен обрабатывать огромные объемы данных. "
            "Многие боятся, что ИИ заменит людей. Но ИИ — это инструмент, а не замена. "
            "ИИ работает лучше всего там, где человек ставит задачу. "
            "Будущее за связкой человек + ИИ."
        ),
    },
}


def _build_page(query: str, source: str, text: str, result: str, mode: str = "analyze") -> str:
    """Render the full HTML page with presets injected as JSON."""
    presets_json = json.dumps(_PRESETS, ensure_ascii=False)
    chk_analyze = "checked" if mode != "smart" else ""
    chk_smart = "checked" if mode == "smart" else ""
    return _PAGE.format(
        query=query, source=source, text=text, result=result,
        chk_analyze=chk_analyze, chk_smart=chk_smart,
    ).replace("__PRESETS_JSON__", presets_json)


# ============================================================================
#  Helpers
# ============================================================================

def _fmt(value, default: str = "—") -> str:
    """Format a numeric score; return default for None/missing/invalid."""
    if value is None:
        return default
    try:
        f = float(value)
        if abs(f) >= 1_000_000:
            return "{:.1f}M".format(f / 1_000_000)
        if abs(f) >= 1000:
            return "{:.1f}K".format(f / 1000)
        if abs(f) >= 100:
            return "{:.0f}".format(f)
        return "{:.2f}".format(f)
    except (TypeError, ValueError):
        return str(value) if value != "" else default


def _strength_bar(value: float, max_value: float) -> str:
    """Render a horizontal strength bar 0..100%."""
    if max_value <= 0:
        pct = 0
    else:
        pct = max(2, min(100, int((value / max_value) * 100)))
    return '<span class="bar"><span style="width:{pct}%"></span></span>'.format(pct=pct)


def _vein_text(vein: dict) -> str:
    """Extract display text from a POLER v3.0 vein."""
    return (
        vein.get("top_fragment")
        or vein.get("fragment")
        or vein.get("cleaned_text")
        or vein.get("raw_text")
        or vein.get("text")
        or vein.get("snippet")
        or ""
    )


def _highlight_keyword(text: str, keyword: str) -> str:
    """Wrap keyword occurrences in <mark> tags (case-insensitive, escaped)."""
    if not text or not keyword:
        return html.escape(text or "")
    escaped = html.escape(text)
    # Build case-insensitive regex for the keyword (escaped)
    kw_escaped = re.escape(html.escape(keyword))
    pattern = re.compile("(" + kw_escaped + ")", re.IGNORECASE)
    return pattern.sub(r"<mark>\1</mark>", escaped)


def _short_context(full_text: str, keyword: str, max_chars: int = 600) -> str:
    """Return a short context window around the first match of keyword."""
    if not full_text or not keyword:
        return full_text or ""
    idx = full_text.lower().find(keyword.lower())
    if idx == -1:
        return full_text[:max_chars]
    start = max(0, idx - max_chars // 2)
    end = min(len(full_text), idx + max_chars // 2)
    snippet = full_text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(full_text):
        snippet = snippet + "…"
    return snippet


def _render_summary(analysis: dict) -> str:
    """Human-readable summary at the top: 'found N keywords, top: X, Y, Z'."""
    poler_v3 = analysis.get("poler_v3") or {}
    if not isinstance(poler_v3, dict):
        return ""
    nav = poler_v3.get("navigation_map") or {}
    stats = poler_v3.get("stats") or {}

    if not nav:
        return ""

    # Sort by count desc, take top 5
    sorted_words = sorted(nav.items(), key=lambda kv: -(kv[1].get("count", 0) if isinstance(kv[1], dict) else 0))
    top_words = sorted_words[:5]
    total_words = len(nav)
    total_positions = stats.get("total_positions", sum(kv[1].get("count", 0) for kv in sorted_words if isinstance(kv[1], dict)))

    chips = "".join(
        '<span class="word-chip">{kw}<span class="num">×{cnt}</span></span>'.format(
            kw=html.escape(str(kw)), cnt=info.get("count", 0) if isinstance(info, dict) else 0
        )
        for kw, info in top_words
    )

    mode = analysis.get("mode", "text")
    mode_label = {"text": "текст", "python": "Python код", "code": "код"}.get(mode, mode)

    return (
        '<div class="summary-box">'
        '<div class="summary-line">Найдено <strong>{words}</strong> ключевых слов · <strong>{positions}</strong> упоминаний · режим: <strong>{mode}</strong></div>'
        '<div class="summary-line" style="color:var(--muted);font-size:13px;">Самые частые:</div>'
        '<div class="top-words">{chips}</div>'
        '</div>'
    ).format(words=total_words, positions=total_positions, mode=html.escape(mode_label), chips=chips)


def _render_fragment(index: int, vein: dict, full_text: str, max_resonance: float) -> str:
    """Render one fragment with highlighted keyword and human-readable metrics."""
    keyword = vein.get("keyword") or ""
    domain = vein.get("domain") or ""
    count = len(vein.get("positions") or [])
    epsilon_peak = vein.get("epsilon_peak") or 0
    resonance_int = vein.get("resonance_integral") or 0
    confidence = vein.get("confidence") or 0

    # Build a short context snippet around the keyword
    raw_text = _vein_text(vein)
    if not raw_text and full_text and keyword:
        raw_text = _short_context(full_text, keyword, max_chars=600)
    elif not raw_text:
        raw_text = "(фрагмент недоступен)"

    # Highlight the keyword in the snippet
    highlighted = _highlight_keyword(raw_text, keyword)

    # Strength bar (relative to max resonance)
    bar = _strength_bar(float(resonance_int), max_resonance)

    # First few positions as "char N, char N..."
    positions = vein.get("positions") or []
    pos_preview = positions[:5]
    pos_str = ", ".join(str(p) for p in pos_preview)
    if len(positions) > 5:
        pos_str += " +{} ещё".format(len(positions) - 5)

    # Domain badge
    domain_html = ''
    if domain and domain != "general":
        domain_html = ' <span style="color:var(--accent);font-size:11px;background:var(--bg);padding:2px 6px;border-radius:3px;">{}</span>'.format(html.escape(str(domain)))

    return (
        '<div class="fragment">'
        '<div class="fragment-header">'
        '<div><span class="fragment-num">#{n}</span> <span class="fragment-kw">{kw}</span>{domain}</div>'
        '<div class="fragment-meta">найден ×{count} · сила сигнала: {bar} {pct}%</div>'
        '</div>'
        '<div class="fragment-text">{text}</div>'
        '<div class="metrics">'
        '<div class="metric"><div class="metric-label">ε peak</div><div class="metric-value blue">{ep}</div></div>'
        '<div class="metric"><div class="metric-label">R ∫</div><div class="metric-value green">{ri}</div></div>'
        '<div class="metric"><div class="metric-label">упоминаний</div><div class="metric-value">{cnt}</div></div>'
        '<div class="metric"><div class="metric-label">уверенность</div><div class="metric-value">{cf}</div></div>'
        '</div>'
        '<details class="collapsible"><summary>позиции в тексте ({total})</summary><pre style="font-size:11px;">{pos}</pre></details>'
        '</div>'
    ).format(
        n=index + 1, kw=html.escape(str(keyword)) or "?", domain=domain_html,
        count=count, bar=bar,
        pct=int(min(100, (float(resonance_int) / max_resonance * 100))) if max_resonance > 0 else 0,
        text=highlighted,
        ep=_fmt(epsilon_peak), ri=_fmt(resonance_int), cnt=count, cf=_fmt(confidence),
        total=len(positions), pos=html.escape(pos_str),
    )


def _render_full_table(nav: dict) -> str:
    """Collapsible table with all keywords, sorted by count."""
    if not nav:
        return ""
    sorted_words = sorted(nav.items(), key=lambda kv: -(kv[1].get("count", 0) if isinstance(kv[1], dict) else 0))
    rows = []
    for kw, info in sorted_words:
        if not isinstance(info, dict):
            continue
        rows.append(
            '<tr><td><strong>{kw}</strong></td><td>{cnt}</td><td>{peak}</td><td>{res}</td><td>{dom}</td></tr>'.format(
                kw=html.escape(str(kw)),
                cnt=info.get("count", "—"),
                peak=_fmt(info.get("peak_epsilon")),
                res=_fmt(info.get("total_resonance")),
                dom=html.escape(str(info.get("domain", "—"))),
            )
        )
    if not rows:
        return ""
    return (
        '<details class="table-wrap"><summary>📋 Все ключевые слова ({n}) — таблица</summary>'
        '<table><tr><th>слово</th><th>найдено</th><th>ε peak</th><th>R ∫</th><th>домен</th></tr>'
        '{rows}</table></details>'
    ).format(n=len(sorted_words), rows="".join(rows))


def _render_diagnostics(analysis: dict) -> str:
    """Code diagnostics block (python/code mode)."""
    if analysis.get("mode") == "text":
        return ""
    diagnostics = analysis.get("code_diagnostics") or {}
    if not diagnostics:
        return ""
    parts = ['<div class="summary-box"><h3 style="margin:0 0 8px;">Проверка кода</h3>']
    parts.append(
        '<div class="summary-line" style="font-size:13px;">{mode} · {interp} · <strong style="color:{color}">{status}</strong></div>'.format(
            mode=html.escape(analysis.get("mode", "unknown")),
            interp=html.escape(str(diagnostics.get("interpreter") or "не найден")),
            status=html.escape(str(diagnostics.get("status", "unknown"))),
            color="var(--accent)" if diagnostics.get("status") == "ok" else "var(--warn)",
        )
    )
    if diagnostics.get("message"):
        parts.append('<pre style="background:var(--bg);padding:8px;border-radius:4px;font-size:12px;white-space:pre-wrap;margin-top:8px;">{}</pre>'.format(
            html.escape(str(diagnostics["message"]))
        ))
    if diagnostics.get("issues"):
        for issue in diagnostics["issues"]:
            parts.append('<pre style="background:#2d1518;padding:8px;border-radius:4px;font-size:12px;white-space:pre-wrap;margin-top:6px;">{}</pre>'.format(
                html.escape(json.dumps(issue, ensure_ascii=False, default=str, indent=2))
            ))
    parts.append('</div>')
    return "".join(parts)


def _render_result(analysis: dict, full_text: str = "") -> str:
    """Render the full analysis result — never raises on missing keys."""
    if not analysis:
        return ""

    if analysis.get("error"):
        return '<div class="error-box">⚠ Ошибка анализа: {}</div>'.format(html.escape(str(analysis["error"])))

    # Get the actual veins from poler_v3 (the "selected" in analysis is the same list)
    poler_v3 = analysis.get("poler_v3") or {}
    if not isinstance(poler_v3, dict):
        poler_v3 = {}
    veins = poler_v3.get("veins") or analysis.get("selected") or []
    nav = poler_v3.get("navigation_map") or {}

    # Find max resonance for bar normalization
    max_resonance = 0.0
    for v in veins:
        try:
            r = float(v.get("resonance_integral", 0) or 0)
            if r > max_resonance:
                max_resonance = r
        except (TypeError, ValueError):
            pass

    summary_html = _render_summary(analysis)
    diagnostics_html = _render_diagnostics(analysis)
    table_html = _render_full_table(nav)

    if not veins:
        fragments_html = '<div class="empty">Ничего не найдено. Попробуйте изменить запрос или текст.</div>'
    else:
        fragments_html = "".join(_render_fragment(i, v, full_text, max_resonance) for i, v in enumerate(veins[:10]))

    return (
        '<div class="result">'
        '<h2>Результат анализа</h2>'
        '{summary}'
        '{diag}'
        '<h3>🔍 Топ-{n} фрагментов</h3>'
        '{fragments}'
        '{table}'
        '</div>'
    ).format(summary=summary_html, diag=diagnostics_html, n=min(10, len(veins)), fragments=fragments_html, table=table_html)


# ============================================================================
#  Smart-анализ — render the SmartInterpreter report
# ============================================================================

_SEV_COLORS = {
    "CRITICAL": "#f4b8bf",
    "HIGH":     "#f4d4a0",
    "MEDIUM":   "#f5e89c",
    "LOW":      "#c5e8b8",
}
_SEV_BG = {
    "CRITICAL": "#3a1820",
    "HIGH":     "#3a2c18",
    "MEDIUM":   "#363318",
    "LOW":      "#1e3a26",
}


def _render_smart_summary(summary: dict) -> str:
    """Top summary box: total + by severity."""
    total = summary.get("total", 0)
    by_sev = summary.get("by_severity", {}) or {}
    chips = "".join(
        '<span class="word-chip" style="background:{bg};color:{fg};border-color:{fg};">{sev}<span class="num">×{cnt}</span></span>'.format(
            sev=sev, cnt=by_sev.get(sev, 0),
            bg=_SEV_BG.get(sev, "var(--bg)"),
            fg=_SEV_COLORS.get(sev, "var(--text)"),
        )
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )
    return (
        '<div class="summary-box">'
        '<div class="summary-line">⚡ Smart-анализ: найдено <strong>{total}</strong> нарушений</div>'
        '<div class="top-words">{chips}</div>'
        '<div class="summary-line" style="color:var(--muted);font-size:12px;margin-top:8px;">'
        'Без LLM. AST-walk + правила + POLER для контекста. Сортировка по severity.</div>'
        '</div>'
    ).format(total=total, chips=chips)


def _render_smart_violation(index: int, v: dict) -> str:
    """Render one smart-analysis violation as a fragment card."""
    sev = v.get("severity", "LOW")
    sev_color = _SEV_COLORS.get(sev, "var(--text)")
    sev_bg = _SEV_BG.get(sev, "var(--bg)")
    rule_id = html.escape(str(v.get("rule_id", "?")))
    line = v.get("line", "?")
    col = v.get("col", "?")
    why = html.escape(str(v.get("why", "")))
    fix = html.escape(str(v.get("fix", "")))
    manual = html.escape(str(v.get("manual_review", "")))
    snippet = html.escape(str(v.get("source_snippet", "")))
    node_type = html.escape(str(v.get("ast_node_type", "?")))
    poler = v.get("poler") or {}
    poler_html = ""
    if poler:
        poler_html = (
            '<div class="metric"><div class="metric-label">POLER count</div>'
            '<div class="metric-value blue">{cnt}</div></div>'
            '<div class="metric"><div class="metric-label">R (resonance)</div>'
            '<div class="metric-value green">{r}</div></div>'
            '<div class="metric"><div class="metric-label">domain</div>'
            '<div class="metric-value">{dom}</div></div>'
        ).format(
            cnt=poler.get("total_count", 0),
            r=_fmt(poler.get("total_resonance")),
            dom=html.escape(str(poler.get("domain", "general"))),
        )
        if poler.get("best_vein_fragment"):
            poler_html += (
                '<details class="collapsible"><summary>POLER fragment</summary>'
                '<pre style="font-size:11px;">{frag}</pre></details>'
            ).format(frag=html.escape(str(poler["best_vein_fragment"])[:300]))

    return (
        '<div class="fragment" style="border-left:3px solid {sev_color};">'
        '<div class="fragment-header">'
        '<div><span class="fragment-num">#{n}</span> '
        '<span class="fragment-kw" style="background:{sev_bg};color:{sev_color};">{sev}</span> '
        '<span style="color:var(--muted);font-size:12px;">{rule}</span></div>'
        '<div class="fragment-meta">line {line} · col {col} · {node}</div>'
        '</div>'
        '<div style="color:var(--text);font-size:13px;margin-bottom:6px;">{why}</div>'
        '<div class="fragment-text">{snippet}</div>'
        '<div style="margin-top:8px;"><strong style="color:var(--accent);font-size:12px;">FIX:</strong>'
        '<pre style="background:var(--bg);padding:8px;border-radius:4px;font-size:12px;margin:4px 0;white-space:pre-wrap;">{fix}</pre></div>'
        '<div style="margin-top:4px;"><strong style="color:var(--muted);font-size:11px;">Manual review:</strong> '
        '<span style="color:var(--muted);font-size:12px;">{manual}</span></div>'
        '<div class="metrics">{poler}</div>'
        '</div>'
    ).format(
        n=index + 1, sev_color=sev_color, sev_bg=sev_bg, sev=html.escape(sev),
        rule=rule_id, line=line, col=col, node=node_type,
        why=why, snippet=snippet, fix=fix, manual=manual, poler=poler_html,
    )


def _render_smart_result(report: dict) -> str:
    """Render the full smart-analysis report."""
    if not report:
        return '<div class="error-box">⚠ Пустой отчёт smart-анализа</div>'

    if not report.get("syntax_ok"):
        err = report.get("syntax_error") or {}
        return (
            '<div class="result"><h2>Smart-анализ</h2>'
            '<div class="error-box">⚠ Синтаксическая ошибка — AST не разобран.'
            ' line {line}, col {col}: {msg}<br><br>Текст строки:<br>{text}</div>'
            '<div class="summary-line" style="color:var(--muted);font-size:13px;">'
            'Сначала исправьте синтаксис, потом smart-анализ сможет найти нарушения.</div>'
            '</div>'
        ).format(
            line=err.get("line", "?"),
            col=err.get("col", "?"),
            msg=html.escape(str(err.get("message", ""))),
            text=html.escape(str(err.get("text", ""))),
        )

    summary = report.get("summary", {}) or {}
    violations = report.get("violations", []) or []

    # Sort: CRITICAL → HIGH → MEDIUM → LOW, then by line
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    violations = sorted(violations, key=lambda v: (
        sev_order.get(v.get("severity", "LOW"), 9),
        v.get("line", 0),
    ))

    summary_html = _render_smart_summary(summary)

    if not violations:
        fragments_html = (
            '<div class="empty" style="background:var(--panel);padding:30px;border-radius:8px;">'
            '✅ Нарушений не найдено. Код выглядит чисто по известным правилам.</div>'
        )
    else:
        # Show top 30 (was 10 — smart analysis usually has many findings)
        fragments_html = "".join(
            _render_smart_violation(i, v) for i, v in enumerate(violations[:30])
        )
        if len(violations) > 30:
            fragments_html += (
                '<div class="empty" style="background:var(--panel);padding:14px;border-radius:8px;">'
                '… и ещё {n} нарушений ниже. См. JSON / консоль для полного списка.</div>'
            ).format(n=len(violations) - 30)

    # By-rule table
    by_rule = summary.get("by_rule", {}) or {}
    rule_rows = "".join(
        '<tr><td><strong>{rid}</strong></td><td>{cnt}</td></tr>'.format(
            rid=html.escape(rid), cnt=cnt
        )
        for rid, cnt in sorted(by_rule.items())
    )
    table_html = ""
    if rule_rows:
        table_html = (
            '<details class="table-wrap"><summary>📋 Все правила ({n})</summary>'
            '<table><tr><th>правило</th><th>нарушений</th></tr>{rows}</table>'
            '</details>'
        ).format(n=len(by_rule), rows=rule_rows)

    return (
        '<div class="result">'
        '<h2>⚡ Smart-анализ Python</h2>'
        '{summary}'
        '<h3>🔍 Топ-{n} нарушений (отсортировано по критичности)</h3>'
        '{fragments}'
        '{table}'
        '</div>'
    ).format(
        summary=summary_html,
        n=min(30, len(violations)),
        fragments=fragments_html,
        table=table_html,
    )


# ============================================================================
#  HTTP server
# ============================================================================

def make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            self._send(_build_page(query="", source="", text="", result="", mode="analyze"))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            values = parse_qs(self.rfile.read(length).decode("utf-8"))
            query = values.get("query", [""])[0]
            source = values.get("source", [""])[0]
            text = values.get("text", [""])[0]
            # mode может прийти дважды (radio + button value) — берём smart если есть
            modes = values.get("mode", [])
            mode = "smart" if "smart" in modes else "analyze"

            if mode == "smart" and text:
                # Smart-анализ: AST + правила + POLER (без LLM)
                report = smart_analyze_code(text, filename=source or "smart-input.py")
                result_html = _render_smart_result(report)
            else:
                analysis = PolerEdit(text=text, query=query, source=source or "poler-edit-ui").analyze()
                result_html = _render_result(analysis, full_text=text)

            self._send(_build_page(
                query=html.escape(query),
                source=html.escape(source),
                text=html.escape(text),
                result=result_html,
                mode=mode,
            ))

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the local PolerEdit web interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler())
    url = f"http://{args.host}:{args.port}"
    print(f"PolerEdit запущен: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPolerEdit остановлен")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
