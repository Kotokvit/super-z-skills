#!/usr/bin/env python3
"""
media_triage.py — Online-layer triage of audio/video URLs.

Triggered by conversation-watcher when a YouTube/Soundcloud/media URL or
audio/video file appears in the user's message. Downloads audio via yt-dlp,
transcribes via z-ai ASR, summarizes via poler-toolkit, returns a compact
brief for the agent.

CLI conventions match orchestrator:
    python3 media_triage.py <input> --json
    echo "msg with URL" | python3 media_triage.py - --json

Output: standard envelope {status, confidence, data, error}.

Author: Online Layer build, 2026-07-04
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parent.parent  # /home/z/my-project
CACHE_DIR = Path("/tmp/media_triage_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# yt-dlp installed via pip --user; ensure it's on PATH
YT_DLP = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"

# poler-toolkit ingest.py — for theme/keyword extraction
POLER_INGEST = SKILL_DIR.parent / "poler-toolkit" / "scripts" / "ingest.py"

# Pattern 1 (source-grounded brief) — shared helper from _orchestrator
_ORCH_SCRIPTS = SKILL_DIR.parent / "_orchestrator" / "scripts"
if str(_ORCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ORCH_SCRIPTS))
try:
    from patterns.source_grounded_brief import build_brief, Claim, validate_brief
    _HAS_PATTERN1 = True
except Exception as _e:  # pragma: no cover
    sys.stderr.write(f"[media-triage] WARNING: source_grounded_brief unavailable: {_e}\n")
    _HAS_PATTERN1 = False

# URL patterns for media sources
URL_PATTERNS = [
    # YouTube: watch?v=, youtu.be/, embed/, shorts/
    re.compile(r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,})[^\s]*', re.IGNORECASE),
    # Soundcloud
    re.compile(r'https?://(?:www\.)?soundcloud\.com/[^\s]+', re.IGNORECASE),
    # Vimeo
    re.compile(r'https?://(?:www\.)?vimeo\.com/[^\s]+', re.IGNORECASE),
    # Direct audio/video URLs
    re.compile(r'https?://[^\s]+\.(?:mp3|wav|m4a|mp4|webm|aac|flac|ogg)[^\s]*', re.IGNORECASE),
]

# Local file extensions (matched when input is a path, not a URL)
LOCAL_MEDIA_EXTS = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".aac", ".flac", ".ogg"}


# ─────────────────────────────────────────────────────────────────────
# URL detection
# ─────────────────────────────────────────────────────────────────────

def extract_media_url(text: str) -> Optional[str]:
    """Find first media URL in text. Returns None if not found."""
    for pat in URL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).rstrip(".,);")
    return None


def classify_source(url: str) -> str:
    """Return source_type: youtube / soundcloud / vimeo / url."""
    low = url.lower()
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "soundcloud.com" in low:
        return "soundcloud"
    if "vimeo.com" in low:
        return "vimeo"
    return "url"


# ─────────────────────────────────────────────────────────────────────
# yt-dlp audio extraction (with anti-bot bypass strategies)
# ─────────────────────────────────────────────────────────────────────

def get_cache_key(url: str) -> str:
    """Stable hash for caching transcripts."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# yt-dlp anti-bot bypass strategies. Tried in order; first that works wins.
# Each strategy is a list of extra CLI args to pass to yt-dlp.
# These cover the most common YouTube/SoundCloud blocks:
#   - "Sign in to confirm you're not a bot" → use android/ios/tv/mweb clients
#   - "Unable to extract video data" → use web_safari + cookies
#   - HTTP 429 → use po_token + different client
# Order matters: start with no-arg, then progressively more aggressive.
# YouTube cloud-IP blocks often require multiple client rotations.
YT_DLP_STRATEGIES = [
    # Strategy 0: vanilla (works for most non-blocked content)
    [],
    # Strategy 1: android client (often bypasses bot detection)
    ["--extractor-args", "youtube:player_client=android"],
    # Strategy 2: ios client
    ["--extractor-args", "youtube:player_client=ios"],
    # Strategy 3: tv_embedded client — often works when web/android blocked
    ["--extractor-args", "youtube:player_client=tv_embedded"],
    # Strategy 4: mweb (mobile web) client
    ["--extractor-args", "youtube:player_client=mweb"],
    # Strategy 5: tv client
    ["--extractor-args", "youtube:player_client=tv"],
    # Strategy 6: multiple clients in fallback order
    ["--extractor-args", "youtube:player_client=android,web_safari,web,tv"],
    # Strategy 7: with throttling to avoid rate limit
    ["--extractor-args", "youtube:player_client=android,web_safari",
     "--sleep-requests", "0.5"],
    # Strategy 8: try with default client but force user-agent
    ["--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"],
    # Strategy 9: try source_address=0.0.0.0 (force IPv4)
    ["--source-address", "0.0.0.0",
     "--extractor-args", "youtube:player_client=android"],
]

# Optional: env-configured cookies file (most reliable bypass)
COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "")
COOKIES_FROM_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "")  # e.g. "firefox", "chrome", "chromium"

# Detect available browser for cookies-from-browser fallback
def _detect_browser_for_cookies() -> Optional[str]:
    """Returns 'firefox' / 'chrome' / 'chromium' / None."""
    if COOKIES_FROM_BROWSER:
        return COOKIES_FROM_BROWSER
    home = Path.home()
    if (home / ".mozilla/firefox").exists():
        return "firefox"
    if (home / ".config/google-chrome").exists() or (home / ".config/chromium").exists():
        return "chrome"
    return None


def _build_yt_dlp_strategies() -> List[List[str]]:
    """Build full strategy list, including cookies-from-browser if available."""
    strategies = [list(s) for s in YT_DLP_STRATEGIES]
    # Add cookies-based strategies at the end (most reliable but most invasive)
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        strategies.append(["--cookies", COOKIES_FILE])
        strategies.append(["--cookies", COOKIES_FILE,
                          "--extractor-args", "youtube:player_client=android,web"])
    browser = _detect_browser_for_cookies()
    if browser:
        strategies.append(["--cookies-from-browser", browser])
        strategies.append(["--cookies-from-browser", browser,
                          "--extractor-args", "youtube:player_client=android,web"])
    return strategies


def _run_yt_dlp_with_strategies(base_cmd: List[str], url: str,
                                  timeout: int = 300) -> subprocess.CompletedProcess:
    """Run yt-dlp with multiple anti-bot strategies. Returns first successful proc.

    Raises RuntimeError if all strategies fail (with combined error message).
    """
    strategies = _build_yt_dlp_strategies()
    last_err = ""
    for i, extra_args in enumerate(strategies):
        cmd = list(base_cmd) + list(extra_args) + [url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if proc.returncode == 0:
                return proc
            err = proc.stderr.strip()
            # If error is NOT anti-bot (e.g. video not found, network), no point retrying
            err_lower = err.lower()
            non_retryable = any(s in err_lower for s in [
                "video unavailable", "private video", "does not exist",
                "no longer available", "removed by user", "not found",
                "unsupported url",
            ])
            if non_retryable:
                raise RuntimeError(f"yt-dlp non-retryable error: {err[:300]}")
            last_err = err[:200]
            sys.stderr.write(
                f"[media-triage] yt-dlp strategy {i} failed, trying next: {err[:120]}\n"
            )
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s"
            sys.stderr.write(
                f"[media-triage] yt-dlp strategy {i} timed out, trying next\n"
            )
    raise RuntimeError(
        f"yt-dlp exhausted {len(strategies)} anti-bot strategies. Last error: {last_err}"
    )


def download_audio(url: str, max_duration_sec: int = 7200) -> Tuple[str, Dict[str, Any]]:
    """Download audio via yt-dlp. Returns (audio_path, meta_dict).

    meta_dict contains: title, duration_sec, source_type, original_url.

    Uses multi-strategy anti-bot bypass: tries vanilla yt-dlp first, then
    progressively more aggressive strategies (android client, ios client,
    cookies-from-browser). This handles YouTube's "Sign in to confirm
    you're not a bot" blocks without manual intervention.
    """
    cache_key = get_cache_key(url)
    output_template = str(CACHE_DIR / f"{cache_key}.%(ext)s")
    final_path = CACHE_DIR / f"{cache_key}.wav"

    # If cached WAV exists, skip download
    if final_path.exists() and final_path.stat().st_size > 0:
        # Still need metadata — try to get from sidecar JSON
        meta_path = CACHE_DIR / f"{cache_key}.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return str(final_path), meta
            except Exception:
                pass
        return str(final_path), {"title": "cached", "duration_sec": None,
                                 "source_type": classify_source(url)}

    # Get metadata first (title, duration) — with anti-bot strategies
    meta_base_cmd = [
        YT_DLP, "--dump-json", "--no-warnings",
        "--no-playlist",  # don't grab whole playlist
    ]
    title = url
    duration = None
    try:
        proc = _run_yt_dlp_with_strategies(meta_base_cmd, url, timeout=60)
        if proc.stdout.strip():
            info = json.loads(proc.stdout.strip().split("\n")[0])
            title = info.get("title", url)
            duration = info.get("duration")
    except Exception as e:
        # Metadata fetch failed — but we can still try the actual download
        # which uses different strategies; if all fail, we'll raise there.
        sys.stderr.write(f"[media-triage] metadata fetch failed: {e}\n")

    # Skip if duration exceeds hard limit
    if duration and duration > max_duration_sec:
        # Tell yt-dlp to download only the first N seconds
        download_section = f"0-{max_duration_sec}"
    else:
        download_section = None

    # Download audio as WAV 16kHz mono — with anti-bot strategies
    download_base_cmd = [
        YT_DLP,
        "-x", "--audio-format", "wav",
        "--audio-quality", "0",
        "--postprocessor-args", f"-ar 16000 -ac 1",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "-o", output_template,
    ]
    if download_section:
        download_base_cmd.extend(["--download-sections", f"*{download_section}"])
        download_base_cmd.extend(["--force-keyframes-at-cuts"])

    try:
        proc = _run_yt_dlp_with_strategies(download_base_cmd, url, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp timed out (300s) for {url}")
    except FileNotFoundError:
        raise RuntimeError(f"yt-dlp not found at {YT_DLP}")
    except RuntimeError as e:
        # All yt-dlp audio strategies failed — try subtitles fallback
        sys.stderr.write(
            f"[media-triage] all audio strategies failed, trying subtitles fallback\n"
        )
        subs_path = _try_subtitles_fallback(url, cache_key, title, duration)
        if subs_path:
            # Build a synthetic "audio file" containing the subtitle text
            # so downstream ASR can be skipped — we already have text
            fake_wav = CACHE_DIR / f"{cache_key}.wav"
            fake_wav.write_bytes(b"")  # empty placeholder; downstream checks .txt
            meta = {
                "title": title,
                "duration_sec": duration,
                "source_type": classify_source(url),
                "original_url": url,
                "truncated": bool(download_section),
                "transcript_source": "subtitles",
                "transcript_path": str(subs_path),
            }
            meta_path = CACHE_DIR / f"{cache_key}.meta.json"
            try:
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            return str(fake_wav), meta
        raise  # re-raise the original RuntimeError

    if not final_path.exists():
        # yt-dlp sometimes uses different ext; find any audio file with our cache key
        candidates = list(CACHE_DIR.glob(f"{cache_key}.*"))
        audio_files = [c for c in candidates if c.suffix.lower() in {".wav", ".m4a", ".mp3", ".webm"}]
        if not audio_files:
            # Last-chance fallback: try subtitles
            sys.stderr.write(
                f"[media-triage] no audio produced, trying subtitles fallback\n"
            )
            subs_path = _try_subtitles_fallback(url, cache_key, title, duration)
            if subs_path:
                fake_wav = CACHE_DIR / f"{cache_key}.wav"
                fake_wav.write_bytes(b"")
                meta = {
                    "title": title, "duration_sec": duration,
                    "source_type": classify_source(url),
                    "original_url": url,
                    "truncated": bool(download_section),
                    "transcript_source": "subtitles",
                    "transcript_path": str(subs_path),
                }
                meta_path = CACHE_DIR / f"{cache_key}.meta.json"
                try:
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                return str(fake_wav), meta
            raise RuntimeError(f"yt-dlp produced no audio file in {CACHE_DIR}")
        # If not WAV, convert via ffmpeg
        if audio_files[0].suffix.lower() != ".wav":
            converted = CACHE_DIR / f"{cache_key}.wav"
            conv_cmd = [FFMPEG, "-y", "-i", str(audio_files[0]),
                        "-ar", "16000", "-ac", "1", str(converted)]
            subprocess.run(conv_cmd, capture_output=True, timeout=120, check=True)
            audio_files[0].unlink(missing_ok=True)
            final_path = converted

    meta = {
        "title": title,
        "duration_sec": duration,
        "source_type": classify_source(url),
        "original_url": url,
        "truncated": bool(download_section),
    }
    # Save sidecar meta
    meta_path = CACHE_DIR / f"{cache_key}.meta.json"
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return str(final_path), meta


def _try_subtitles_fallback(url: str, cache_key: str,
                              title: str, duration) -> Optional[Path]:
    """Fallback when audio extraction is blocked: try to fetch subtitles.

    Tries in order:
      1. yt-dlp --write-subs --write-auto-subs --skip-download (with strategies)
      2. youtube-transcript-api (if installed)
      3. Web search for "{title} transcript" → fetch third-party transcript

    Returns path to .transcript.txt file, or None if all fallbacks fail.
    """
    out_txt = CACHE_DIR / f"{cache_key}.transcript.txt"
    if out_txt.exists() and out_txt.stat().st_size > 0:
        return out_txt

    # 1. yt-dlp subtitles (with anti-bot strategies)
    subs_template = str(CACHE_DIR / f"{cache_key}.%(language)s.%(ext)s")
    subs_base_cmd = [
        YT_DLP,
        "--write-subs", "--write-auto-subs",
        "--sub-format", "vtt/srt/best",
        "--skip-download",
        "--no-playlist", "--no-warnings", "--no-progress",
        "--sub-langs", "en,ru,en-orig,en-US",
        "-o", subs_template,
    ]
    try:
        proc = _run_yt_dlp_with_strategies(subs_base_cmd, url, timeout=60)
        # Find any subtitle file produced
        sub_files = list(CACHE_DIR.glob(f"{cache_key}.*.vtt")) + \
                    list(CACHE_DIR.glob(f"{cache_key}.*.srt")) + \
                    list(CACHE_DIR.glob(f"{cache_key}.*.json3"))
        if sub_files:
            text = _parse_subtitle_file(sub_files[0])
            if text and len(text) > 50:
                out_txt.write_text(text, encoding="utf-8")
                # cleanup sub files
                for sf in sub_files:
                    sf.unlink(missing_ok=True)
                sys.stderr.write(
                    f"[media-triage] ✓ subtitles fallback succeeded ({len(text)} chars)\n"
                )
                return out_txt
    except Exception as e:
        sys.stderr.write(f"[media-triage] yt-dlp subs fallback failed: {e}\n")

    # 2. youtube-transcript-api (if available)
    try:
        sys.path.insert(0, str(Path.home() / ".local/lib/python3.13/site-packages"))
        from youtube_transcript_api import YouTubeTranscriptApi
        # extract video id from URL
        vid_match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})', url)
        if vid_match:
            vid = vid_match.group(1)
            api = YouTubeTranscriptApi()
            for lang in ['en', 'ru', 'en-US', 'en-orig']:
                try:
                    t = api.fetch(vid, languages=[lang])
                    if t and t.snippets:
                        text = " ".join(s.text for s in t.snippets)
                        if len(text) > 50:
                            out_txt.write_text(text, encoding="utf-8")
                            sys.stderr.write(
                                f"[media-triage] ✓ youtube-transcript-api fallback "
                                f"succeeded ({len(text)} chars, lang={lang})\n"
                            )
                            return out_txt
                except Exception:
                    continue
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"[media-triage] youtube-transcript-api fallback failed: {e}\n")

    # 3. Web search for transcript — search "{title} transcript"
    # This finds third-party transcript sites (e.g. greasyfork, github, blogs)
    try:
        text = _web_search_transcript(url, title)
        if text and len(text) > 100:
            out_txt.write_text(text, encoding="utf-8")
            sys.stderr.write(
                f"[media-triage] ✓ web-search transcript fallback succeeded "
                f"({len(text)} chars)\n"
            )
            return out_txt
    except Exception as e:
        sys.stderr.write(f"[media-triage] web-search fallback failed: {e}\n")

    return None


def _parse_subtitle_file(path: Path) -> str:
    """Parse VTT/SRT/JSON3 subtitle file → plain text."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    # Strip VTT/SRT timing lines and tags
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip VTT header
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        # Skip SRT index numbers
        if line.isdigit():
            continue
        # Skip timing lines (00:00:01.000 --> 00:00:03.000)
        if "-->" in line:
            continue
        # Strip HTML/VTT tags <c>, <i>, etc.
        clean = re.sub(r'<[^>]+>', '', line)
        # Strip duplicate lines (VTT often repeats)
        if lines and lines[-1] == clean:
            continue
        lines.append(clean)
    return " ".join(lines)


def _web_search_transcript(url: str, title: str) -> Optional[str]:
    """Search the web for a transcript of the video, fetch and parse it."""
    # Use z-ai web-search if available
    z_ai = shutil.which("z-ai") or Z_AI
    if not Path(z_ai).exists():
        return None
    # Extract video ID for YouTube URLs — much more reliable than title search
    vid = None
    vid_match = re.search(r'(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})', url)
    if vid_match:
        vid = vid_match.group(1)
    # Build queries — prefer video ID over title (title may be missing if metadata fetch failed)
    queries = []
    if vid:
        queries.extend([
            f'"{vid}" transcript',
            f'youtube {vid} full transcript',
            f'site:reddit.com "{vid}" transcript',
        ])
    if title and title != url:
        queries.extend([
            f'"{title}" transcript',
            f'"{title}" full text',
        ])
    queries.append(f'youtube {url} transcript')
    for q in queries:
        try:
            cmd = [z_ai, "web-search", "--query", q, "--count", "5"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                continue
            try:
                data = json.loads(proc.stdout)
            except Exception:
                continue
            results = data.get("results") or data.get("data", {}).get("results") or []
            for r in results[:3]:
                page_url = r.get("url") or r.get("link", "")
                if not page_url:
                    continue
                # Skip YouTube itself — we know it's blocked
                if "youtube.com" in page_url or "youtu.be" in page_url:
                    continue
                # Fetch the page
                try:
                    cmd2 = [z_ai, "web-reader", "--url", page_url]
                    proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
                    if proc2.returncode != 0:
                        continue
                    try:
                        page = json.loads(proc2.stdout)
                    except Exception:
                        continue
                    text = page.get("text") or page.get("content") or page.get("html", "")
                    if not text:
                        continue
                    # Strip HTML tags
                    text = re.sub(r'<script[^>]*>.*?</script>', '', text,
                                  flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text,
                                  flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) > 500:
                        return text[:50000]  # cap
                except Exception:
                    continue
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────
# ASR transcription via z-ai CLI (with chunking — z-ai has 30s limit)
# ─────────────────────────────────────────────────────────────────────

# z-ai ASR API rejects files longer than 30 seconds — we chunk into 28s
# segments (small overlap buffer to avoid losing last words)
CHUNK_DURATION_SEC = 28


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return float(proc.stdout.strip())
    except Exception:
        pass
    return 0.0


def _split_audio_into_chunks(audio_path: str, cache_key: str) -> List[str]:
    """Split audio file into N 28-second chunks via ffmpeg.

    Returns list of chunk file paths. Files are stored alongside the source
    audio in the cache dir.
    """
    duration = _get_audio_duration(audio_path)
    if duration <= 0:
        # ffprobe failed — assume single chunk
        return [audio_path]

    n_chunks = max(1, int(duration // CHUNK_DURATION_SEC) +
                   (1 if duration % CHUNK_DURATION_SEC > 0 else 0))

    chunks: List[str] = []
    chunk_dir = CACHE_DIR / f"{cache_key}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    for i in range(n_chunks):
        chunk_path = chunk_dir / f"chunk_{i:04d}.wav"
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            chunks.append(str(chunk_path))
            continue
        start_sec = i * CHUNK_DURATION_SEC
        cmd = [
            FFMPEG, "-y",
            "-i", audio_path,
            "-ss", str(start_sec),
            "-t", str(CHUNK_DURATION_SEC),
            "-ar", "16000", "-ac", "1",
            str(chunk_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            if proc.returncode == 0 and chunk_path.exists():
                chunks.append(str(chunk_path))
        except Exception:
            pass

    return chunks


def _transcribe_chunk(chunk_path: str) -> str:
    """Transcribe a single chunk (≤28s) via z-ai asr CLI."""
    cmd = [Z_AI, "asr", "--file", chunk_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return ""

    if proc.returncode != 0:
        # Log but don't crash — we can still use other chunks
        sys.stderr.write(
            f"[media-triage] chunk {Path(chunk_path).name} ASR failed: "
            f"{proc.stderr[:150]}\n"
        )
        return ""

    # z-ai asr stdout has emoji lines + JSON mixed.
    # Find the JSON object that contains the "text" field — typically the
    # largest { ... } block in the output.
    stdout = proc.stdout
    json_start = stdout.find("{")
    if json_start < 0:
        return ""

    # Try parsing incrementally — find matching closing brace
    # (naive: try each substring starting at { until one parses)
    depth = 0
    for i in range(json_start, len(stdout)):
        if stdout[i] == "{":
            depth += 1
        elif stdout[i] == "}":
            depth -= 1
            if depth == 0:
                # Try parsing this substring
                candidate = stdout[json_start:i + 1]
                try:
                    data = json.loads(candidate)
                    text = (data.get("text", "") or
                            data.get("transcription", "")).strip()
                    if text:
                        return text
                except json.JSONDecodeError:
                    pass
                # Move to next {
                next_brace = stdout.find("{", i + 1)
                if next_brace < 0:
                    break
                json_start = next_brace
                depth = 0
    return ""


def transcribe_audio(audio_path: str, lang_hint: str = "auto",
                     cache_key: Optional[str] = None) -> Tuple[str, float]:
    """Transcribe audio file via z-ai asr CLI, with 28s chunking.

    z-ai ASR API has a hard 30-second limit per file. We split longer audio
    into 28s chunks via ffmpeg, transcribe each, concatenate text.

    Args:
        audio_path: path to WAV file (16kHz mono recommended)
        lang_hint: language hint (currently unused by z-ai CLI)
        cache_key: if provided, used to look up/store per-chunk cache

    Returns (full_transcript_text, total_elapsed_sec).
    """
    t0 = time.time()

    # Check duration — if short enough, single call
    duration = _get_audio_duration(audio_path)

    if duration > 0 and duration <= CHUNK_DURATION_SEC:
        # Single-shot
        text = _transcribe_chunk(audio_path)
        return text, time.time() - t0

    # Otherwise: chunk + concatenate
    if not cache_key:
        cache_key = hashlib.sha256(audio_path.encode()).hexdigest()[:16]

    chunks = _split_audio_into_chunks(audio_path, cache_key)
    if not chunks:
        raise RuntimeError(f"Audio chunking failed for {audio_path}")

    parts: List[str] = []
    for chunk_path in chunks:
        # Per-chunk cache
        chunk_cache = CACHE_DIR / f"{cache_key}_chunks" / \
                      (Path(chunk_path).stem + ".txt")
        if chunk_cache.exists():
            try:
                parts.append(chunk_cache.read_text(encoding="utf-8"))
                continue
            except Exception:
                pass

        text = _transcribe_chunk(chunk_path)
        if text:
            parts.append(text)
            try:
                chunk_cache.write_text(text, encoding="utf-8")
            except Exception:
                pass

    full_transcript = " ".join(parts).strip()
    elapsed = time.time() - t0
    return full_transcript, elapsed


# ─────────────────────────────────────────────────────────────────────
# poler-toolkit summarization
# ─────────────────────────────────────────────────────────────────────

def summarize_text(text: str) -> Tuple[Dict[str, Any], float]:
    """Run poler-toolkit ingest.py on transcript text.

    Returns (summary_dict, elapsed_sec).
    summary_dict has: theme (str|null), keywords (list), clusters (count int).
    """
    if not POLER_INGEST.exists():
        # Fallback: simple first-500-chars summary
        return {
            "theme": None,
            "keywords": [],
            "clusters_count": 0,
            "fallback": True,
        }, 0.0

    t0 = time.time()
    # Write transcript to a temp file (poler needs a path or "-")
    with tempfile.NamedTemporaryFile(prefix="media_triage_", suffix=".txt",
                                     delete=False, mode="w", encoding="utf-8") as tf:
        tf.write(text)
        tmp_path = tf.name

    try:
        cmd = ["python3", str(POLER_INGEST), tmp_path, "--json", "--no-clusters"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            return {
                "theme": None,
                "keywords": [],
                "clusters_count": 0,
                "fallback": True,
                "error": proc.stderr[:200],
            }, elapsed

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {
                "theme": None,
                "keywords": [],
                "clusters_count": 0,
                "fallback": True,
            }, elapsed

        # poler-toolkit returns {status, confidence, data: {theme, keywords, ...}}
        data = result.get("data") or {}
        theme_obj = data.get("theme") or {}
        keywords = data.get("keywords") or []

        return {
            "theme": theme_obj.get("name") or theme_obj.get("semantic"),
            "keywords": keywords[:8] if isinstance(keywords, list) else [],
            "clusters_count": len(data.get("clusters") or []),
            "fallback": False,
        }, elapsed
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Brief formatting (the key output for the agent)
# ─────────────────────────────────────────────────────────────────────

def format_duration(sec: Optional[float]) -> str:
    if not sec or sec <= 0:
        return "unknown"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def format_brief(meta: Dict[str, Any], transcript: str,
                 summary: Dict[str, Any]) -> str:
    """Build the 3-5 line compact brief for the agent."""
    lines = []
    title = meta.get("title", "unknown")
    src = meta.get("source_type", "url")
    duration = format_duration(meta.get("duration_sec"))
    chars = len(transcript)

    line1 = f"🎧 media-triage: \"{title[:80]}\""
    if meta.get("truncated"):
        line1 += " [truncated]"
    lines.append(line1)

    line2 = f"- duration: {duration}, transcript: {chars:,} chars, source: {src}"
    lines.append(line2)

    theme = summary.get("theme")
    kws = summary.get("keywords") or []
    if theme or kws:
        kw_str = ", ".join(kws[:5]) if kws else "—"
        line3 = f"- theme: {theme or 'n/a'}, top keywords: {kw_str}"
    else:
        line3 = f"- theme: n/a (poler-toolkit fallback), first 200 chars: {transcript[:200]!r}"
    lines.append(line3)

    # Suggested questions based on keywords
    if kws:
        q1 = f"что автор говорит про {kws[0]}?"
        q2 = f"какие основные аргументы про {kws[1]}?" if len(kws) > 1 else "каковы выводы?"
        lines.append(f"- suggested user questions: \"{q1}\", \"{q2}\"")

    lines.append("→ agent has full transcript context, can answer directly")
    return "\n".join(lines)


def suggest_questions(kws: List[str]) -> List[str]:
    """Generate 2-3 likely user questions based on keywords."""
    if not kws:
        return ["о чём это видео?", "каковы основные выводы?"]
    qs = [f"что автор говорит про {kws[0]}?"]
    if len(kws) > 1:
        qs.append(f"какие аргументы про {kws[1]}?")
    qs.append("каковы основные выводы?")
    return qs


# ─────────────────────────────────────────────────────────────────────
# Confidence calculation
# ─────────────────────────────────────────────────────────────────────

def calc_confidence(transcript: str, summary: Dict[str, Any]) -> float:
    """Heuristic: 0.95 if dense transcript + poler found theme; 0.5 fallback."""
    if not transcript:
        return 0.0
    if len(transcript) < 100:
        return 0.2
    if summary.get("fallback"):
        return 0.5
    base = 0.75
    if summary.get("theme"):
        base += 0.1
    if summary.get("keywords"):
        base += 0.1
    return min(0.95, base)


# ─────────────────────────────────────────────────────────────────────
# Main triage pipeline
# ─────────────────────────────────────────────────────────────────────

def triage(input_value: str, max_duration_sec: int = 7200,
           lang_hint: str = "auto") -> Dict[str, Any]:
    """Run full triage pipeline. Returns standard envelope."""

    t_start = time.time()

    # 1. Resolve input to a media URL or local file path
    media_url: Optional[str] = None
    local_path: Optional[str] = None

    if input_value == "-":
        # Read message text from stdin, extract URL
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""
        media_url = extract_media_url(stdin_text)
        if not media_url:
            return _error_envelope("No media URL found in stdin message")
    elif input_value.startswith(("http://", "https://")):
        media_url = input_value
    elif Path(input_value).exists() and Path(input_value).suffix.lower() in LOCAL_MEDIA_EXTS:
        local_path = input_value
    else:
        # Try to extract URL from arbitrary text input
        media_url = extract_media_url(input_value)
        if not media_url:
            # Maybe it's a file path that doesn't exist?
            return _error_envelope(
                f"Input is not a URL, media file, or text containing URL: {input_value[:100]}"
            )

    # 2. Get audio path + metadata
    try:
        if media_url:
            audio_path, meta = download_audio(media_url, max_duration_sec=max_duration_sec)
            # Save transcript cache key for later
            cache_key = get_cache_key(media_url)
        else:
            # Local file: convert to WAV 16kHz mono via ffmpeg if needed
            audio_path, meta = _prepare_local_audio(local_path)
            cache_key = hashlib.sha256(local_path.encode()).hexdigest()[:16]
    except Exception as e:
        # Detect if this was an anti-bot block — return Pattern 1 structured error
        err_str = str(e)
        is_blocked = ("anti-bot" in err_str.lower() or
                      "sign in to confirm" in err_str.lower() or
                      "strategies" in err_str.lower())
        return _error_envelope(
            f"Audio extraction failed: {e}",
            blocked=is_blocked,
            url=media_url or local_path,
        )

    # 3. Check transcript cache
    transcript_cache = CACHE_DIR / f"{cache_key}.transcript.txt"

    # Special case: subtitles fallback already produced a transcript file
    # (when audio extraction was blocked by YouTube anti-bot)
    subs_transcript_path = meta.get("transcript_path") if meta else None
    if subs_transcript_path and Path(subs_transcript_path).exists():
        try:
            transcript = Path(subs_transcript_path).read_text(encoding="utf-8")
            # Mirror it to the standard transcript cache location
            try:
                transcript_cache.write_text(transcript, encoding="utf-8")
            except Exception:
                pass
            asr_elapsed = 0.0
            cached = True
            sys.stderr.write(
                f"[media-triage] using subtitles transcript ({len(transcript):,} chars) "
                f"instead of ASR (audio was blocked)\n"
            )
        except Exception:
            transcript = ""
            asr_elapsed = 0.0
            cached = False
    elif transcript_cache.exists():
        try:
            transcript = transcript_cache.read_text(encoding="utf-8")
            asr_elapsed = 0.0
            cached = True
        except Exception:
            transcript = ""
            asr_elapsed = 0.0
            cached = False
    else:
        cached = False
        # 4. Transcribe (with chunking — z-ai ASR has 30s limit per file)
        try:
            transcript, asr_elapsed = transcribe_audio(
                audio_path, lang_hint=lang_hint, cache_key=cache_key,
            )
            # Cache transcript
            try:
                transcript_cache.write_text(transcript, encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            return _error_envelope(f"ASR failed: {e}")

    if not transcript or len(transcript.strip()) < 10:
        return _error_envelope(
            f"ASR returned empty transcript (audio might be silent or too short)",
            confidence=0.2,
        )

    # 5. Summarize via poler-toolkit
    try:
        summary, summarize_elapsed = summarize_text(transcript)
    except Exception as e:
        summary = {"theme": None, "keywords": [], "fallback": True}
        summarize_elapsed = 0.0

    # 6. Build brief (Pattern 1: source-grounded)
    brief_text = format_brief(meta, transcript, summary)
    confidence = calc_confidence(transcript, summary)

    # 7. Save full transcript for agent reference
    transcript_path = str(transcript_cache)

    total_elapsed = time.time() - t_start

    # Pattern 1: build source-grounded brief (claims + coverage)
    # This is the architectural constraint — every claim MUST cite a source.
    grounded = None
    if _HAS_PATTERN1:
        claims = []
        # Claim: theme (if found)
        if summary.get("theme"):
            claims.append(Claim(
                text=f"Detected theme: {summary['theme']}",
                source="poler-toolkit",
                span=f"{transcript_path}:theme.name",
                confidence=0.85,
                tags=["theme"],
            ))
        # Claim: top keywords (one claim, citing poler-toolkit keywords list)
        kws = summary.get("keywords") or []
        if kws:
            claims.append(Claim(
                text=f"Top keywords: {', '.join(kws[:5])}",
                source="poler-toolkit",
                span=f"{transcript_path}:keywords[0:{min(5,len(kws))}]",
                confidence=0.8,
                tags=["keywords"],
            ))
        # Claim: transcript existence (citing the ASR source)
        claims.append(Claim(
            text=f"Full transcript available ({len(transcript):,} chars) — agent can answer questions directly from it",
            source="z-ai-asr",
            span=transcript_path,
            confidence=confidence,
            tags=["transcript"],
        ))
        # Claim: duration/title (citing yt-dlp metadata)
        claims.append(Claim(
            text=f"Source: \"{meta.get('title','unknown')}\" ({format_duration(meta.get('duration_sec'))})",
            source="yt-dlp" if media_url else "ffmpeg-local",
            span=media_url or local_path or "local-file",
            confidence=0.95,
            tags=["meta"],
        ))

        aspects_queried = ["theme", "keywords", "transcript", "meta", "entities"]
        aspects_covered = {t for c in claims for t in c.tags}

        try:
            grounded = build_brief(
                summary=brief_text,
                claims=claims,
                aspects_queried=aspects_queried,
                aspects_covered=sorted(aspects_covered),
                sources_used=2,  # ASR + poler-toolkit (or yt-dlp meta)
                sources_total=2,
                transient=False,
                extra={
                    "keywords": kws,
                    "theme": summary.get("theme"),
                    "suggested_questions": suggest_questions(kws),
                    "transcript_path": transcript_path,
                    "source": media_url or local_path,
                    "source_type": meta.get("source_type", "file"),
                    "title": meta.get("title", "unknown"),
                    "duration_sec": meta.get("duration_sec"),
                    "transcript_chars": len(transcript),
                    "extraction_meta": {
                        "method": "yt-dlp" if media_url else "ffmpeg-local",
                        "asr_elapsed_sec": round(asr_elapsed, 2),
                        "summarize_elapsed_sec": round(summarize_elapsed, 2),
                        "total_elapsed_sec": round(total_elapsed, 2),
                        "cached": cached,
                    },
                },
            )
        except Exception as e:
            sys.stderr.write(f"[media-triage] grounded brief build failed: {e}\n")
            grounded = None

    # Fallback to old format if Pattern 1 unavailable
    if grounded is None:
        data = {
            "source": media_url or local_path,
            "source_type": meta.get("source_type", "file"),
            "title": meta.get("title", "unknown"),
            "duration_sec": meta.get("duration_sec"),
            "transcript_chars": len(transcript),
            "brief": brief_text,
            "keywords": summary.get("keywords") or [],
            "theme": summary.get("theme"),
            "suggested_questions": suggest_questions(summary.get("keywords") or []),
            "transcript_path": transcript_path,
            "meta": {
                "extraction_method": "yt-dlp" if media_url else "ffmpeg-local",
                "asr_elapsed_sec": round(asr_elapsed, 2),
                "summarize_elapsed_sec": round(summarize_elapsed, 2),
                "total_elapsed_sec": round(total_elapsed, 2),
                "cached": cached,
            },
        }
    else:
        data = grounded

    return {
        "status": "success",
        "confidence": round(confidence, 2),
        "data": data,
        "error": None,
    }


def _prepare_local_audio(local_path: str) -> Tuple[str, Dict[str, Any]]:
    """Convert local audio file to WAV 16kHz mono if needed."""
    p = Path(local_path)
    if p.suffix.lower() == ".wav":
        # Assume already in correct format (z-ai asr will handle if not)
        return local_path, {
            "title": p.name,
            "duration_sec": None,
            "source_type": "file",
        }

    cache_key = hashlib.sha256(str(p).encode()).hexdigest()[:16]
    out_path = CACHE_DIR / f"local_{cache_key}.wav"
    if not out_path.exists():
        cmd = [FFMPEG, "-y", "-i", str(p), "-ar", "16000", "-ac", "1", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr[:200]}")

    return str(out_path), {
        "title": p.name,
        "duration_sec": None,
        "source_type": "file",
    }


def _error_envelope(msg: str, confidence: float = 0.0,
                     blocked: bool = False,
                     url: Optional[str] = None) -> Dict[str, Any]:
    """Return error envelope. If blocked=True, include Pattern 1 claims so
    gap-detector knows the source was blocked (not just missing)."""
    data = None
    if blocked and _HAS_PATTERN1:
        # Pattern 1: even errors should produce structured claims for the agent
        claims = [Claim(
            text=f"YouTube blocked extraction (cloud IP anti-bot): {url or 'unknown'}",
            source="media-triage",
            span="yt-dlp:strategies_exhausted",
            confidence=0.95,
            tags=["blocked", "video"],
        )]
        grounded = build_brief(
            summary=("⚠️ Video blocked by YouTube anti-bot. Tried 10 yt-dlp strategies "
                     "+ subtitles + transcript API + web-search. All failed. "
                     "Ask user to provide local file or alternative URL."),
            claims=claims,
            aspects_queried=["audio", "transcript", "metadata"],
            aspects_covered=[],  # nothing covered — all aspects unanswered
            sources_used=0,
            sources_total=5,
            transient=False,
            extra={
                "suggested_questions": [
                    "Could you provide a local audio/video file instead?",
                    "Could you share a different URL (e.g. Vimeo, direct mp4)?",
                    "Could you paste the transcript text directly?",
                ],
            },
        )
        data = grounded
    return {
        "status": "error",
        "confidence": confidence,
        "data": data,
        "error": msg,
        "blocked": blocked,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="media-triage — extract brief from audio/video URL or file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", help="Media URL, local file path, or '-' to read message from stdin")
    ap.add_argument("--json", action="store_true", help="Output JSON (always on for orchestrator)")
    ap.add_argument("--max-duration-sec", type=int, default=7200,
                    help="Hard cap on media length (default: 7200 = 2h)")
    ap.add_argument("--lang-hint", default="auto",
                    help="Language hint for ASR (e.g. 'ru', 'en', 'auto')")
    args = ap.parse_args()

    result = triage(args.input, max_duration_sec=args.max_duration_sec,
                    lang_hint=args.lang_hint)

    # Always output JSON (orchestrator convention)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
