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
DEFAULT_MODEL         = "anthropic/claude-3.5-sonnet"
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

def _llm(client: OpenAI, prompt: str, system: str = "", model: str = DEFAULT_MODEL) -> str:
    """Send a chat completion request via OpenRouter."""
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
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

def analyze_informative(client: OpenAI, timestamped_transcript: str, metadata: dict, model: str) -> dict:
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
  "takeaways": ["Takeaway 1", "Takeaway 2"]
}}

Rules:
- key_topics: 6-10 entries, timestamps must exist in the transcript
- key_insights: 5-8 entries, timestamps must exist in the transcript
- important_facts: 5-8 notable data points or facts
- takeaways: 4-6 actionable or memorable points
- Return ONLY valid JSON"""

    raw = _llm(client, prompt, model=model)
    return _parse_json(raw)


# ─── Step 2b: Interview analysis ──────────────────────────────────────────────

def analyze_interview(client: OpenAI, timestamped_transcript: str, metadata: dict, model: str) -> dict:
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
  "takeaways": ["Takeaway 1", "Takeaway 2"]
}}

Rules:
- qa_pairs: 6-10 entries covering the most significant exchanges
- notable_quotes: 4-6 impactful quotes
- key_insights: 5-8 entries
- All timestamps must exist in the transcript
- Return ONLY valid JSON"""

    raw = _llm(client, prompt, model=model)
    return _parse_json(raw)


# ─── Step 3: Screenshots ──────────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _get_stream_url(youtube_url: str) -> str:
    """Return the best direct video stream URL (no download)."""
    opts = {
        "format":      "best[ext=mp4]/bestvideo[ext=mp4]/best",
        "quiet":       True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    if "url" in info:
        return info["url"]

    for fmt in sorted(
        info.get("formats", []),
        key=lambda f: f.get("height", 0) or 0,
        reverse=True,
    ):
        if fmt.get("ext") == "mp4" and fmt.get("vcodec") not in (None, "none"):
            return fmt["url"]

    raise RuntimeError("Could not obtain a direct video stream URL.")


def _take_single_screenshot(stream_url: str, hms: str, out_path: str) -> bool:
    """Use ffmpeg to grab one frame at timestamp hms. Returns True on success."""
    cmd = [
        "ffmpeg", "-ss", hms, "-i", stream_url,
        "-vframes", "1", "-q:v", "2", "-y", out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=90)
        return result.returncode == 0 and Path(out_path).exists()
    except subprocess.TimeoutExpired:
        return False


def take_screenshots(
    youtube_url: str,
    items: list[dict],
    output_dir: Path,
    max_shots: int = MAX_SCREENSHOTS,
) -> list[dict]:
    """
    For each item with a 'timestamp' key, take a screenshot.
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

    enriched = []
    taken    = 0
    for idx, item in enumerate(items):
        ts = item.get("timestamp", "")
        if not ts or taken >= max_shots:
            enriched.append(item)
            continue

        fname = f"shot_{idx+1:02d}_{ts.replace(':', '-')}.jpg"
        fpath = shots_dir / fname

        print(f"  [screenshot] @ {ts} ...", end=" ", flush=True)
        ok = _take_single_screenshot(stream_url, ts, str(fpath))
        if ok:
            print("OK")
            item = {**item, "screenshot": str(fpath)}
            taken += 1
        else:
            print("FAILED")

        enriched.append(item)

    print(f"  {taken} screenshot(s) saved -> {shots_dir}")
    return enriched


# ─── Step 4: Word cloud ────────────────────────────────────────────────────────

def generate_wordcloud(plain_text: str, output_path: str) -> str:
    wc = WordCloud(
        width=1400,
        height=700,
        background_color="white",
        stopwords=STOPWORDS,
        max_words=150,
        colormap="viridis",
        collocations=True,
        prefer_horizontal=0.85,
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
        default=DEFAULT_MODEL,
        help=f"Model to use via OpenRouter (default: {DEFAULT_MODEL})",
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
    args = parser.parse_args()

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
    print(f"  Model: {args.model}")
    print(SEP)

    # ── 1. Video metadata ────────────────────────────────────────
    print("\n[1/7] Fetching video metadata ...")
    video_id = extract_video_id(args.url)
    metadata = fetch_video_metadata(args.url)
    print(f"      Title   : {metadata['title']}")
    print(f"      Channel : {metadata['channel']}")
    print(f"      Duration: {seconds_to_hms(metadata['duration'])}")

    # ── 2. Transcript ────────────────────────────────────────────
    print("\n[2/7] Fetching transcript ...")
    segments, lang = fetch_transcript(video_id)
    plain_text       = " ".join((s.text if hasattr(s, "text") else s["text"]) for s in segments)
    timestamped_text = segments_to_timestamped_text(segments)
    print(f"      Language: {lang}  |  Segments: {len(segments)}  |  "
          f"Chars: {len(plain_text):,}")

    # ── 3. Output directory ──────────────────────────────────────
    # Sanitise title for use as a folder name
    safe_title = re.sub(r'[\\/:*?"<>|]', '', metadata['title']).strip()
    safe_title = re.sub(r'\s+', ' ', safe_title)[:100]  # cap length
    folder_name = safe_title or video_id
    out_dir = Path(args.output_dir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[3/7] Output -> {out_dir.resolve()}")

    transcript_file = out_dir / "transcript.txt"
    transcript_file.write_text(timestamped_text, encoding="utf-8")
    print(f"      transcript.txt saved ({len(timestamped_text):,} chars)")

    # ── 4. Classify ──────────────────────────────────────────────
    print("\n[4/7] Classifying video type ...")
    video_type = classify_video(client, metadata, timestamped_text, args.model)
    print(f"      -> {video_type.upper()}")

    # ── 5. Analyse ───────────────────────────────────────────────
    print("\n[5/7] Analysing transcript ...")
    if video_type == "informative":
        analysis = analyze_informative(client, timestamped_text, metadata, args.model)
        print(f"      Topics: {len(analysis.get('key_topics', []))}  |  "
              f"Insights: {len(analysis.get('key_insights', []))}")
    else:
        analysis = analyze_interview(client, timestamped_text, metadata, args.model)
        print(f"      Q&A pairs: {len(analysis.get('qa_pairs', []))}  |  "
              f"Insights: {len(analysis.get('key_insights', []))}")

    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── 6. Screenshots ───────────────────────────────────────────
    if args.no_screenshots:
        print("\n[6/7] Screenshots skipped (--no-screenshots).")
    else:
        print("\n[6/7] Taking screenshots at key timestamps ...")
        max_shots = args.max_screenshots
        if video_type == "informative":
            analysis["key_insights"] = take_screenshots(
                args.url, analysis.get("key_insights", []), out_dir, max_shots
            )
            already = sum(1 for i in analysis["key_insights"] if "screenshot" in i)
            remaining = max(0, max_shots - already)
            if remaining:
                analysis["key_topics"] = take_screenshots(
                    args.url, analysis.get("key_topics", []), out_dir, remaining
                )
        else:
            analysis["qa_pairs"] = take_screenshots(
                args.url, analysis.get("qa_pairs", []), out_dir, max_shots
            )
            already = sum(1 for q in analysis["qa_pairs"] if "screenshot" in q)
            remaining = max(0, max_shots - already)
            if remaining:
                analysis["key_insights"] = take_screenshots(
                    args.url, analysis.get("key_insights", []), out_dir, remaining
                )

    # ── 7. Word cloud ────────────────────────────────────────────
    print("\n[7/7] Generating word cloud ...", end=" ", flush=True)
    wc_path = str(out_dir / "wordcloud.png")
    generate_wordcloud(plain_text, wc_path)
    print(f"saved -> {wc_path}")

    # ── Report ───────────────────────────────────────────────────
    print("      Building report ...", end=" ", flush=True)
    if video_type == "informative":
        report_path = build_report_informative(analysis, metadata, out_dir, lang, args.url)
    else:
        report_path = build_report_interview(analysis, metadata, out_dir, lang, args.url)
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
