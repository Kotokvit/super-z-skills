---
name: media-triage
version: 1.0.0
category: online-context
priority: 70
license: MIT
---

# media-triage

> **Online layer skill.** Triggered automatically by conversation-watcher when
> a YouTube/Soundcloud/media URL appears in the user's message — runs in
> background, before the agent answers. Not a CLI tool the user calls directly.

## What it does

```
user message contains "https://youtube.com/watch?v=..."
       │
       ▼
[1] extract media URL via regex
[2] yt-dlp → download audio (WAV 16kHz mono)
[3] z-ai asr → transcribe to text
[4] poler-toolkit → extract theme, keywords, clusters
[5] format → 3-5 line brief for the agent
       │
       ▼
context_brief.json (consumed by agent before answering)
```

## Trigger conditions (declared in manifest.json)

- URL containing `youtube.com`, `youtu.be`, `soundcloud.com`, `vimeo.com`
- File attachment `.mp3` `.wav` `.m4a` `.mp4` `.webm` `.aac` `.flac` `.ogg`
- Keywords in message: "подкаст", "podcast", "послушай", "посмотри",
  "watch this", "listen to"

## Output: the agent brief

The skill returns a standard envelope, but the key field for the agent is
`data.brief` — a 3-5 line human-readable summary. Example:

```
🎧 media-triage: "Lex Fridman #456 — Sam Altman"
- duration: 2h14m, transcript: 47k chars
- theme: tech-general, top keywords: AGI, alignment, OpenAI, regulation
- key topics: AGI timeline 2029, open vs closed, AI safety
- suggested user questions: "что там про regulation?", "AGI когда?"
→ agent already has full context, no need to ask user to summarize
```

## CLI usage (for testing)

```bash
# Direct URL
python3 media_triage.py "https://youtube.com/watch?v=xxx" --json

# From a message containing URL
echo "посмотри это https://youtu.be/abc123" | python3 media_triage.py - --json

# Local audio file
python3 media_triage.py /path/to/audio.mp3 --json
```

## Dependencies

- `yt-dlp` (in `~/.local/bin/yt-dlp`)
- `ffmpeg` (system)
- `z-ai` CLI (for ASR API)
- `poler-toolkit` skill (for theme/keyword extraction) — invoked via subprocess

## Caching

Transcripts are cached by URL hash in `/tmp/media_triage_cache/`. Re-running
on the same URL returns cached result in <1s.

## Failure modes

- **URL not accessible** → status=error, confidence=0.0, brief explains why
- **Media too long (>2h)** → truncated to `max_duration_sec`, brief notes this
- **ASR returns empty** → status=error, confidence=0.2
- **poler-toolkit fails** → fall back to first 500 chars of transcript as brief,
  confidence=0.5
