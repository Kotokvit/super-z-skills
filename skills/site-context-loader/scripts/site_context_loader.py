#!/usr/bin/env python3
"""
site_context_loader.py — Online-layer geographic context loader.

Triggered by conversation-watcher when a toponym (place name) or geo-coordinate
appears in the user's message. Queries OpenStreetMap Nominatim API for
location metadata, returns a Pattern 1 source-grounded brief with claims.

Output includes an "assumption doc" — a one-paragraph text the agent can use
to confirm with the user: "I'm assuming you mean Kyiv, Ukraine (50.45, 30.52).
If you meant a different Kyiv, please specify."

CLI:
    python3 site_context_loader.py "Kyiv" --json
    python3 site_context_loader.py "50.45,30.52" --json
    echo "msg with toponym" | python3 site_context_loader.py - --json
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────

SKILL_DIR = Path(__file__).resolve().parent.parent

# Pattern 1 helper
_ORCH_SCRIPTS = SKILL_DIR.parent / "_orchestrator" / "scripts"
if str(_ORCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ORCH_SCRIPTS))
try:
    from patterns.source_grounded_brief import build_brief, Claim, validate_brief
    _HAS_PATTERN1 = True
except Exception as _e:
    sys.stderr.write(f"[site-context-loader] WARNING: source_grounded_brief unavailable: {_e}\n")
    _HAS_PATTERN1 = False

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "site-context-loader/1.0 (online-layer)"
TIMEOUT_SEC = 10

# Toponym detection patterns (Russian + English prepositions)
# Catches "возле Москвы", "near Paris", "под Киевом", etc.
TOPONYM_PATTERNS = [
    re.compile(
        r'\b(?:возле|около|под|вблизи|рядом\s+с|near|nearby|around|under)\s+'
        r'([А-ЯЁA-Z][а-яёa-z]+(?:[\s\-][А-ЯЁA-Z][а-яёa-z]+)?)',
        re.IGNORECASE,
    ),
    # Bare capitalized place name (caution: too aggressive if alone — must be combined with context)
    # We skip bare detection to avoid false positives.
]

GEOCOORD_PATTERN = re.compile(
    r'\b(-?\d{1,3}\.\d+),\s*(-?\d{1,3}\.\d+)\b'
)


# ─────────────────────────────────────────────────────────────────────
# Toponym lemmatization (Russian/Ukrainian → nominative case)
# ─────────────────────────────────────────────────────────────────────

# pymorphy3 for Russian morphology
try:
    sys.path.insert(0, str(Path.home() / ".local/lib/python3.13/site-packages"))
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
    _HAS_MORPH = True
except Exception:
    _MORPH = None
    _HAS_MORPH = False

# Fallback dictionary for common Ukrainian/Belarusian place names that
# pymorphy3 (Russian-focused) may not lemmatize correctly.
# Keys are common inflected forms; values are nominative forms OSM understands.
TOPONYM_FALLBACK_DICT = {
    # Ukrainian cities in inflected forms
    "львовом": "Львов", "львова": "Львов", "львову": "Львов", "львові": "Львов",
    "киевом": "Киев", "киева": "Киев", "киеву": "Киев",
    "києвом": "Київ", "києва": "Київ", "києву": "Київ", "києві": "Київ",
    "одессой": "Одесса", "одессы": "Одесса", "одессе": "Одесса",
    "харьковом": "Харьков", "харькова": "Харьков", "харькову": "Харьков",
    "днепром": "Днепр", "днепра": "Днепр", "днепру": "Днепр",
    "минском": "Минск", "минска": "Минск", "минску": "Минск",
    "варшавой": "Варшава", "варшавы": "Варшава", "варшаве": "Варшава",
    "прагой": "Прага", "праги": "Прага", "праге": "Прага",
    "берлином": "Берлин", "берлина": "Берлин", "берлину": "Берлин",
    "москвой": "Москва", "москвы": "Москва", "москве": "Москва",
    "петербургом": "Петербург", "петербурга": "Петербург", "петербургу": "Петербург",
    # Common short forms
    "лвовом": "Львов", "лвова": "Львов",
    "киеве": "Киев", "києві": "Київ",
    "львові": "Львів", "києві": "Київ",
}


def lemmatize_toponym(name: str) -> str:
    """Lemmatize a toponym to nominative case for OSM lookup.

    Handles Russian/Ukrainian declensions: "Львовом" → "Львов",
    "Киеве" → "Киев", "Москвой" → "Москва".

    Tries in order:
      1. pymorphy3 morphological analyzer (best for Russian)
      2. Fallback dictionary (for Ukrainian/Belarusian pymorphy3 misses)
      3. Original form (last resort — OSM may still match some inflected forms)
    """
    if not name:
        return name
    # Preserve original capitalization for display, but work on lowercase for matching
    low = name.lower().strip()

    # 1. pymorphy3 — best for Russian, also handles many Ukrainian forms
    if _HAS_MORPH:
        try:
            p = _MORPH.parse(name)
            if p:
                # Pick the most likely parse
                best = p[0]
                # If it's recognized as a geographical name (Geox tag) or noun,
                # use the normal form (nominative singular)
                if best.normal_form and best.normal_form.strip():
                    nom = best.normal_form.strip()
                    # Capitalize first letter to match OSM expectations
                    nom = nom[0].upper() + nom[1:]
                    return nom
        except Exception:
            pass

    # 2. Fallback dictionary
    if low in TOPONYM_FALLBACK_DICT:
        return TOPONYM_FALLBACK_DICT[low]

    # 3. Return original (OSM may still match)
    return name


# ─────────────────────────────────────────────────────────────────────
# Toponym extraction
# ─────────────────────────────────────────────────────────────────────

def extract_toponyms(text: str) -> List[str]:
    """Extract candidate toponyms from text."""
    found = []
    seen = set()
    for pat in TOPONYM_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1).strip()
            if name.lower() not in seen and len(name) >= 3:
                seen.add(name.lower())
                found.append(name)
    return found


def extract_coords(text: str) -> List[Tuple[str, str]]:
    """Extract (lat, lon) pairs from text."""
    return [(m.group(1), m.group(2)) for m in GEOCOORD_PATTERN.finditer(text)]


# ─────────────────────────────────────────────────────────────────────
# OSM Nominatim lookup
# ─────────────────────────────────────────────────────────────────────

def nominatim_search(query: str, lang_hint: str = "auto") -> List[Dict[str, Any]]:
    """Query OSM Nominatim. Returns list of result dicts."""
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "3",
        "addressdetails": "1",
    }
    if lang_hint and lang_hint != "auto":
        params["accept-language"] = lang_hint

    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, list):
            return data
    except Exception as e:
        sys.stderr.write(f"[site-context-loader] Nominatim error for {query!r}: {e}\n")
    return []


def nominatim_reverse(lat: str, lon: str, lang_hint: str = "auto") -> Optional[Dict[str, Any]]:
    """Reverse geocode lat,lon → address."""
    params = {
        "lat": lat, "lon": lon,
        "format": "jsonv2",
        "addressdetails": "1",
    }
    if lang_hint and lang_hint != "auto":
        params["accept-language"] = lang_hint
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write(f"[site-context-loader] Nominatim reverse error: {e}\n")
        return None


# ─────────────────────────────────────────────────────────────────────
# Assumption doc builder
# ─────────────────────────────────────────────────────────────────────

def build_assumption_doc(toponym: str, results: List[Dict[str, Any]]) -> str:
    """One-paragraph text the agent shows user to confirm location."""
    if not results:
        return f"Could not find any location named '{toponym}' on OpenStreetMap. Please specify the country or region."
    top = results[0]
    name = top.get("display_name", toponym)
    lat = top.get("lat", "?")
    lon = top.get("lon", "?")
    addr = top.get("address") or {}
    country = addr.get("country", "")
    region = addr.get("state") or addr.get("region") or ""
    kind = top.get("type") or top.get("class") or "place"

    alt_str = ""
    if len(results) > 1:
        alt_parts = []
        for r in results[1:3]:
            a = r.get("address") or {}
            alt_country = a.get("country", "?")
            alt_region = a.get("state") or a.get("region") or ""
            alt_parts.append(f"{alt_country}" + (f" ({alt_region})" if alt_region else ""))
        alt_str = f" Other matches: {', '.join(alt_parts)}."

    return (f"Assuming '{toponym}' means {name} ({kind}) at {lat}, {lon} — "
            f"{country}" + (f", {region}" if region else "") + f".{alt_str}"
            f" If you meant a different place, please specify.")


# ─────────────────────────────────────────────────────────────────────
# Main loader
# ─────────────────────────────────────────────────────────────────────

def _error_envelope(msg: str, confidence: float = 0.0) -> Dict[str, Any]:
    return {"status": "error", "confidence": confidence, "data": None, "error": msg}


def load(input_value: str, lang_hint: str = "auto",
         transient: bool = False) -> Dict[str, Any]:
    """Run site context loader pipeline."""
    t_start = time.time()
    toponyms: List[str] = []
    coords: List[Tuple[str, str]] = []

    if input_value == "-":
        try:
            text = sys.stdin.read()
        except Exception:
            text = ""
        toponyms = extract_toponyms(text)
        coords = extract_coords(text)
        if not toponyms and not coords:
            return _error_envelope("No toponyms or geo-coordinates found in stdin")
    elif GEOCOORD_PATTERN.match(input_value.strip()):
        # Input is coords
        m = GEOCOORD_PATTERN.match(input_value.strip())
        coords = [(m.group(1), m.group(2))]
    else:
        # First try to extract toponyms from the input (handles "около Киева" etc.)
        extracted = extract_toponyms(input_value)
        if extracted:
            toponyms = extracted
        else:
            # No preposition match — treat input as a direct toponym name
            toponyms = [input_value.strip().strip('"\'')]
            # Also try coords in case input had them
            coords = extract_coords(input_value)

    # Dedup toponyms
    seen = set()
    uniq_toponyms = []
    for t in toponyms:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq_toponyms.append(t)
    toponyms = uniq_toponyms[:3]  # cap at 3 to respect rate limit

    if not toponyms and not coords:
        return _error_envelope("No toponyms or geo-coordinates in input")

    # Rate-limit: Nominatim allows 1 req/sec
    all_claims: List[Claim] = []
    all_toponyms_data: List[Dict[str, Any]] = []
    aspects_queried = {"location", "country", "region", "type"}
    aspects_covered = set()

    for i, t in enumerate(toponyms):
        if i > 0:
            time.sleep(1.1)  # respect rate limit
        # Lemmatize to nominative case before OSM lookup — handles
        # "под Львовом" → "Львов", "около Киева" → "Киев", etc.
        # This fixes Finding 2 from the system test.
        t_lemma = lemmatize_toponym(t)
        if t_lemma != t:
            sys.stderr.write(
                f"[site-context-loader] lemmatized '{t}' → '{t_lemma}' for OSM lookup\n"
            )
        results = nominatim_search(t_lemma, lang_hint=lang_hint)
        if not results:
            all_claims.append(Claim(
                text=f"No OSM matches for toponym '{t}' (lemmatized to '{t_lemma}')",
                source="site-context-loader",
                span=f"nominatim:search?q={urllib.parse.quote(t_lemma)}",
                confidence=0.5,
                tags=["location"],
            ))
            continue
        top = results[0]
        addr = top.get("address") or {}
        assumption = build_assumption_doc(t, results)
        all_toponyms_data.append({
            "query": t,
            "query_lemmatized": t_lemma,
            "best_match": top.get("display_name"),
            "lat": top.get("lat"),
            "lon": top.get("lon"),
            "type": top.get("type") or top.get("class"),
            "country": addr.get("country"),
            "region": addr.get("state") or addr.get("region"),
            "assumption_doc": assumption,
            "alternatives": [
                {"display_name": r.get("display_name"),
                 "lat": r.get("lat"), "lon": r.get("lon")}
                for r in results[1:3]
            ],
        })

        all_claims.append(Claim(
            text=f"Toponym '{t}' (lemmatized to '{t_lemma}') → {top.get('display_name')} at {top.get('lat')},{top.get('lon')}",
            source="osm-nominatim",
            span=f"nominatim:search?q={urllib.parse.quote(t_lemma)}",
            confidence=0.85,
            tags=["location"],
        ))
        if addr.get("country"):
            all_claims.append(Claim(
                text=f"'{t}' is in {addr['country']}",
                source="osm-nominatim",
                span=f"nominatim:address.country",
                confidence=0.9,
                tags=["country"],
            ))
            aspects_covered.add("country")
        if addr.get("state") or addr.get("region"):
            region = addr.get("state") or addr.get("region")
            all_claims.append(Claim(
                text=f"'{t}' is in region: {region}",
                source="osm-nominatim",
                span=f"nominatim:address.state",
                confidence=0.85,
                tags=["region"],
            ))
            aspects_covered.add("region")
        if top.get("type"):
            all_claims.append(Claim(
                text=f"'{t}' is a {top.get('type')} (geographic type)",
                source="osm-nominatim",
                span=f"nominatim:type",
                confidence=0.8,
                tags=["type"],
            ))
            aspects_covered.add("type")
        aspects_covered.add("location")

    for i, (lat, lon) in enumerate(coords):
        if i > 0 or toponyms:
            time.sleep(1.1)
        rev = nominatim_reverse(lat, lon, lang_hint=lang_hint)
        if rev:
            addr = rev.get("address") or {}
            display = rev.get("display_name", f"{lat},{lon}")
            all_toponyms_data.append({
                "query": f"{lat},{lon}",
                "best_match": display,
                "lat": lat, "lon": lon,
                "country": addr.get("country"),
                "region": addr.get("state") or addr.get("region"),
                "type": rev.get("type"),
            })
            all_claims.append(Claim(
                text=f"Coords {lat},{lon} → {display}",
                source="osm-nominatim",
                span=f"nominatim:reverse?lat={lat}&lon={lon}",
                confidence=0.85,
                tags=["location"],
            ))
            aspects_covered.add("location")

    if not all_claims:
        return _error_envelope("No claims produced — all Nominatim queries failed", confidence=0.2)

    # Build brief
    parts = []
    for td in all_toponyms_data:
        parts.append(f"📍 {td['query']} → {td.get('best_match', '?')}"
                     + (f" ({td.get('country')})" if td.get("country") else ""))
    brief_text = "🗺️ site-context-loader:\n  " + "\n  ".join(parts)
    if all_toponyms_data:
        brief_text += "\n  → assumptions ready, agent can confirm with user"

    elapsed = time.time() - t_start

    # Pattern 1 brief
    grounded = None
    if _HAS_PATTERN1:
        try:
            grounded = build_brief(
                summary=brief_text,
                claims=all_claims,
                aspects_queried=sorted(aspects_queried),
                aspects_covered=sorted(aspects_covered),
                sources_used=1,
                sources_total=1,
                transient=transient,
                extra={
                    "toponyms": all_toponyms_data,
                    "assumption_doc": "\n\n".join(
                        td.get("assumption_doc", "") for td in all_toponyms_data
                        if td.get("assumption_doc")
                    ),
                    "extraction_meta": {
                        "elapsed_sec": round(elapsed, 2),
                        "queries_made": len(toponyms) + len(coords),
                    },
                },
            )
        except Exception as e:
            sys.stderr.write(f"[site-context-loader] grounded brief build failed: {e}\n")
            grounded = None

    if grounded is None:
        data = {
            "brief": brief_text,
            "toponyms": all_toponyms_data,
            "extraction_meta": {"elapsed_sec": round(elapsed, 2)},
        }
    else:
        data = grounded

    confidence = 0.85 if all_claims else 0.3
    return {
        "status": "success",
        "confidence": round(confidence, 2),
        "data": data,
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="site-context-loader — geographic context from toponyms/coords",
    )
    ap.add_argument("input", help="Toponym, 'lat,lon', or '-' for stdin")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang-hint", default="auto")
    ap.add_argument("--transient", action="store_true")
    args = ap.parse_args()
    result = load(args.input, lang_hint=args.lang_hint, transient=args.transient)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
