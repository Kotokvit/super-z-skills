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
:root {{ color-scheme: dark; --bg:#101418; --panel:#182027; --line:#31404a; --text:#e8eef0; --muted:#93a3aa; --accent:#62d5b0; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:linear-gradient(135deg,#101418,#1d2930); color:var(--text); font:16px/1.5 system-ui,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:36px 20px 60px; }} h1 {{ margin:0 0 8px; font-size:38px; }} p {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:24px; }} section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; }}
label {{ display:block; color:var(--muted); margin:0 0 8px; }} textarea {{ width:100%; min-height:220px; resize:vertical; background:#0d1216; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:12px; font:inherit; }}
input {{ width:100%; background:#0d1216; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:11px; font:inherit; }} button {{ margin-top:14px; background:var(--accent); color:#08251d; border:0; border-radius:6px; padding:11px 18px; font-weight:700; cursor:pointer; }}
.result {{ margin-top:16px; }} .summary {{ color:var(--accent); white-space:pre-wrap; }} .fragment {{ padding:12px 0; border-top:1px solid var(--line); }} .meta {{ color:var(--muted); font-size:13px; }}
.vein-text {{ white-space:pre-wrap; }}
.vein-tags {{ margin-top:4px; }}
.vein-tag {{ display:inline-block; background:#0d1216; border:1px solid var(--line); border-radius:4px; padding:2px 8px; margin-right:6px; font-size:12px; color:var(--muted); }}
.vein-json {{ margin-top:8px; background:#0d1216; padding:8px; border-radius:4px; font-size:12px; max-height:200px; overflow:auto; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; margin:8px 0; }}
.stat {{ background:#0d1216; padding:8px; border-radius:4px; }}
.stat-label {{ color:var(--muted); font-size:11px; text-transform:uppercase; }}
.stat-value {{ color:var(--accent); font-size:18px; font-weight:bold; }}
.error-box {{ background:#2d1518; border:1px solid #6b2c33; color:#f4b8bf; padding:12px; border-radius:6px; margin-top:12px; }}
@media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} h1 {{ font-size:30px; }} }}
</style>
</head>
<body><main>
<h1>PolerEdit</h1><p>Локальний аналіз тексту через epsilon та resonance (POLER v3.0).</p>
<form method="post"><div class="grid">
<section><label for="query">Запит</label><input id="query" name="query" value="{query}" placeholder="Наприклад: знайди головний ризик"></section>
<section><label for="source">Файл / мова (необов'язково)</label><input id="source" name="source" value="{source}" placeholder="Наприклад: example.py або article.txt"><label for="text">Текст</label><textarea id="text" name="text" placeholder="Вставте текст для аналізу...">{text}</textarea></section>
</div><button type="submit">Проаналізувати</button></form>
{result}
</main></body></html>"""


def _vein_text(vein: dict) -> str:
    """Extract display text from a vein using the same fallback chain as poler_edit.py."""
    return (
        vein.get("cleaned_text")
        or vein.get("raw_text")
        or vein.get("text")
        or vein.get("snippet")
        or ""
    )


def _format_score(value, default: str = "—") -> str:
    """Format a numeric score, returning default for None/missing."""
    if value is None:
        return default
    try:
        return "{:.3f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _render_vein(index: int, vein: dict) -> str:
    """Render a single vein as HTML — fully defensive against missing keys."""
    text = _vein_text(vein)
    resonance = _format_score(vein.get("resonance"))
    epsilon = _format_score(vein.get("epsilon"))
    weight = _format_score(vein.get("weight"))
    score = _format_score(vein.get("score"))

    tags = []
    if vein.get("type"):
        tags.append('<span class="vein-tag">type: {}</span>'.format(html.escape(str(vein["type"]))))
    if vein.get("theme"):
        tags.append('<span class="vein-tag">theme: {}</span>'.format(html.escape(str(vein["theme"]))))
    if vein.get("keywords"):
        kw = vein["keywords"]
        if isinstance(kw, list):
            kw_str = ", ".join(str(k) for k in kw[:5])
        else:
            kw_str = str(kw)
        tags.append('<span class="vein-tag">kw: {}</span>'.format(html.escape(kw_str[:120])))
    tags_html = '<div class="vein-tags">{}</div>'.format("".join(tags)) if tags else ""

    # Show extra vein fields as a collapsible JSON for debugging/transparency
    extra_keys = {k: v for k, v in vein.items()
                  if k not in {"cleaned_text", "raw_text", "text", "snippet",
                               "resonance", "epsilon", "weight", "score",
                               "type", "theme", "keywords"}}
    extra_html = ""
    if extra_keys:
        extra_json = json.dumps(extra_keys, ensure_ascii=False, default=str, indent=2)
        extra_html = '<details class="vein-json"><summary>extra fields ({})</summary><pre>{}</pre></details>'.format(
            len(extra_keys), html.escape(extra_json)
        )

    return (
        '<div class="fragment">'
        '<div class="vein-text">{}</div>'
        '<div class="meta">resonance: {} · epsilon: {} · weight: {} · score: {}</div>'
        '{}{}'
        '</div>'
    ).format(
        html.escape(text[:600]),
        resonance, epsilon, weight, score,
        tags_html, extra_html,
    )


def _render_stats(analysis: dict) -> str:
    """Render POLER v3.0 stats block if present."""
    poler_v3 = analysis.get("poler_v3") or {}
    stats = poler_v3.get("stats") if isinstance(poler_v3, dict) else None
    if not isinstance(stats, dict) or not stats:
        return ""

    cells = []
    for key in ("total_veins", "selected", "density", "avg_resonance", "avg_epsilon"):
        if key in stats:
            cells.append(
                '<div class="stat"><div class="stat-label">{}</div><div class="stat-value">{}</div></div>'.format(
                    html.escape(key.replace("_", " ")),
                    html.escape(str(stats[key])),
                )
            )
    if not cells:
        return ""
    return '<div class="stats-grid">{}</div>'.format("".join(cells))


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
            '<div class="fragment"><strong>Діагностика</strong><pre>{}</pre></div>'.format(
                html.escape(str(diagnostics["message"]))
            )
        )
    if diagnostics.get("issues"):
        for issue in diagnostics["issues"]:
            parts.append(
                '<div class="fragment"><strong>Issue</strong><pre>{}</pre></div>'.format(
                    html.escape(json.dumps(issue, ensure_ascii=False, default=str, indent=2))
                )
            )
    return "".join(parts)


def _render_result(analysis: dict) -> str:
    """Render the full analysis result — never raises on missing keys."""
    if not analysis:
        return ""

    # POLER v3.0 error path
    if analysis.get("error"):
        return '<section class="result"><h2>Результат</h2><div class="error-box">{}</div></section>'.format(
            html.escape(str(analysis["error"]))
        )

    selected = analysis.get("selected") or []
    fragments = "".join(_render_vein(i, v) for i, v in enumerate(selected))
    diagnostics_html = _render_diagnostics(analysis)
    stats_html = _render_stats(analysis)
    summary = analysis.get("summary") or ""

    return (
        '<section class="result">'
        '<h2>Результат (POLER v3.0)</h2>'
        '{}'
        '{}'
        '<div class="summary">{}</div>'
        '{}'
        '</section>'
    ).format(diagnostics_html, stats_html, html.escape(summary), fragments)


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
