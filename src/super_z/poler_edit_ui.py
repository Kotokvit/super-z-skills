from __future__ import annotations

import argparse
import html
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .poler_edit import PolerEdit


_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolerEdit</title>
<style>
:root {{ color-scheme: dark; --bg:#101418; --panel:#182027; --line:#31404a; --text:#e8eef0; --muted:#93a3aa; --accent:#62d5b0; --accent2:#7ab8ff; --warn:#f4b8bf; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:linear-gradient(135deg,#101418,#1d2930); color:var(--text); font:16px/1.5 system-ui,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:36px 20px 60px; }} h1 {{ margin:0 0 8px; font-size:38px; }} p {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:24px; }} section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; }}
label {{ display:block; color:var(--muted); margin:0 0 8px; }} textarea {{ width:100%; min-height:220px; resize:vertical; background:#0d1216; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:12px; font:inherit; }}
input {{ width:100%; background:#0d1216; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:11px; font:inherit; }} button {{ margin-top:14px; background:var(--accent); color:#08251d; border:0; border-radius:6px; padding:11px 18px; font-weight:700; cursor:pointer; }}
.result {{ margin-top:16px; }} .summary {{ color:var(--accent); white-space:pre-wrap; padding:8px 0; }} .fragment {{ padding:14px 0; border-top:1px solid var(--line); }} .meta {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.vein-text {{ white-space:pre-wrap; padding:6px 10px; background:#0d1216; border-left:3px solid var(--accent); border-radius:0 4px 4px 0; }}
.vein-tags {{ margin-top:6px; }}
.vein-tag {{ display:inline-block; background:#0d1216; border:1px solid var(--line); border-radius:4px; padding:2px 8px; margin-right:6px; font-size:12px; color:var(--muted); }}
.vein-tag.kw {{ color:var(--accent2); border-color:#2a4d6b; }}
.vein-tag.domain {{ color:var(--accent); border-color:#2a6b56; }}
.vein-json {{ margin-top:8px; background:#0d1216; padding:8px; border-radius:4px; font-size:12px; max-height:240px; overflow:auto; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; margin:12px 0; }}
.stat {{ background:#0d1216; padding:8px 12px; border-radius:4px; border:1px solid var(--line); }}
.stat-label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.5px; }}
.stat-value {{ color:var(--accent); font-size:18px; font-weight:bold; margin-top:2px; }}
.score-row {{ display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-top:6px; }}
.score-cell {{ background:#0d1216; padding:4px 8px; border-radius:4px; font-size:12px; }}
.score-label {{ color:var(--muted); font-size:10px; text-transform:uppercase; }}
.score-num {{ color:var(--accent2); font-weight:bold; }}
.error-box {{ background:#2d1518; border:1px solid #6b2c33; color:var(--warn); padding:12px; border-radius:6px; margin-top:12px; white-space:pre-wrap; }}
.positions {{ color:var(--muted); font-size:11px; margin-top:2px; }}
@media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} h1 {{ font-size:30px; }} .score-row {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body><main>
<h1>PolerEdit</h1><p>Локальний аналіз тексту через epsilon та resonance (POLER v3.0).</p>
<form method="post"><div class="grid">
<section><label for="query">Запит (ключове слово)</label><input id="query" name="query" value="{query}" placeholder="Наприклад: ИИ, ризик, алгоритм"></section>
<section><label for="source">Файл / мова (необов'язково)</label><input id="source" name="source" value="{source}" placeholder="Наприклад: example.py або article.txt"><label for="text">Текст</label><textarea id="text" name="text" placeholder="Вставте текст для аналізу...">{text}</textarea></section>
</div><button type="submit">Проаналізувати</button></form>
{result}
</main></body></html>"""


def _fmt(value, default: str = "—") -> str:
    """Format a numeric score; return default for None/missing/invalid."""
    if value is None:
        return default
    try:
        f = float(value)
        if abs(f) >= 1000:
            return "{:.1f}".format(f)
        return "{:.3f}".format(f)
    except (TypeError, ValueError):
        return str(value) if value != "" else default


def _vein_text(vein: dict) -> str:
    """Extract display text from a POLER v3.0 vein using the real field names."""
    return (
        vein.get("top_fragment")
        or vein.get("fragment")
        or vein.get("cleaned_text")
        or vein.get("raw_text")
        or vein.get("text")
        or vein.get("snippet")
        or ""
    )


def _render_vein(index: int, vein: dict) -> str:
    """Render one vein — fully defensive against missing keys."""
    text = _vein_text(vein)
    keyword = vein.get("keyword") or ""
    domain = vein.get("domain") or ""
    epsilon_peak = _fmt(vein.get("epsilon_peak"))
    resonance_int = _fmt(vein.get("resonance_integral"))
    confidence = _fmt(vein.get("confidence"))
    positions = vein.get("positions") or []

    # Tags
    tags = []
    if keyword:
        tags.append('<span class="vein-tag kw">keyword: {}</span>'.format(html.escape(str(keyword))))
    if domain:
        tags.append('<span class="vein-tag domain">domain: {}</span>'.format(html.escape(str(domain))))
    tags_html = '<div class="vein-tags">{}</div>'.format("".join(tags)) if tags else ""

    # Score row — 4 main metrics
    score_row = (
        '<div class="score-row">'
        '<div class="score-cell"><div class="score-label">epsilon peak</div><div class="score-num">{ep}</div></div>'
        '<div class="score-cell"><div class="score-label">resonance ∫</div><div class="score-num">{ri}</div></div>'
        '<div class="score-cell"><div class="score-label">confidence</div><div class="score-num">{cf}</div></div>'
        '<div class="score-cell"><div class="score-label">positions</div><div class="score-num">{pn}</div></div>'
        '</div>'
    ).format(ep=epsilon_peak, ri=resonance_int, cf=confidence, pn=len(positions) if positions else 0)

    # Positions (char offsets in source text)
    positions_html = ""
    if positions:
        pos_str = ", ".join(str(p) for p in positions[:10])
        if len(positions) > 10:
            pos_str += " +{} more".format(len(positions) - 10)
        positions_html = '<div class="positions">positions: [{}]</div>'.format(html.escape(pos_str))

    # Extra fields (debug, collapsible)
    known_keys = {"top_fragment", "fragment", "cleaned_text", "raw_text", "text", "snippet",
                  "keyword", "domain", "epsilon_peak", "resonance_integral", "confidence",
                  "positions", "source_file"}
    extra = {k: v for k, v in vein.items() if k not in known_keys}
    extra_html = ""
    if extra:
        extra_json = json.dumps(extra, ensure_ascii=False, default=str, indent=2)
        extra_html = '<details class="vein-json"><summary>extra fields ({})</summary><pre>{}</pre></details>'.format(
            len(extra), html.escape(extra_json)
        )

    text_html = html.escape(text[:1000]) if text else '<em style="color:var(--muted)">(no fragment text)</em>'

    return (
        '<div class="fragment">'
        '<div class="vein-text">#{n} · {txt}</div>'
        '{tags}'
        '{scores}'
        '{positions}'
        '{extra}'
        '</div>'
    ).format(n=index + 1, txt=text_html, tags=tags_html, scores=score_row, positions=positions_html, extra=extra_html)


def _render_stats(analysis: dict) -> str:
    """Render POLER v3.0 stats block."""
    poler_v3 = analysis.get("poler_v3") or {}
    if not isinstance(poler_v3, dict):
        return ""
    stats = poler_v3.get("stats") or {}
    if not isinstance(stats, dict) or not stats:
        return ""

    # Map stats keys to labels
    label_map = {
        "total_keywords": "keywords",
        "total_veins": "veins",
        "total_positions": "positions",
        "density": "density",
        "avg_resonance": "avg resonance",
        "avg_epsilon": "avg epsilon",
        "selected": "selected",
    }
    cells = []
    for key, label in label_map.items():
        if key in stats:
            cells.append(
                '<div class="stat"><div class="stat-label">{}</div><div class="stat-value">{}</div></div>'.format(
                    html.escape(label), html.escape(str(stats[key]))
                )
            )
    if not cells:
        return ""
    return '<div class="stats-grid">{}</div>'.format("".join(cells))


def _render_navigation(analysis: dict) -> str:
    """Render navigation map (keyword → positions/counts)."""
    poler_v3 = analysis.get("poler_v3") or {}
    if not isinstance(poler_v3, dict):
        return ""
    nav = poler_v3.get("navigation_map") or {}
    if not nav:
        return ""
    rows = []
    for kw, info in nav.items():
        if not isinstance(info, dict):
            continue
        rows.append(
            '<tr><td><strong>{kw}</strong></td><td>{cnt}</td><td>{peak}</td><td>{avg}</td><td>{res}</td><td>{dom}</td></tr>'.format(
                kw=html.escape(str(kw)),
                cnt=info.get("count", "—"),
                peak=_fmt(info.get("peak_epsilon")),
                avg=_fmt(info.get("avg_epsilon")),
                res=_fmt(info.get("total_resonance")),
                dom=html.escape(str(info.get("domain", "—"))),
            )
        )
    if not rows:
        return ""
    return (
        '<div style="margin:12px 0; overflow-x:auto;">'
        '<table border=1 cellpadding=6 style="border-collapse:collapse; width:100%; font-size:13px;">'
        '<tr style="color:var(--muted);"><th>keyword</th><th>count</th><th>peak ε</th><th>avg ε</th><th>resonance ∫</th><th>domain</th></tr>'
        '{rows}'
        '</table></div>'
    ).format(rows="".join(rows))


def _render_diagnostics(analysis: dict) -> str:
    """Render code diagnostics block (only in code/python mode)."""
    diagnostics = analysis.get("code_diagnostics") or {}
    if analysis.get("mode") == "text":
        return ""
    if not diagnostics:
        return ""
    parts = [
        '<p class="meta">Режим: {} · Інтерпретатор: {} · Статус: {}</p>'.format(
            html.escape(analysis.get("mode", "unknown")),
            html.escape(str(diagnostics.get("interpreter") or "не знайдений")),
            html.escape(str(diagnostics.get("status", "unknown"))),
        )
    ]
    if diagnostics.get("message"):
        parts.append(
            '<div class="fragment"><strong>Діагностика</strong><pre style="white-space:pre-wrap; padding:8px; background:#0d1216; border-radius:4px;">{}</pre></div>'.format(
                html.escape(str(diagnostics["message"]))
            )
        )
    if diagnostics.get("issues"):
        for issue in diagnostics["issues"]:
            parts.append(
                '<div class="fragment"><strong>Issue</strong><pre style="white-space:pre-wrap; padding:8px; background:#0d1216; border-radius:4px;">{}</pre></div>'.format(
                    html.escape(json.dumps(issue, ensure_ascii=False, default=str, indent=2))
                )
            )
    return "".join(parts)


def _render_result(analysis: dict) -> str:
    """Render the full analysis result — never raises on missing keys."""
    if not analysis:
        return ""

    if analysis.get("error"):
        return '<section class="result"><h2>Результат</h2><div class="error-box">{}</div></section>'.format(
            html.escape(str(analysis["error"]))
        )

    selected = analysis.get("selected") or []
    fragments = "".join(_render_vein(i, v) for i, v in enumerate(selected))
    diagnostics_html = _render_diagnostics(analysis)
    stats_html = _render_stats(analysis)
    nav_html = _render_navigation(analysis)
    summary = analysis.get("summary") or ""
    mode = analysis.get("mode", "text")

    return (
        '<section class="result">'
        '<h2>Результат (POLER v3.0 · mode: {mode})</h2>'
        '{diag}'
        '{stats}'
        '{nav}'
        '<div class="summary">{summary}</div>'
        '{fragments}'
        '</section>'
    ).format(mode=html.escape(mode), diag=diagnostics_html, stats=stats_html, nav=nav_html,
             summary=html.escape(summary), fragments=fragments)


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
            self._send(_PAGE.format(query="", source="", text="", result=""))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            values = parse_qs(self.rfile.read(length).decode("utf-8"))
            query = values.get("query", [""])[0]
            source = values.get("source", [""])[0]
            text = values.get("text", [""])[0]
            analysis = PolerEdit(text=text, query=query, source=source or "poler-edit-ui").analyze()
            self._send(_PAGE.format(
                query=html.escape(query),
                source=html.escape(source),
                text=html.escape(text),
                result=_render_result(analysis),
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
