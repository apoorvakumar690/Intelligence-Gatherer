# Sangrahak — संग्राहक

Research utility toolkit. Currently: YouTube transcript analyser.

## Commands

```bash
# Run transcript analyser
export OPENROUTER_API_KEY="sk-or-..."
python youtube_transcript_analyzer.py <YouTube-URL>

# Skip screenshots (requires ffmpeg otherwise)
python youtube_transcript_analyzer.py <url> --no-screenshots

# Use a cheaper model
python youtube_transcript_analyzer.py <url> --model google/gemini-flash-1.5

# Custom analyse instructions (inline or from file)
python youtube_transcript_analyzer.py <url> --instructions "extract all stock tickers"
python youtube_transcript_analyzer.py <url> --instructions-file prompts/finance.txt

# Custom report instructions (inline or from file)
python youtube_transcript_analyzer.py <url> --report-instructions "write for retail investors"
python youtube_transcript_analyzer.py <url> --report-instructions-file prompts/report_style.txt

# Both steps customised
python youtube_transcript_analyzer.py <url> \
  --instructions-file prompts/analyse.txt \
  --report-instructions-file prompts/report.txt

# Install deps manually
pip install -r requirements.txt
```

## Architecture

Single-file tool: `youtube_transcript_analyzer.py`

- Auto-installs missing pip packages on first run
- Makes **2 LLM calls** per video: classify (informative vs interview) → analyse
- Screenshots taken via `yt-dlp` + `ffmpeg` at key timestamps; silently skipped if ffmpeg absent
- Output: `output/<Video Title>/` with `transcript.txt`, `analysis.json`, `wordcloud.png`, `report.md`, `screenshots/`

## Environment

- `OPENROUTER_API_KEY` — required (get from openrouter.ai/keys)
- Default model: `anthropic/claude-3.5-sonnet`
- Transcript capped at 90,000 chars (~22k tokens)

## Key Gotchas

- **Screenshots require `ffmpeg` on PATH** — `sudo apt install ffmpeg` on WSL/Ubuntu. Without it, screenshots are skipped silently but the rest works fine.
- **`--no-screenshots` flag** bypasses screenshot capture entirely; use this if ffmpeg is not installed or screenshots are causing failures.
- **LLM does NOT process screenshots** — screenshots are taken at timestamps extracted from the transcript analysis; they are embedded in the markdown report for the human reader, not fed to the LLM.
- Hindi word cloud may render as boxes on systems without a Devanagari font — analysis is unaffected.
- Transcripts require the YouTube video to have captions enabled.

## Writing Style (SANGRAHAK_SYSTEM_PROMPT)

The LLM prompt instructs output in the style of "Markets by Zerodha — Daily Brief":
- Hook → Context → Mechanism → Insight → Implication structure
- Calm, curious, slightly opinionated tone
- Short paragraphs, explain WHY not just WHAT
- No bullet-point summaries, no corporate tone

## Planned Modules

`meeting`, `blog`, `monitor`, `compare`, `summarize` — not yet implemented.
