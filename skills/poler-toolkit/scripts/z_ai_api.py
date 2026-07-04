"""
z_ai_api — тонкая обёртка над z-ai-web-dev-sdk из Python.

Использует тот же config (/etc/.z-ai-config) что и Node SDK.
Zero dependencies — только urllib из stdlib.

Две функции:
  - web_search(query, num=10) → list of {url, name, snippet, host_name, rank, date, favicon}
  - page_reader(url)          → {code, status, data: {html, title, url, publishedTime?}}

Плюс:
  - html_to_text(html) — чистит HTML до текста
  - invoke(function_name, args) — прямой вызов любой функции
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# ============================================================
# Config
# ============================================================
def load_zai_config() -> Dict[str, str]:
    """
    Загружает конфиг z-ai.
    Ищет в порядке:
      1. ./.z-ai-config (cwd)
      2. ~/.z-ai-config
      3. /etc/.z-ai-config
    """
    candidates = [
        os.path.join(os.getcwd(), ".z-ai-config"),
        os.path.join(os.path.expanduser("~"), ".z-ai-config"),
        "/etc/.z-ai-config",
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("baseUrl") and cfg.get("apiKey"):
                return cfg
    raise FileNotFoundError(
        "z-ai config not found in: " + ", ".join(candidates)
    )


# ============================================================
# Invoke
# ============================================================
def invoke(function_name: str, arguments: Dict[str, Any],
           timeout: int = 30) -> Any:
    """
    Универсальный вызов функции z-ai.

    POST {baseUrl}/functions/invoke
    Body: {"function_name": ..., "arguments": ...}
    Headers: Authorization: Bearer {apiKey}, X-Z-AI-From: Z

    Возвращает содержимое поля `result` (сервер оборачивает в {result: ...}).
    """
    cfg = load_zai_config()
    base = cfg["baseUrl"].rstrip("/")
    url = f"{base}/functions/invoke"
    body = json.dumps({
        "function_name": function_name,
        "arguments": arguments,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['apiKey']}",
        "X-Z-AI-From": "Z",
    }
    # Token + chatId из конфига — сервер требует X-Token header
    if cfg.get("token"):
        headers["X-Token"] = cfg["token"]
    if cfg.get("chatId"):
        headers["X-Chat-Id"] = cfg["chatId"]
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw)
        # Сервер возвращает {result: ...} — разворачиваем
        return data.get("result", data)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"z-ai HTTP {e.code} for {function_name}: {err_body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"z-ai URL error for {function_name}: {e}") from e


# ============================================================
# web_search
# ============================================================
def web_search(query: str, num: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск через z-ai web_search.

    Returns: list of items:
      {url, name, snippet, host_name, rank, date, favicon}
    """
    result = invoke("web_search", {"query": query, "num": num})
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    # Unknown shape — возвращаем как есть в списке
    return [result] if result else []


# ============================================================
# page_reader
# ============================================================
def page_reader(url: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Читает URL через z-ai page_reader. Возвращает HTML.

    Returns: {code, status, data: {html, title, url, publishedTime?}}
    """
    return invoke("page_reader", {"url": url}, timeout=timeout)


# ============================================================
# HTML → text
# ============================================================
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
_NUMERIC_ENTITY_RE = re.compile(r"&#(\d+);")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """
    Чистит HTML до plain text.
    Удаляет <script>, <style>, теги, сущности, схлопывает whitespace.
    """
    if not html:
        return ""
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    # Декодируем numeric entities (&#39; → ')
    text = _NUMERIC_ENTITY_RE.sub(
        lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x10000 else " ",
        text,
    )
    text = _ENTITY_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


# ============================================================
# Convenience
# ============================================================
def fetch_text(url: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Скачивает URL через page_reader, чистит HTML → text.
    Returns: {url, title, text, published_time, char_count, word_count}
    """
    raw = page_reader(url, timeout=timeout)
    data = raw.get("data", {}) if isinstance(raw, dict) else {}
    html = data.get("html", "")
    text = html_to_text(html)
    title = data.get("title", "")
    pub = data.get("publishedTime") or data.get("published_time")
    return {
        "url": url,
        "title": title,
        "text": text,
        "published_time": pub,
        "char_count": len(text),
        "word_count": len(text.split()) if text else 0,
    }


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python z_ai_api.py search 'query' [num]")
        print("  python z_ai_api.py fetch 'url'")
        print("  python z_ai_api.py text  'url'   # fetch + html_to_text")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: search 'query' [num]")
            sys.exit(1)
        q = sys.argv[2]
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        results = web_search(q, num=n)
        print(f"=== {len(results)} results for '{q}' ===\n")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r.get('name', '?')}")
            print(f"   URL:     {r.get('url', '')}")
            print(f"   Host:    {r.get('host_name', '')}")
            print(f"   Snippet: {r.get('snippet', '')[:200]}")
            if r.get("date"):
                print(f"   Date:    {r['date']}")
            print()
    elif cmd == "fetch":
        if len(sys.argv) < 3:
            print("Usage: fetch 'url'")
            sys.exit(1)
        url = sys.argv[2]
        result = page_reader(url)
        data = result.get("data", {}) if isinstance(result, dict) else {}
        print(f"=== {url} ===")
        print(f"Title: {data.get('title', '?')}")
        print(f"Published: {data.get('publishedTime', 'N/A')}")
        print(f"HTML length: {len(data.get('html', ''))} chars")
        print(f"Status: {result.get('status')}")
        print()
        print("--- First 500 chars of HTML ---")
        print(data.get("html", "")[:500])
    elif cmd == "text":
        if len(sys.argv) < 3:
            print("Usage: text 'url'")
            sys.exit(1)
        url = sys.argv[2]
        info = fetch_text(url)
        print(f"=== {url} ===")
        print(f"Title:      {info['title']}")
        print(f"Published:  {info['published_time'] or 'N/A'}")
        print(f"Chars:      {info['char_count']}")
        print(f"Words:      {info['word_count']}")
        print()
        print("--- First 1000 chars of text ---")
        print(info["text"][:1000])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
