# Sangrahak — संग्राहक
### Intelligence Gatherer

> Gather. Analyse. Understand.

Sangrahak is a modular research utility toolkit that collects intelligence from any source —
videos, meetings, blogs, competitor content — and turns raw data into structured insight.

Each tool in the toolkit works standalone and shares a common output format (markdown report +
JSON + word cloud), powered by any LLM via [OpenRouter](https://openrouter.ai).

---

## Toolkit Roadmap

| Module | Status | Description |
|---|---|---|
| `transcript` | **Live** | YouTube video analyser — informative & interview |
| `meeting` | Planned | Meeting recorder/notes analyser — extract action items, decisions, summaries |
| `blog` | Planned | Blog & article extractor — summarise, track topics, extract insights |
| `monitor` | Planned | Competitor intelligence — watch blogs, channels, publications for changes |
| `compare` | Planned | Compare multiple sources side by side — spot gaps, contradictions, trends |
| `summarize` | Planned | Drop any document (PDF, URL, text) — get structured summary + key points |

---

## Current Tool — `transcript`

Analyses YouTube videos in **Hindi and English**. Auto-detects whether it's an informative
video or an interview/podcast, then extracts structured insights, takes screenshots at key
moments, generates a word cloud, and produces a full markdown report.

### What it extracts

| | Informative videos | Interview / Podcast |
|---|---|---|
| Key topics with importance level | ✓ | — |
| Key insights with timestamps | ✓ | ✓ |
| Q&A extraction | — | ✓ |
| Notable quotes | — | ✓ |
| Screenshots at key moments | ✓ | ✓ |
| Word cloud | ✓ | ✓ |
| Summary | ✓ | ✓ |
| Takeaways | ✓ | ✓ |

**Informative** — StudyIQ IAS, Zerodha Markets, UPSC prep, explainers, lectures
**Interview** — India AI Summit, podcasts, panel discussions, fireside chats

---

## Setup

### 1. Install dependencies

All packages auto-install on first run. To install manually:

```bash
pip install -r requirements.txt
```

### 2. ffmpeg (for screenshots)

Screenshots are silently skipped if ffmpeg is not installed.

```bash
# Ubuntu / Debian / WSL
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows — https://ffmpeg.org/download.html
```

### 3. OpenRouter API key

Get a free key at **https://openrouter.ai/keys**

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

---

## Usage

### `transcript` — YouTube analyser

```bash
python youtube_transcript_analyzer.py <YouTube-URL> [options]
```

#### Options

| Flag | Default | Description |
|---|---|---|
| `--api-key` | `$OPENROUTER_API_KEY` | OpenRouter API key |
| `--model` | `anthropic/claude-3.5-sonnet` | Any model on OpenRouter |
| `--output-dir` | `output` | Base folder for results |
| `--no-screenshots` | off | Skip screenshot capture |
| `--max-screenshots` | `8` | Max screenshots per video |

#### Examples

```bash
# Informative video (StudyIQ)
python youtube_transcript_analyzer.py https://www.youtube.com/watch?v=rNmceyZBH1M

# Interview / podcast (India AI Summit)
python youtube_transcript_analyzer.py https://www.youtube.com/watch?v=3UB5kRjmDa4

# Use a cheaper model for bulk processing
python youtube_transcript_analyzer.py <url> --model google/gemini-flash-1.5

# Skip screenshots
python youtube_transcript_analyzer.py <url> --no-screenshots
```

#### Output

Results are saved under `output/<Video Title>/`:

```
output/
  India releases first anti-terror policy PRAHAAR .../
    transcript.txt      — raw transcript with [HH:MM:SS] timestamps
    analysis.json       — structured analysis (JSON)
    wordcloud.png       — word cloud image
    report.md           — full markdown report with embedded screenshots
    screenshots/
      shot_01_00-02-15.jpg
      shot_02_00-07-43.jpg
```

---

## Cost per video (`transcript`)

The script makes **2 LLM API calls** per video (classify + analyse).

| Video length | `claude-3.5-sonnet` | `claude-3-haiku` | `gemini-flash-1.5` |
|---|---|---|---|
| 10 min | ~$0.029 | ~$0.002 | ~$0.001 |
| 20 min | ~$0.035 | ~$0.003 | ~$0.001 |
| 40 min | ~$0.047 | ~$0.004 | ~$0.001 |
| 60 min | ~$0.059 | ~$0.005 | ~$0.001 |
| 2 hrs+ (cap) | ~$0.090 | ~$0.008 | ~$0.002 |

Transcript is capped at 90,000 characters (~22k tokens) so cost never grows beyond the
"2 hrs+" row.

---

## Supported models

Any model on [openrouter.ai/models](https://openrouter.ai/models) works. Recommended:

| Model | `--model` value | Best for |
|---|---|---|
| Claude 3.5 Sonnet | `anthropic/claude-3.5-sonnet` | Best quality (default) |
| Claude 3 Haiku | `anthropic/claude-3-haiku` | Fast, low cost |
| Gemini Flash 1.5 | `google/gemini-flash-1.5` | Bulk / cheapest |
| GPT-4o | `openai/gpt-4o` | Alternative |

---

## Transcript language support

Priority order when fetching transcripts:

1. Manually created English (`en`, `en-IN`)
2. Manually created Hindi (`hi`, `hi-IN`)
3. Auto-generated English
4. Auto-generated Hindi
5. Any available language

Hindi stop words are included in the word cloud filter so common filler words
don't dominate the visualisation.

---

## Coming soon

### `meeting` — Meeting Intelligence
Extract structured intelligence from meeting recordings or notes.
- Action items with owners and deadlines
- Decisions made
- Open questions
- Participant summaries
- Full meeting summary

### `blog` — Blog & Article Extractor
Feed any blog URL or RSS feed and get:
- Key arguments and claims
- Topic extraction and tagging
- Author stance and tone
- Structured summary

### `monitor` — Competitor Intelligence
Set up watches on competitor blogs, YouTube channels, publications.
- New content alerts
- Topic trend tracking
- Side-by-side insight comparison
- Weekly digest report

---

## Limitations

- `transcript` requires a YouTube video with transcripts enabled
- Screenshots require `ffmpeg` on PATH — skipped gracefully otherwise
- Hindi word cloud may render Devanagari as boxes on systems without a Hindi font
  (analysis and report are unaffected)
- Transcripts are trimmed (head + tail) for videos over ~2 hours to stay within
  model context limits
