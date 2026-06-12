#!/usr/bin/env python3
"""
YouTube Transcript Analyzer
============================
Fetches Hindi/English transcripts, auto-classifies video type,
extracts insights/Q&A with timestamps, takes screenshots, and
generates a word cloud + markdown report.

Usage:
    python youtube_transcript_analyzer.py <YouTube-URL> [options]

    export OPENROUTER_API_KEY="sk-or-..."
    python youtube_transcript_analyzer.py https://www.youtube.com/watch?v=rNmceyZBH1M
    python youtube_transcript_analyzer.py https://youtu.be/3UB5kRjmDa4 --no-screenshots
    python youtube_transcript_analyzer.py <url> --model google/gemini-flash-1.5
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs


# ─── Auto-install missing packages ────────────────────────────────────────────

def _pip_install(*packages: str) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

_DEPS = {
    "youtube_transcript_api": "youtube-transcript-api",
    "openai":                 "openai",
    "wordcloud":              "wordcloud",
    "matplotlib":             "matplotlib",
    "yt_dlp":                 "yt-dlp",
    "PIL":                    "Pillow",
}

_missing = [pkg for mod, pkg in _DEPS.items() if not __import__("importlib").util.find_spec(mod)]
if _missing:
    print(f"Installing missing packages: {', '.join(_missing)} ...")
    _pip_install(*_missing)


# ─── Imports (after auto-install) ─────────────────────────────────────────────

from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
)
from openai import OpenAI
from wordcloud import WordCloud
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yt_dlp


# ─── Constants ────────────────────────────────────────────────────────────────

OPENROUTER_BASE_URL   = "https://openrouter.ai/api/v1"
DEFAULT_ANALYSE_MODEL = "google/gemini-2.5-flash"
DEFAULT_REPORT_MODEL  = "anthropic/claude-sonnet-4.6"
DEFAULT_MODEL         = DEFAULT_ANALYSE_MODEL  # legacy --model flag default
TRANSCRIPT_CHAR_LIMIT = 90_000   # ~22k tokens
MAX_SCREENSHOTS       = 8

STOPWORDS = {
    # English filler
    "the","a","an","and","or","but","in","on","at","to","for","of","with","this",
    "that","is","are","was","were","be","been","being","have","has","had","do",
    "does","did","will","would","could","should","may","might","shall","can",
    "i","you","he","she","we","they","it","its","their","our","my","your","his",
    "her","us","him","them","so","if","as","by","from","up","about","into",
    "through","during","also","more","than","then","now","very","just","like",
    "what","when","where","which","who","how","all","said","not","no","yes",
    "really","actually","basically","okay","right","gonna","wanna","gotta","kind",
    "things","something","anything","everything","thing","lot","bit","much","many",
    "some","any","even","still","back","well","one","two","three","get","got","go",
    "going","come","know","think","see","look","need","want","use","make","take",
    "give","put","let","say","um","uh","yeah","oh",
    # Hindi filler
    "\u0939\u0948","\u0939\u0948\u0902","\u0925\u093e","\u0925\u0947","\u0925\u0940",
    "\u0939\u094b","\u0939\u094b\u0924\u093e","\u0939\u094b\u0924\u0940","\u0939\u094b\u0924\u0947",
    "\u0915\u0930\u0928\u093e","\u0915\u0930\u0924\u0947","\u0915\u093f\u092f\u093e",
    "\u0915\u0940","\u0915\u0947","\u0915\u093e","\u0915\u094b","\u092e\u0947\u0902",
    "\u0938\u0947","\u092a\u0930","\u0914\u0930","\u092f\u093e","\u0932\u0947\u0915\u093f\u0928",
    "\u0915\u093f","\u091c\u094b","\u092f\u0939","\u0935\u0939","\u0907\u0938","\u0909\u0938",
    "\u090f\u0915","\u092d\u0940","\u0924\u094b","\u0928\u0947","\u0939\u0940","\u0928\u0939\u0940\u0902",
    "\u0915\u094d\u092f\u093e","\u0915\u0948\u0938\u0947","\u0915\u092c","\u0915\u0939\u093e\u0901",
    "\u0915\u094d\u092f\u094b\u0902","\u0905\u092c","\u091c\u092c","\u0924\u092c",
    "\u0939\u092e","\u0906\u092a","\u0935\u0947","\u092e\u0948\u0902",
    "\u092f\u0939\u093e\u0901","\u0935\u0939\u093e\u0901","\u0938\u092c","\u0938\u092d\u0940",
    "\u092c\u0939\u0941\u0924","\u092a\u0939\u0932\u0947","\u092c\u093e\u0926","\u0938\u093e\u0925",
    "\u092c\u093f\u0928\u093e","\u0909\u0902\u092a\u0930","\u0928\u0940\u091a\u0947",
    "\u0905\u0917\u0930","\u091c\u0948\u0938\u0947","\u0907\u0938\u0932\u093f\u090f",
    "\u092a\u0930\u0902\u0924\u0941","\u0915\u094d\u092f\u094b\u0902\u0915\u093f",
}

SANGRAHAK_SYSTEM_PROMPT = """You are an intelligent market analyst and explainer.

Your job is NOT to summarize content.
Your job is to help the reader UNDERSTAND what's really going on.

Adopt the writing style of "Markets by Zerodha — Daily Brief".

## CORE PRINCIPLES (MANDATORY)

1. Do NOT start with raw facts or data.
   Always start with an observation, tension, or curiosity.

2. Focus on:
   - Why things are happening
   - How things work (mechanism)
   - Why it matters

3. Avoid:
   - Jargon (or explain it instantly)
   - Corporate tone
   - Sensationalism
   - Empty summaries

4. Write like:
   A smart operator explaining something over coffee.

Tone:
- Calm
- Curious
- Slightly opinionated
- Clear, not flashy

## WRITING STRUCTURE (STRICT)

For each major topic or section, follow this structure:

1. HOOK
   Start with a curiosity-driven line.

2. CONTEXT
   What happened (brief, no overload)

3. MECHANISM
   Explain step-by-step WHY it happened (A → B → C causal chain)

4. INSIGHT
   What's the deeper story here? What are people missing?

5. IMPLICATION
   Why should the reader care?

## WRITING STYLE RULES

- Use short paragraphs (2-4 lines max)
- One idea per paragraph
- Use analogies where helpful: "Think of it like..."
- Prefer simple language over technical precision
- Add subtle opinions: "This might not be as important as it sounds..."

## ANTI-PATTERNS (STRICTLY AVOID)

- "In this video, the speaker discusses..."
- Bullet-point summaries without explanation
- Long paragraphs
- Data-first explanations
- Generic conclusions

## FINAL CHECK BEFORE OUTPUT

Ask yourself:
- Does this feel like a story or a report?
- Did I explain WHY, not just WHAT?
- Would a smart non-expert understand this easily?

If not, rewrite."""


# ─── URL / ID helpers ──────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    """Parse video ID from any common YouTube URL format."""
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/").split("?")[0]
    qs = parse_qs(parsed.query)
    ids = qs.get("v", [])
    if ids:
        return ids[0]
    raise ValueError(f"Cannot extract video ID from: {url}")


def seconds_to_hms(seconds: float) -> str:
    t = int(seconds)
    h, rem = divmod(t, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ─── Transcript ────────────────────────────────────────────────────────────────

def fetch_transcript(video_id: str) -> tuple[list, str]:
    """
    Returns (segments, language_code).
    Priority: manually-created EN -> HI -> auto-generated EN -> HI -> any.
    Segments are FetchedTranscriptSnippet objects (v1.x): use .text / .start
    """
    priority = ["en", "hi", "en-IN", "hi-IN", "en-US"]
    ytt = YouTubeTranscriptApi()
    try:
        tlist = ytt.list(video_id)
    except TranscriptsDisabled:
        raise RuntimeError("Transcripts are disabled for this video.")

    for lang in priority:
        try:
            t = tlist.find_manually_created_transcript([lang])
            return t.fetch(), t.language_code
        except Exception:
            pass

    for lang in priority:
        try:
            t = tlist.find_generated_transcript([lang])
            return t.fetch(), t.language_code
        except Exception:
            pass

    for t in tlist:
        return t.fetch(), t.language_code

    raise RuntimeError("No transcript found for this video.")


def segments_to_timestamped_text(segments) -> str:
    lines = []
    for seg in segments:
        # v1.x returns FetchedTranscriptSnippet dataclass objects
        start = seg.start if hasattr(seg, "start") else seg["start"]
        text  = seg.text  if hasattr(seg, "text")  else seg["text"]
        ts = seconds_to_hms(start)
        lines.append(f"[{ts}] {text.strip()}")
    return "\n".join(lines)


def trim_transcript(text: str, limit: int = TRANSCRIPT_CHAR_LIMIT) -> tuple[str, bool]:
    """Trim to token-safe length keeping head + tail."""
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.60)
    tail = int(limit * 0.40)
    trimmed = text[:head] + "\n\n[... transcript trimmed for length ...]\n\n" + text[-tail:]
    return trimmed, True


# ─── Video metadata via yt-dlp ─────────────────────────────────────────────────

def fetch_video_metadata(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title":       info.get("title", ""),
        "channel":     info.get("channel", info.get("uploader", "")),
        "description": (info.get("description", "") or "")[:600],
        "duration":    info.get("duration", 0),
        "view_count":  info.get("view_count", 0),
        "upload_date": info.get("upload_date", ""),
    }


# ─── OpenRouter helpers ────────────────────────────────────────────────────────

def _llm(client: OpenAI, prompt: str, system: str = "", model: str = DEFAULT_MODEL, max_tokens: int = 4096) -> str:
    """Send a chat completion request via OpenRouter."""
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system or "You are a senior research analyst. Be precise and concise."},
            {"role": "user",   "content": prompt},
        ],
    )
    return resp.choices[0].message.content


def _parse_json(raw: str) -> dict | list:
    """Strip markdown fences and parse JSON."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ─── Step 1: Classify ──────────────────────────────────────────────────────────

def classify_video(client: OpenAI, metadata: dict, sample: str, model: str) -> str:
    """Returns 'informative' or 'interview'."""
    prompt = f"""Classify this YouTube video as exactly one of: informative | interview

Video Title   : {metadata['title']}
Channel       : {metadata['channel']}
Description   : {metadata['description']}
Transcript (sample):
{sample[:1200]}

Classification rules:
- informative : educational lectures, tutorials, news analysis, market analysis, explainers,
                documentary-style videos (e.g. StudyIQ, Zerodha Markets, UPSC prep channels)
- interview   : podcasts, panel discussions, Q&A sessions, summit talks with multiple speakers,
                fireside chats, conversations (e.g. India AI Summit, podcast shows, talk shows)

Respond with ONE word only: informative OR interview"""
    result = _llm(client, prompt, model=model).strip().lower()
    return "interview" if "interview" in result else "informative"


# ─── Step 2a: Informative analysis ────────────────────────────────────────────

def analyze_informative(client: OpenAI, timestamped_transcript: str, metadata: dict, model: str, instructions: str = "") -> dict:
    trimmed, was_trimmed = trim_transcript(timestamped_transcript)
    note = " (transcript was trimmed due to length)" if was_trimmed else ""

    prompt = f"""Analyze this informative/educational YouTube video transcript{note}.

Video : {metadata['title']}
Channel: {metadata['channel']}

TRANSCRIPT (with [HH:MM:SS] timestamps):
{trimmed}

Return a single JSON object - no markdown, no explanation, raw JSON only:
{{
  "summary": "4-6 sentence executive summary of the entire video",
  "key_topics": [
    {{
      "topic": "Topic name",
      "description": "2-3 sentence explanation",
      "timestamp": "HH:MM:SS",
      "importance": "high | medium | low"
    }}
  ],
  "key_insights": [
    {{
      "insight": "The insight statement (1-2 sentences)",
      "timestamp": "HH:MM:SS",
      "context": "Why this matters"
    }}
  ],
  "important_facts": ["Fact 1", "Fact 2"],
  "takeaways": ["Takeaway 1", "Takeaway 2"],
  "visual_cues": [
    {{
      "timestamp": "HH:MM:SS",
      "description": "What visual is being referenced (e.g. 'chart showing 59% demand increase')"
    }}
  ]
}}

Rules:
- key_topics: 6-10 entries, timestamps must exist in the transcript
- key_insights: 5-8 entries, timestamps must exist in the transcript
- important_facts: 5-8 notable data points or facts
- takeaways: 4-6 actionable or memorable points
- visual_cues: 3-6 entries — timestamps where speaker says "as you can see", "this chart/graph shows", "here's the data", "looking at this", or references a statistic likely displayed on screen. Timestamps must exist in transcript.
- Return ONLY valid JSON"""

    if instructions:
        prompt += f"\n\nAdditional instructions: {instructions}"

    raw = _llm(client, prompt, model=model)
    return _parse_json(raw)


# ─── Step 2b: Interview analysis ──────────────────────────────────────────────

def analyze_interview(client: OpenAI, timestamped_transcript: str, metadata: dict, model: str, instructions: str = "") -> dict:
    trimmed, was_trimmed = trim_transcript(timestamped_transcript)
    note = " (transcript was trimmed due to length)" if was_trimmed else ""

    prompt = f"""Analyze this interview/podcast YouTube video transcript{note}.

Video : {metadata['title']}
Channel: {metadata['channel']}

TRANSCRIPT (with [HH:MM:SS] timestamps):
{trimmed}

Return a single JSON object - no markdown, no explanation, raw JSON only:
{{
  "summary": "4-6 sentence summary covering the key discussion points",
  "participants": ["Participant name/role (if identifiable)"],
  "main_themes": ["Theme 1", "Theme 2"],
  "qa_pairs": [
    {{
      "question": "The question asked (verbatim or paraphrased)",
      "answer": "Concise summary of the answer",
      "timestamp": "HH:MM:SS",
      "insight": "Key takeaway from this exchange"
    }}
  ],
  "notable_quotes": [
    {{
      "quote": "Near-verbatim quote",
      "speaker": "Speaker name or 'Speaker' if unknown",
      "timestamp": "HH:MM:SS"
    }}
  ],
  "key_insights": [
    {{
      "insight": "Insight statement",
      "timestamp": "HH:MM:SS"
    }}
  ],
  "takeaways": ["Takeaway 1", "Takeaway 2"],
  "visual_cues": [
    {{
      "timestamp": "HH:MM:SS",
      "description": "What visual is being referenced (e.g. 'chart showing 59% demand increase')"
    }}
  ]
}}

Rules:
- qa_pairs: 6-10 entries covering the most significant exchanges
- notable_quotes: 4-6 impactful quotes
- key_insights: 5-8 entries
- All timestamps must exist in the transcript
- visual_cues: 3-6 entries — timestamps where speaker says "as you can see", "this chart/graph shows", "here's the data", "looking at this", or references a statistic likely displayed on screen. Timestamps must exist in transcript.
- Return ONLY valid JSON"""

    if instructions:
        prompt += f"\n\nAdditional instructions: {instructions}"

    raw = _llm(client, prompt, model=model)
    return _parse_json(raw)


# ─── Step 2c: Sangrahak styled report ─────────────────────────────────────────

def generate_sangrahak_report(
    client: OpenAI,
    analysis: dict,
    metadata: dict,
    video_url: str,
    lang: str,
    video_type: str,
    output_dir: Path,
    model: str,
    report_instructions: str = "",
) -> str:
    """Generate a Zerodha-style narrative markdown report from structured analysis JSON."""

    deep_dive_instruction = (
        "For each key_topic"
        if video_type == "informative"
        else "For each main_theme (drawing on qa_pairs and key_insights for detail)"
    )

    prompt = f"""Video: {metadata['title']}
Channel: {metadata['channel']}
Type: {video_type}
Language: {lang}
URL: {video_url}
Duration: {seconds_to_hms(metadata['duration'])}

STRUCTURED ANALYSIS (JSON) — use this as your source of truth, do not invent facts:
{json.dumps(analysis, indent=2, ensure_ascii=False)}

Generate a Zerodha-style markdown report using the analysis above.

OUTPUT FORMAT (follow exactly):

### 1. Title
One insight-driven headline (NOT just the video title)

### 2. Metadata table
| Field | Value |
|-------|-------|
rows for Channel, Language, Duration, URL, Analyzed

### 3. Summary
3-5 bullet points — high-signal takeaways only

---

### 4. Deep Dive Sections
{deep_dive_instruction}, write one section:
- Section heading = topic/theme name
- Follow: Hook → Context → Mechanism → Insight → Implication (each as its own short paragraph)

---

### 5. Key Takeaways
4-6 bullet points — actionable or mental-model level insights

---

### 6. Word Cloud
![Word Cloud](wordcloud.png)

Output only the markdown. No preamble, no explanation."""

    if report_instructions:
        prompt += f"\n\nAdditional report instructions: {report_instructions}"

    styled_md = _llm(client, prompt, system=SANGRAHAK_SYSTEM_PROMPT, model=model, max_tokens=8192)

    # Ensure word cloud is present at the end if the LLM omitted it
    if "wordcloud.png" not in styled_md:
        styled_md += "\n\n---\n\n## Word Cloud\n\n![Word Cloud](wordcloud.png)\n"

    # Post-process: inject screenshot images after their matching timestamp markers
    items_with_shots = (
        analysis.get("key_insights", []) + analysis.get("key_topics", [])
        if video_type == "informative"
        else analysis.get("qa_pairs", []) + analysis.get("key_insights", [])
    )
    for item in items_with_shots:
        if "screenshot" not in item:
            continue
        try:
            rel = Path(item["screenshot"]).relative_to(output_dir)
        except ValueError:
            rel = item["screenshot"]
        ts = item.get("timestamp", "")
        if ts and ts in styled_md:
            # Insert the image right after the first occurrence of the timestamp
            styled_md = styled_md.replace(ts, f"{ts}\n\n![Screenshot]({rel})", 1)

    report_path = output_dir / "report.md"
    report_path.write_text(styled_md, encoding="utf-8")
    return str(report_path)


# ─── Step 3: Screenshots ──────────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _get_stream_url(youtube_url: str) -> str:
    """Return a video-only stream URL suitable for screenshot seeking."""
    # Video-only (no audio track): ffmpeg only needs to fetch video segments
    # when seeking, which is much faster than muxed streams for mid-video seeks.
    opts = {
        "format":      "worstvideo[height>=240][ext=mp4]/worstvideo[height>=240]/worstvideo[ext=mp4]/worstvideo",
        "quiet":       True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    # Single-stream result
    if "url" in info:
        return info["url"]

    # Pick video-only stream: no audio codec, has a video codec
    candidates = [
        f for f in info.get("formats", [])
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") in (None, "none")
        and f.get("url")
    ]
    if candidates:
        candidates.sort(key=lambda f: f.get("height", 0) or 0)
        return candidates[0]["url"]

    # Fallback: any format with a video track
    candidates = [
        f for f in info.get("formats", [])
        if f.get("vcodec") not in (None, "none") and f.get("url")
    ]
    if candidates:
        candidates.sort(key=lambda f: f.get("height", 0) or 0)
        return candidates[0]["url"]

    raise RuntimeError("Could not obtain a direct video stream URL.")


def _take_single_screenshot(stream_url: str, hms: str, out_path: str) -> bool:
    """Use ffmpeg to grab one frame at timestamp hms. Returns True on success."""
    cmd = [
        "ffmpeg",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-ss", hms,
        "-i", stream_url,
        "-an",           # no audio — video-only stream, skip audio decoding
        "-vframes", "1",
        "-q:v", "2",
        "-y", out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=45)
        return result.returncode == 0 and Path(out_path).exists()
    except subprocess.TimeoutExpired:
        return False


def _hms_to_sec(hms: str) -> float:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _detect_scene_changes(stream_url: str, center_hms: str, window_sec: int = 30) -> list[str]:
    """Detect scene-change timestamps within a window around center_hms.
    Returns HH:MM:SS strings sorted closest-first to center."""
    def sec_to_hms(s):
        s = max(0.0, s)
        h, m = int(s//3600), int((s%3600)//60)
        return f"{h:02d}:{m:02d}:{s%60:06.3f}"

    center_sec = _hms_to_sec(center_hms)
    start_sec  = max(0.0, center_sec - window_sec / 2)
    cmd = [
        "ffmpeg", "-reconnect", "1", "-reconnect_streamed", "1",
        "-ss", sec_to_hms(start_sec), "-t", str(window_sec),
        "-i", stream_url, "-an",
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-vsync", "vfr", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
    except subprocess.TimeoutExpired:
        return []
    times = []
    for line in result.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            times.append(start_sec + float(m.group(1)))
    times.sort(key=lambda s: abs(s - center_sec))
    return [sec_to_hms(s) for s in times]


def _snap_to_scene(stream_url: str, target_hms: str, out_path: str) -> bool:
    """Take screenshot at nearest scene change to target_hms, fallback to target."""
    for ts in _detect_scene_changes(stream_url, target_hms)[:3]:
        if _take_single_screenshot(stream_url, ts, out_path):
            return True
    return _take_single_screenshot(stream_url, target_hms, out_path)


def take_screenshots(
    youtube_url: str,
    items: list[dict],
    output_dir: Path,
    max_shots: int = MAX_SCREENSHOTS,
    visual_cues: list[dict] | None = None,
) -> list[dict]:
    """
    For each item with a 'timestamp' key, take a screenshot.
    visual_cues items are prioritised; scene-change snapping is used for each shot.
    Returns items with an added 'screenshot' key (path) on success.
    """
    if not _ffmpeg_available():
        print("  [!] ffmpeg not found on PATH - screenshots skipped.")
        print("      Install ffmpeg: https://ffmpeg.org/download.html")
        return items

    print("  Getting video stream URL ...", end=" ", flush=True)
    try:
        stream_url = _get_stream_url(youtube_url)
        print("OK")
    except Exception as exc:
        print(f"FAILED ({exc})")
        return items

    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(exist_ok=True)

    # visual_cues first; remaining items fill the quota
    cue_set    = {c.get("timestamp") for c in (visual_cues or [])}
    candidates = list(visual_cues or []) + [i for i in items if i.get("timestamp") not in cue_set]

    # Build index to attach screenshots back onto original items
    ts_to_idx = {item.get("timestamp"): idx for idx, item in enumerate(items) if item.get("timestamp")}
    enriched  = list(items)

    taken = 0
    for shot_num, cand in enumerate(candidates):
        if taken >= max_shots:
            break
        ts = cand.get("timestamp", "")
        if not ts:
            continue
        fname = f"shot_{shot_num+1:02d}_{ts.replace(':', '-')}.jpg"
        fpath = shots_dir / fname
        print(f"  [screenshot] @ {ts} ...", end=" ", flush=True)
        if _snap_to_scene(stream_url, ts, str(fpath)):
            print("OK")
            taken += 1
            if ts in ts_to_idx:
                idx = ts_to_idx[ts]
                enriched[idx] = {**enriched[idx], "screenshot": str(fpath)}
            elif items:
                # visual_cue timestamp — attach to nearest item by time
                try:
                    cue_sec = _hms_to_sec(ts)
                    nearest = min(
                        range(len(enriched)),
                        key=lambda i: abs(_hms_to_sec(enriched[i].get("timestamp", "00:00:00")) - cue_sec)
                    )
                    if "screenshot" not in enriched[nearest]:
                        enriched[nearest] = {**enriched[nearest], "screenshot": str(fpath)}
                except Exception:
                    pass
        else:
            print("FAILED")

    print(f"  {taken} screenshot(s) saved -> {shots_dir}")
    return enriched


# ─── Step 4: Word cloud ────────────────────────────────────────────────────────

def generate_wordcloud(plain_text: str, output_path: str, font_path: str | None = None) -> str:
    # Auto-detect a Hindi-capable font if none provided
    if font_path is None:
        candidates = [
            "/mnt/c/Windows/Fonts/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            str(Path(__file__).parent / "NotoSansDevanagari-Regular.ttf"),
        ]
        for c in candidates:
            if Path(c).exists():
                font_path = c
                break
    wc = WordCloud(
        width=1400,
        height=700,
        background_color="white",
        stopwords=STOPWORDS,
        max_words=150,
        colormap="viridis",
        collocations=True,
        prefer_horizontal=0.85,
        font_path=font_path,
    )
    wc.generate(plain_text)

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ─── Step 5: Reports ──────────────────────────────────────────────────────────

def _screenshot_md(item: dict, output_dir: Path) -> str:
    if "screenshot" not in item:
        return ""
    try:
        rel = Path(item["screenshot"]).relative_to(output_dir)
        return f"\n\n![Screenshot]({rel})"
    except ValueError:
        return f"\n\n![Screenshot]({item['screenshot']})"


def build_report_informative(
    analysis: dict, metadata: dict, output_dir: Path, lang: str, video_url: str
) -> str:
    lines = [
        f"# {metadata['title']}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Channel** | {metadata['channel']} |",
        f"| **Language** | `{lang}` |",
        f"| **Duration** | {seconds_to_hms(metadata['duration'])} |",
        f"| **URL** | {video_url} |",
        f"| **Analyzed** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        "",
        "---",
        "",
        "## Summary",
        "",
        analysis.get("summary", ""),
        "",
        "---",
        "",
        "## Key Topics",
    ]

    for i, t in enumerate(analysis.get("key_topics", []), 1):
        badge = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}.get(
            t.get("importance", "medium"), ""
        )
        lines += [
            "",
            f"### {i}. {t.get('topic', '')}  `[{t.get('timestamp', '')}]` {badge}",
            "",
            t.get("description", ""),
            _screenshot_md(t, output_dir),
        ]

    lines += ["", "---", "", "## Key Insights", ""]
    for i, ins in enumerate(analysis.get("key_insights", []), 1):
        lines += [
            f"### Insight {i}  `[{ins.get('timestamp', '')}]`",
            "",
            f"> {ins.get('insight', '')}",
            "",
            f"*{ins.get('context', '')}*",
            _screenshot_md(ins, output_dir),
            "",
        ]

    lines += ["---", "", "## Important Facts", ""]
    for f in analysis.get("important_facts", []):
        lines.append(f"- {f}")

    lines += ["", "## Takeaways", ""]
    for t in analysis.get("takeaways", []):
        lines.append(f"- {t}")

    lines += ["", "---", "", "## Word Cloud", "", "![Word Cloud](wordcloud.png)", ""]

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


def build_report_interview(
    analysis: dict, metadata: dict, output_dir: Path, lang: str, video_url: str
) -> str:
    lines = [
        f"# {metadata['title']}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Channel** | {metadata['channel']} |",
        f"| **Type** | Interview / Podcast |",
        f"| **Language** | `{lang}` |",
        f"| **Duration** | {seconds_to_hms(metadata['duration'])} |",
        f"| **URL** | {video_url} |",
        f"| **Analyzed** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        "",
        "---",
        "",
        "## Summary",
        "",
        analysis.get("summary", ""),
        "",
    ]

    participants = analysis.get("participants", [])
    if participants:
        lines += ["## Participants", ""]
        for p in participants:
            lines.append(f"- {p}")
        lines.append("")

    lines += ["## Main Themes", ""]
    for t in analysis.get("main_themes", []):
        lines.append(f"- {t}")

    lines += ["", "---", "", "## Q&A Highlights", ""]
    for i, qa in enumerate(analysis.get("qa_pairs", []), 1):
        lines += [
            f"### Q{i}.  `[{qa.get('timestamp', '')}]`",
            "",
            f"**Q:** {qa.get('question', '')}",
            "",
            f"**A:** {qa.get('answer', '')}",
            "",
            f"> **Insight:** {qa.get('insight', '')}",
            _screenshot_md(qa, output_dir),
            "",
        ]

    lines += ["---", "", "## Notable Quotes", ""]
    for q in analysis.get("notable_quotes", []):
        lines += [
            f'> "{q.get("quote", "")}"',
            "",
            f'-- *{q.get("speaker", "Speaker")}*  `[{q.get("timestamp", "")}]`',
            "",
        ]

    lines += ["---", "", "## Key Insights", ""]
    for i, ins in enumerate(analysis.get("key_insights", []), 1):
        lines += [
            f"**{i}.** {ins.get('insight', '')}  `[{ins.get('timestamp', '')}]`",
            _screenshot_md(ins, output_dir),
            "",
        ]

    lines += ["## Takeaways", ""]
    for t in analysis.get("takeaways", []):
        lines.append(f"- {t}")

    lines += ["", "---", "", "## Word Cloud", "", "![Word Cloud](wordcloud.png)", ""]

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube Transcript Analyzer -- informative & interview videos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="Full YouTube video URL")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key (default: $OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_ANALYSE_MODEL,
        help=f"Model for classify + analyse steps (default: {DEFAULT_ANALYSE_MODEL})",
    )
    parser.add_argument(
        "--report-model",
        default=DEFAULT_REPORT_MODEL,
        help=f"Model for report generation (default: {DEFAULT_REPORT_MODEL})",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Base output directory (default: output/<video_id>/)",
    )
    parser.add_argument(
        "--no-screenshots",
        action="store_true",
        help="Skip screenshot capture (ffmpeg not required)",
    )
    parser.add_argument(
        "--max-screenshots",
        type=int,
        default=MAX_SCREENSHOTS,
        help=f"Max screenshots to capture (default: {MAX_SCREENSHOTS})",
    )
    parser.add_argument(
        "--instructions",
        default="",
        metavar="TEXT",
        help="Extra extraction instructions for the analyse step "
             "(e.g. \"extract all stock tickers mentioned\")",
    )
    parser.add_argument(
        "--instructions-file",
        default="",
        metavar="PATH",
        help="Path to a text file with analyse-step instructions (merged with --instructions)",
    )
    parser.add_argument(
        "--report-instructions",
        default="",
        metavar="TEXT",
        help="Extra instructions for the Sangrahak report generation step",
    )
    parser.add_argument(
        "--report-instructions-file",
        default="",
        metavar="PATH",
        help="Path to a text file with report-step instructions (merged with --report-instructions)",
    )
    args = parser.parse_args()

    def _load_instructions(inline: str, fpath: str) -> str:
        parts = []
        if fpath:
            try:
                parts.append(Path(fpath).read_text(encoding="utf-8").strip())
            except OSError as e:
                sys.exit(f"ERROR: Cannot read instructions file '{fpath}': {e}")
        if inline:
            parts.append(inline.strip())
        return "\n\n".join(parts)

    analyse_instructions = _load_instructions(args.instructions, args.instructions_file)
    report_instructions  = _load_instructions(args.report_instructions, args.report_instructions_file)

    if not args.api_key:
        sys.exit(
            "ERROR: OpenRouter API key required.\n"
            "  Set OPENROUTER_API_KEY environment variable, or pass --api-key <key>\n"
            "  Get a key at: https://openrouter.ai/keys"
        )

    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=args.api_key,
    )

    SEP = "-" * 62
    print(f"\n{SEP}")
    print("  YouTube Transcript Analyzer")
    print(f"  Analyse model : {args.model}")
    print(f"  Report model  : {args.report_model}")
    print(SEP)

    # ── 1. Video metadata ────────────────────────────────────────
    print("\n[1/7] Fetching video metadata ...")
    video_id = extract_video_id(args.url)
    metadata = fetch_video_metadata(args.url)
    print(f"      Title   : {metadata['title']}")
    print(f"      Channel : {metadata['channel']}")
    print(f"      Duration: {seconds_to_hms(metadata['duration'])}")

    # ── 3. Output directory (needed before transcript check) ─────
    safe_title = re.sub(r'[\\/:*?"<>|]', '', metadata['title']).strip()
    safe_title = re.sub(r'\s+', ' ', safe_title)[:100]
    folder_name = safe_title or video_id
    out_dir = Path(args.output_dir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[3/7] Output -> {out_dir.resolve()}")

    transcript_file = out_dir / "transcript.txt"
    metadata_file   = out_dir / "metadata.json"
    analysis_file   = out_dir / "analysis.json"

    # ── 2. Transcript ────────────────────────────────────────────
    if transcript_file.exists():
        print("\n[2/7] Transcript cached — loading from file ...")
        timestamped_text = transcript_file.read_text(encoding="utf-8")
        plain_text = " ".join(
            line.split("] ", 1)[1]
            for line in timestamped_text.splitlines()
            if "] " in line
        )
        cached_meta = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.exists() else {}
        lang = cached_meta.get("lang", "en")
        print(f"      {len(timestamped_text):,} chars  |  lang: {lang}")
    else:
        print("\n[2/7] Fetching transcript ...")
        segments, lang = fetch_transcript(video_id)
        plain_text       = " ".join((s.text if hasattr(s, "text") else s["text"]) for s in segments)
        timestamped_text = segments_to_timestamped_text(segments)
        print(f"      Language: {lang}  |  Segments: {len(segments)}  |  "
              f"Chars: {len(plain_text):,}")
        transcript_file.write_text(timestamped_text, encoding="utf-8")
        print(f"      transcript.txt saved")
        metadata_file.write_text(
            json.dumps({**metadata, "lang": lang}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── 4+5. Classify + Analyse ──────────────────────────────────
    if analysis_file.exists():
        print("\n[4/7] Classify skipped — analysis.json cached.")
        print("\n[5/7] Analyse skipped — analysis.json cached.")
        analysis   = json.loads(analysis_file.read_text(encoding="utf-8"))
        video_type = analysis.get("video_type", "informative")
        print(f"      -> {video_type.upper()} (from cache)")
    else:
        print("\n[4/7] Classifying video type ...")
        video_type = classify_video(client, metadata, timestamped_text, args.model)
        print(f"      -> {video_type.upper()}")

        print("\n[5/7] Analysing transcript ...")
        if video_type == "informative":
            analysis = analyze_informative(client, timestamped_text, metadata, args.model, analyse_instructions)
            print(f"      Topics: {len(analysis.get('key_topics', []))}  |  "
                  f"Insights: {len(analysis.get('key_insights', []))}")
        else:
            analysis = analyze_interview(client, timestamped_text, metadata, args.model, analyse_instructions)
            print(f"      Q&A pairs: {len(analysis.get('qa_pairs', []))}  |  "
                  f"Insights: {len(analysis.get('key_insights', []))}")

        analysis["video_type"] = video_type
        analysis_file.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── 6. Screenshots ───────────────────────────────────────────
    shots_dir      = out_dir / "screenshots"
    existing_shots = list(shots_dir.glob("*.jpg")) if shots_dir.exists() else []
    if args.no_screenshots:
        print("\n[6/7] Screenshots skipped (--no-screenshots).")
    elif existing_shots:
        print(f"\n[6/7] Screenshots cached — {len(existing_shots)} already exist, skipping.")
    else:
        print("\n[6/7] Taking screenshots at key timestamps ...")
        max_shots   = args.max_screenshots
        visual_cues = analysis.get("visual_cues", [])
        if video_type == "informative":
            analysis["key_insights"] = take_screenshots(
                args.url, analysis.get("key_insights", []), out_dir, max_shots,
                visual_cues=visual_cues,
            )
            already   = sum(1 for i in analysis["key_insights"] if "screenshot" in i)
            remaining = max(0, max_shots - already)
            if remaining:
                analysis["key_topics"] = take_screenshots(
                    args.url, analysis.get("key_topics", []), out_dir, remaining,
                )
        else:
            analysis["qa_pairs"] = take_screenshots(
                args.url, analysis.get("qa_pairs", []), out_dir, max_shots,
                visual_cues=visual_cues,
            )
            already   = sum(1 for q in analysis["qa_pairs"] if "screenshot" in q)
            remaining = max(0, max_shots - already)
            if remaining:
                analysis["key_insights"] = take_screenshots(
                    args.url, analysis.get("key_insights", []), out_dir, remaining,
                )

    # ── 7. Word cloud ────────────────────────────────────────────
    wc_path = out_dir / "wordcloud.png"
    if wc_path.exists():
        print(f"\n[7/7] Word cloud cached — skipping.")
    else:
        print("\n[7/7] Generating word cloud ...", end=" ", flush=True)
        generate_wordcloud(plain_text, str(wc_path))
        print(f"saved -> {wc_path}")

    # ── Report ───────────────────────────────────────────────────
    report_file = out_dir / "report.md"
    if report_file.exists():
        print("      Report cached — skipping.")
        report_path = str(report_file)
    else:
        print("      Generating Sangrahak report ...", end=" ", flush=True)
        report_path = generate_sangrahak_report(
            client, analysis, metadata, args.url, lang, video_type, out_dir, args.report_model,
            report_instructions=report_instructions,
        )
        print(f"saved -> {report_path}")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  DONE")
    print(SEP)
    print(f"\n  Output folder : {out_dir.resolve()}")
    print( "  Files created :")
    print( "    transcript.txt   -- raw transcript with [HH:MM:SS] timestamps")
    print( "    analysis.json    -- structured JSON analysis")
    print( "    wordcloud.png    -- word cloud image")
    print( "    report.md        -- full markdown report")
    if not args.no_screenshots:
        shots_dir = out_dir / "screenshots"
        if shots_dir.exists():
            count = len(list(shots_dir.glob("*.jpg")))
            print(f"    screenshots/     -- {count} screenshot(s)")
    print()


if __name__ == "__main__":
    main()
