# Sangrahak — संग्राहक
### Intelligence Gatherer

> Gather. Analyse. Understand.

Sangrahak is a modular research utility toolkit that collects intelligence from any source —
videos, meetings, blogs, competitor content — and turns raw data into structured insight.

Each tool in the toolkit works standalone and writes structured outputs such as markdown reports,
JSON metadata, media folders, OCR text, and word clouds where relevant. LLM-backed steps are
powered by [OpenRouter](https://openrouter.ai).

---

## Toolkit Roadmap

| Module | Status | Description |
|---|---|---|
| `transcript` | **Live** | YouTube video analyser — informative & interview |
| `instagram` | **Live** | Instagram post/reel extractor — media, captions, metadata, OCR, and slide crops |
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

## Current Tool — `instagram`

Extracts media and text from public Instagram posts, reels, and IGTV URLs. It downloads images
and videos, saves the caption and metadata, extracts hashtags and mentions, and can process every
carousel slide for embedded text and cropped picture regions.

The extractor accepts either a full Instagram URL or a raw shortcode. For posts that are private,
age-gated, or rate-limited, pass `--login <username>` to use an Instaloader session.

### What it extracts

| Output | Description |
|---|---|
| `caption.txt` | Raw caption text |
| `metadata.json` | Account, shortcode, date, type, likes, comments, media count, hashtags, mentions |
| `media/` | Downloaded images, videos, and thumbnails |
| `report.md` | Slide-ordered report with original slides, extracted text, and cropped pictures side by side |
| `ocr.json` | Structured OCR output when `--ocr` or `--vision-ocr` is used |
| `ocr_text.txt` | Plain text OCR output when OCR is used |
| `slides/slide_text.md` | OCR text organized by carousel slide when `--ocr` or `--vision-ocr` is used |
| `slides/crops/` | Cropped embedded picture regions from each slide when `--crop-pictures` is used |
| `picture_crops.json` | Crop metadata when `--crop-pictures` is used |

### Instagram extraction method

The Instagram extractor is a media-first pipeline. It does not ask the LLM to understand the post
unless `--vision-ocr` is explicitly enabled.

1. Accept a full Instagram URL or raw shortcode, then extract the shortcode from `/p/`, `/reel/`,
   or `/tv/`.
2. Create `output/instagram/<shortcode>/` with `media/` and `slides/` folders.
3. Configure Instaloader. If `--login <username>` is passed, it first tries a saved session, then
   falls back to the password from `INSTAGRAM_PASSWORD` or an interactive prompt.
4. Fetch the post object and metadata with Instaloader.
5. Download images, videos, and video thumbnails into `media/`. Use `--no-videos` when only still
   carousel slides are needed.
6. Save `caption.txt` and `metadata.json`, including account, date, post type, likes, comments,
   media count, hashtags, mentions, and source URL.
7. Optionally run exactly one OCR mode over downloaded image files:
   - `--ocr` uses local Tesseract through `pytesseract`.
   - `--vision-ocr` sends each downloaded image slide to an OpenRouter vision model.
8. Optionally crop likely embedded picture regions from each slide with `--crop-pictures`. The
   auto cropper uses OpenCV to detect large non-background regions and writes crops to
   `slides/crops/`. If the carousel has a repeated layout, `--manual-crop X1,Y1,X2,Y2` applies one
   fixed crop box to every slide.
9. Write `report.md`, keeping the same order as the Instagram carousel and showing each original
   slide beside its extracted text and cropped picture regions.

### Recommended Instagram workflows

For designed carousel posts where each slide contains text and one or more embedded pictures,
use vision OCR plus crop detection:

```bash
python instagram_post_extractor.py <url> --vision-ocr --crop-pictures --no-videos
```

This produces:

- `slides/slide_text.md` — text from each slide in slide order
- `slides/crops/*.jpg` — cropped embedded portraits, paintings, screenshots, or other picture regions
- `picture_crops.json` — source slide and bounding box for every crop
- `report.md` — caption, media, OCR text, and crop previews in one place

For low-cost local extraction, install Tesseract and use:

```bash
python instagram_post_extractor.py <url> --ocr --ocr-lang eng+hin --crop-pictures
```

Use `--manual-crop` only when every slide has the same layout and the auto cropper needs help.
Coordinates can be pixels or percentages:

```bash
python instagram_post_extractor.py <url> --crop-pictures --manual-crop 45%,18%,92%,82%
```

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

### 3. Tesseract (for local Instagram OCR)

`instagram --ocr` uses Tesseract. If it is not installed, OCR is skipped and the rest of the
Instagram extraction still works.

```bash
# Ubuntu / Debian / WSL
sudo apt install tesseract-ocr

# Hindi OCR support, optional
sudo apt install tesseract-ocr-hin

# macOS
brew install tesseract

# Windows — https://github.com/UB-Mannheim/tesseract/wiki
```

### 4. OpenRouter API key

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
| `--model` | `google/gemini-2.0-flash-001` | Model for classify + analyse steps |
| `--report-model` | `anthropic/claude-3.5-sonnet` | Model for Sangrahak report generation |
| `--output-dir` | `output` | Base folder for results |
| `--no-screenshots` | off | Skip screenshot capture |
| `--max-screenshots` | `8` | Max screenshots per video |
| `--instructions` | — | Extra extraction instructions for the analyse step |
| `--instructions-file` | — | Path to a text file with analyse-step instructions (merged with `--instructions`) |
| `--report-instructions` | — | Extra instructions for the Sangrahak report generation step |
| `--report-instructions-file` | — | Path to a text file with report-step instructions (merged with `--report-instructions`) |

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

# Custom analyse instructions (inline)
python youtube_transcript_analyzer.py <url> --instructions "extract all stock tickers mentioned"

# Custom analyse instructions from a file (multi-line, reusable)
python youtube_transcript_analyzer.py <url> --instructions-file prompts/finance.txt

# Custom report instructions — shape the Sangrahak narrative
python youtube_transcript_analyzer.py <url> --report-instructions "write for a retail investor, avoid jargon"

# Both steps customised
python youtube_transcript_analyzer.py <url> \
  --instructions "extract all stock tickers" \
  --report-instructions-file prompts/report_style.txt
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

### `instagram` — Instagram post/reel extractor

```bash
python instagram_post_extractor.py <Instagram-URL-or-shortcode> [options]
```

#### Options

| Flag | Default | Description |
|---|---|---|
| `--output-dir` | `output/instagram` | Base folder for Instagram results |
| `--login USERNAME` | off | Log in for private, restricted, or rate-limited posts |
| `--session-file` | Instaloader default | Custom Instaloader session file path |
| `--password-env` | `INSTAGRAM_PASSWORD` | Environment variable used for login password |
| `--no-videos` | off | Skip video downloads |
| `--comments` | off | Ask Instaloader to download comments metadata |
| `--ocr` | off | Run local Tesseract OCR on downloaded images |
| `--ocr-lang` | `eng` | Tesseract language code, e.g. `eng` or `eng+hin` |
| `--vision-ocr` | off | Extract slide text with a vision LLM via OpenRouter |
| `--api-key` | `$OPENROUTER_API_KEY` | OpenRouter API key for `--vision-ocr` |
| `--vision-model` | `google/gemini-2.0-flash-001` | Vision model for OpenRouter OCR |
| `--crop-pictures` | off | Crop likely embedded picture regions from each slide |
| `--manual-crop` | auto | Fixed crop box for every slide, e.g. `45%,18%,92%,82%` |
| `--min-crop-area` | `0.015` | Minimum auto-detected crop area as a slide fraction |

#### Examples

```bash
# Extract a public post
python instagram_post_extractor.py https://www.instagram.com/p/DYv7LNajDcX/

# Extract a reel
python instagram_post_extractor.py https://www.instagram.com/reel/<shortcode>/

# Use login when Instagram blocks anonymous access
python instagram_post_extractor.py <url> --login your_instagram_username

# Local OCR for carousel slides
python instagram_post_extractor.py <url> --ocr --ocr-lang eng+hin

# Vision OCR through OpenRouter
python instagram_post_extractor.py <url> --vision-ocr

# Extract text and crop embedded pictures from all carousel slides
python instagram_post_extractor.py <url> --vision-ocr --crop-pictures --no-videos

# Use one fixed crop region for all slides
python instagram_post_extractor.py <url> --crop-pictures --manual-crop 45%,18%,92%,82%
```

#### Output

Results are saved under `output/instagram/<shortcode>/`:

```
output/
  instagram/
    DYv7LNajDcX/
      caption.txt
      metadata.json
      report.md
      media/
        *.jpg
        *.mp4
      ocr.json          — only with --ocr or --vision-ocr
      ocr_text.txt      — only with --ocr or --vision-ocr
      picture_crops.json — only with --crop-pictures
      slides/
        slide_text.md    — only with --ocr or --vision-ocr
        crops/
          slide_01_crop_01.jpg — only with --crop-pictures
```

### `extract_urls` — Instagram profile URL extractor

Extracts all accessible post/reel/TV links from an Instagram profile and saves them to CSV.

```bash
python extract_urls.py <Instagram-profile-URL-or-username> [options]
```

#### Options

| Flag | Default | Description |
|---|---|---|
| `--output` | `hindu_saints_posts.csv` | CSV output file |
| `--method` | `auto` | `auto`, `instaloader`, or `browser` |
| `--login USERNAME` | off | Instagram username for Instaloader login |
| `--session-file` | Instaloader default | Custom Instaloader session file path |
| `--password-env` | `INSTAGRAM_PASSWORD` | Environment variable used for login password |
| `--limit` | no limit | Stop after this many posts |
| `--manual-login` | off | Pause browser mode so you can log in manually |
| `--headless` | off | Run browser mode without a visible Chrome window |
| `--initial-wait` | `5.0` | Seconds to wait after opening the profile |
| `--scroll-wait` | `2.5` | Seconds to wait after each scroll |
| `--idle-rounds` | `8` | Stop after this many scrolls with no new links |
| `--max-scrolls` | `500` | Maximum browser scroll attempts |

#### Examples

```bash
# Default profile from the script
python extract_urls.py

# Extract a specific profile
python extract_urls.py hindu_saints

# Best first attempt for full extraction
python extract_urls.py hindu_saints --login your_instagram_username

# Browser fallback when Instagram blocks Instaloader or only returns the first grid
python extract_urls.py hindu_saints --method browser --manual-login

# Save to a custom CSV
python extract_urls.py hindu_saints --output hindu_saints_posts.csv
```

#### Output

The CSV contains:

```csv
index,url,shortcode,date_utc,typename,is_video
```

Instagram may block anonymous GraphQL requests and Instaloader can then report a misleading
`Profile ... does not exist` error. In that case, use `--login` first. If it still fails or only
collects the first ~12 visible posts, use browser mode with `--manual-login`, log in inside the
Chrome window, then press Enter in the terminal so the script can continue scrolling.

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

## Cost per Instagram post

Plain Instagram extraction and `--crop-pictures` do not use an LLM. They only download media and
run local image processing.

`--vision-ocr` makes **1 OpenRouter vision call per downloaded image slide**. A 9-slide carousel
therefore makes 9 vision OCR calls. Use `--ocr` instead when you want fully local OCR through
Tesseract.

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
- `instagram` works best with public posts; private, restricted, or rate-limited posts may require `--login`
- Instagram OCR only runs on downloaded image files, not videos
- `--crop-pictures` is heuristic: it works well for designed slides with clear photo/portrait
  regions, but unusual layouts may need `--manual-crop`
- `--vision-ocr` requires `OPENROUTER_API_KEY` or `--api-key`
- `--ocr` requires Tesseract on PATH; if missing, OCR is skipped gracefully
- Screenshots require `ffmpeg` on PATH — skipped gracefully otherwise
- Hindi word cloud may render Devanagari as boxes on systems without a Hindi font
  (analysis and report are unaffected)
- Transcripts are trimmed (head + tail) for videos over ~2 hours to stay within
  model context limits

## TODO
1. Create hashtags for each file can be kept in the json format and text as line in the end of the report.
2. Can process multiple video and tool use for research starting with //

## PROCESS_TODO
* https://www.youtube.com/watch?v=W-wfD5xfcXo
* https://www.youtube.com/watch?v=QmyDrYW51U8
