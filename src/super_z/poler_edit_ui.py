from __future__ import annotations

import argparse
import html
import json
import os
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
@media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} h1 {{ font-size:30px; }} }}
</style>
</head>
<body><main>
<h1>PolerEdit</h1><p>Локальний аналіз тексту через epsilon та resonance.</p>
<form method="post"><div class="grid">
<section><label for="query">Запит</label><input id="query" name="query" value="{query}" placeholder="Наприклад: знайди головний ризик"></section>
<section><label for="source">Файл / мова (необов'язково)</label><input id="source" name="source" value="{source}" placeholder="Наприклад: example.py або article.txt"><label for="text">Текст</label><textarea id="text" name="text" placeholder="Вставте текст для аналізу...">{text}</textarea></section>
</div><button type="submit">Проаналізувати</button></form>
{result}
</main></body></html>"""


def _render_result(analysis: dict) -> str:
    if not analysis.get("selected"):
        return ""
    fragments = "".join(
        '<div class="fragment"><div>{}</div><div class="meta">resonance: {} · epsilon: {}</div></div>'.format(
            html.escape(item["text"]), item["resonance"], item["epsilon"]
        )
        for item in analysis["selected"]
    )
    diagnostics = analysis.get("code_diagnostics", {})
    diagnostic_text = ""
    if analysis.get("mode") != "text":
        diagnostic_text = '<p class="meta">Режим: {} · Інтерпретатор: {} · Статус: {}</p>'.format(
            html.escape(analysis.get("mode", "unknown")),
            html.escape(str(diagnostics.get("interpreter") or "не знайдений")),
            html.escape(diagnostics.get("status", "unknown")),
        )
        if diagnostics.get("message"):
            diagnostic_text += '<div class="fragment"><strong>Діагностика</strong><pre>{}</pre></div>'.format(html.escape(diagnostics["message"]))
    return '<section class="result"><h2>Результат</h2>{}<div class="summary">{}</div>{}</section>'.format(
        diagnostic_text, html.escape(analysis["summary"]), fragments
    )


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
            self._send(_PAGE.format(query=html.escape(query), source=html.escape(source), text=html.escape(text), result=_render_result(analysis)))

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
