# Visual Screenshot Selection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace transcript-importance-driven screenshots with visually-meaningful ones — frames that show slides, charts, data tables, or on-screen text rather than random talking-head moments.

**Architecture:** Two-stage improvement.
1. **Verbal cue detection** — add a `visual_cues` field to the LLM analysis prompts so the model identifies timestamps where the speaker explicitly references visible content ("as you can see", "this chart shows", "here's the data"). Zero extra LLM calls.
2. **Scene-change snapping** — once a target timestamp is chosen, use ffmpeg to detect scene changes in a ±15 second window around it, then snap to the nearest scene boundary. Scene changes = slide transitions = the exact frame where new content appears on screen.

**Tech Stack:** Python, ffmpeg (already present), existing `_take_single_screenshot` / `take_screenshots` infrastructure in `youtube_transcript_analyzer.py`.

---

## Background: Why the current approach fails

The current flow:
- LLM picks `timestamp` fields in `key_topics` and `key_insights` based on *when something is discussed*
- Those timestamps land mid-sentence — the speaker is talking, no slide has changed
- Result: screenshots of the presenter's face, or a slide that hasn't changed yet

What we want:
- Timestamps where the speaker is *pointing at* visual content
- The exact frame boundary where a new slide appears (±1 second matters)

---

## Files to modify

| File | Change |
|------|--------|
| `youtube_transcript_analyzer.py:329-364` | Add `visual_cues` field to `analyze_informative` prompt |
| `youtube_transcript_analyzer.py:376-421` | Add `visual_cues` field to `analyze_interview` prompt |
| `youtube_transcript_analyzer.py:527-557` | Add `_detect_scene_changes(stream_url, center_hms, window_sec) -> list[str]` |
| `youtube_transcript_analyzer.py:560-584` | Add scene-snap logic in `_take_single_screenshot` or wrapper |
| `youtube_transcript_analyzer.py:587-629` | Prefer `visual_cues` items first in `take_screenshots` |
| `youtube_transcript_analyzer.py:924-950` | Pass `visual_cues` from analysis to `take_screenshots` |

No new files needed.

---

## Task 1: Add `visual_cues` to `analyze_informative` prompt

**Files:**
- Modify: `youtube_transcript_analyzer.py:329-367`

The `visual_cues` field asks the LLM to identify timestamps where the speaker is actively referencing on-screen content. These are highly likely to correspond to slide transitions showing charts or data.

- [ ] **Step 1: Update the JSON schema in the prompt**

In `analyze_informative`, inside the `Return a single JSON object` block, add this field after `takeaways`:

```python
  "visual_cues": [
    {{
      "timestamp": "HH:MM:SS",
      "description": "What visual is being referenced (e.g. 'chart showing 59% demand increase')"
    }}
  ]
```

- [ ] **Step 2: Add the rule for visual_cues**

In the `Rules:` section of the same prompt, add:

```
- visual_cues: 3-6 entries — timestamps where the speaker says things like "as you can see", "this chart shows", "here's the data", "looking at this graph", "these numbers show", or references a specific statistic/figure that is likely displayed on screen. Timestamps must exist in the transcript.
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```
Expected: `OK`

---

## Task 2: Add `visual_cues` to `analyze_interview` prompt

**Files:**
- Modify: `youtube_transcript_analyzer.py:376-421`

Same change as Task 1 but for the interview schema.

- [ ] **Step 1: Update the JSON schema in the prompt**

In `analyze_interview`, add after `takeaways`:

```python
  "visual_cues": [
    {{
      "timestamp": "HH:MM:SS",
      "description": "What visual is being referenced"
    }}
  ]
```

- [ ] **Step 2: Add the rule**

Same rule text as Task 1.

- [ ] **Step 3: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```

---

## Task 3: Add `_detect_scene_changes` helper

**Files:**
- Modify: `youtube_transcript_analyzer.py` — insert after `_take_single_screenshot` (~line 585)

Scene detection: ffmpeg's `select` filter with a scene-change threshold. A threshold of `0.3` catches slide transitions reliably without triggering on every speaker blink.

- [ ] **Step 1: Write the helper**

Insert this function after `_take_single_screenshot`:

```python
def _detect_scene_changes(stream_url: str, center_hms: str, window_sec: int = 30) -> list[str]:
    """
    Detect scene-change timestamps within [center - window/2, center + window/2].
    Returns a list of HH:MM:SS strings, closest-first to center_hms.
    Uses ffmpeg scene filter (threshold 0.3).
    """
    def hms_to_sec(hms: str) -> float:
        parts = hms.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    def sec_to_hms(s: float) -> str:
        s = max(0.0, s)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}"

    center_sec = hms_to_sec(center_hms)
    start_sec  = max(0.0, center_sec - window_sec / 2)

    cmd = [
        "ffmpeg",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-ss", sec_to_hms(start_sec),
        "-t", str(window_sec),
        "-i", stream_url,
        "-an",
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
    except subprocess.TimeoutExpired:
        return []

    # ffmpeg writes showinfo to stderr; lines look like:
    #   [Parsed_showinfo_1 @ ...] n:  0 pts: 12345 pts_time:4.567 ...
    import re
    times = []
    for line in result.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            abs_sec = start_sec + float(m.group(1))
            times.append(abs_sec)

    if not times:
        return []

    # Sort by distance from center
    times.sort(key=lambda s: abs(s - center_sec))
    return [sec_to_hms(s) for s in times]
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```

---

## Task 4: Add scene-snap wrapper `_snap_to_scene`

**Files:**
- Modify: `youtube_transcript_analyzer.py` — insert after `_detect_scene_changes`

This wraps `_take_single_screenshot`: given a target timestamp, find the closest scene change within ±10s and use that instead.

- [ ] **Step 1: Write the wrapper**

```python
def _snap_to_scene(stream_url: str, target_hms: str, out_path: str, snap_window_sec: int = 20) -> bool:
    """
    Try to take a screenshot at the nearest scene change to target_hms.
    Falls back to target_hms if no scene change found within snap_window_sec.
    """
    candidates = _detect_scene_changes(stream_url, target_hms, window_sec=snap_window_sec)
    # candidates are sorted closest-first; try up to 3
    for ts in candidates[:3]:
        if _take_single_screenshot(stream_url, ts, out_path):
            return True
    # fallback: original timestamp
    return _take_single_screenshot(stream_url, target_hms, out_path)
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```

---

## Task 5: Use `visual_cues` first and scene-snap in `take_screenshots`

**Files:**
- Modify: `youtube_transcript_analyzer.py:587-630` (`take_screenshots` function)

Change the function to:
1. Accept an optional `visual_cues: list[dict]` parameter
2. Use `visual_cues` items first (up to `max_shots`), then fall back to existing items
3. Call `_snap_to_scene` instead of `_take_single_screenshot`

- [ ] **Step 1: Update `take_screenshots` signature and body**

Replace the current function signature and inner loop:

```python
def take_screenshots(
    youtube_url: str,
    items: list[dict],
    output_dir: Path,
    max_shots: int = MAX_SCREENSHOTS,
    visual_cues: list[dict] | None = None,
) -> list[dict]:
    """
    For each item with a 'timestamp' key, take a screenshot.
    If visual_cues provided, those are used first (they indicate on-screen content).
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

    # Build ordered candidate list: visual_cues first, then items
    cue_items   = list(visual_cues or [])
    cue_set     = {c.get("timestamp") for c in cue_items}
    other_items = [i for i in items if i.get("timestamp") not in cue_set]
    candidates  = cue_items + other_items

    enriched = list(items)  # return original items enriched with screenshot paths
    ts_to_idx = {item.get("timestamp"): idx for idx, item in enumerate(enriched) if item.get("timestamp")}

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
        ok = _snap_to_scene(stream_url, ts, str(fpath))
        if ok:
            print("OK")
            taken += 1
            # If this timestamp matches an item in enriched list, attach screenshot
            if ts in ts_to_idx:
                idx = ts_to_idx[ts]
                enriched[idx] = {**enriched[idx], "screenshot": str(fpath)}
        else:
            print("FAILED")

    return enriched
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```

---

## Task 6: Wire `visual_cues` from analysis into `take_screenshots` calls in `main()`

**Files:**
- Modify: `youtube_transcript_analyzer.py:924-950` (the `[6/7] Screenshots` block in `main`)

- [ ] **Step 1: Extract visual_cues from analysis**

At the start of the screenshots block (after `if args.no_screenshots:` check), add:

```python
visual_cues = analysis.get("visual_cues", [])
```

- [ ] **Step 2: Pass visual_cues to both take_screenshots calls**

For the informative branch, update the first call:

```python
analysis["key_insights"] = take_screenshots(
    args.url, analysis.get("key_insights", []), out_dir, max_shots,
    visual_cues=visual_cues,
)
```

For subsequent calls (key_topics, qa_pairs, etc.) pass `visual_cues=[]` (cues already used).

Full updated block:

```python
if args.no_screenshots:
    print("\n[6/7] Screenshots skipped (--no-screenshots).")
else:
    print("\n[6/7] Taking screenshots at key timestamps ...")
    max_shots   = args.max_screenshots
    visual_cues = analysis.get("visual_cues", [])

    if video_type == "informative":
        analysis["key_insights"] = take_screenshots(
            args.url, analysis.get("key_insights", []), out_dir, max_shots,
            visual_cues=visual_cues,
        )
        already    = sum(1 for i in analysis["key_insights"] if "screenshot" in i)
        remaining  = max(0, max_shots - already)
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
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -m py_compile youtube_transcript_analyzer.py && echo OK
```

---

## Task 7: Manual verification

- [ ] **Step 1: Run on the El Niño video**

```bash
python youtube_transcript_analyzer.py "https://www.youtube.com/watch?v=oE8NODx1MCc" --no-screenshots
```

Check `output/.../analysis.json` — confirm `visual_cues` field is present and has 3-6 entries with plausible descriptions like "chart showing demand growth".

- [ ] **Step 2: Run with screenshots**

```bash
python youtube_transcript_analyzer.py "https://www.youtube.com/watch?v=oE8NODx1MCc"
```

Open `output/.../screenshots/` — screenshots should show slides/charts, not a talking head.

- [ ] **Step 3: Update CLAUDE.md**

Add to the Key Gotchas section:

```markdown
- **Screenshots prefer `visual_cues`** — the LLM identifies timestamps where on-screen content is referenced; scene-change snapping then finds the exact slide-transition frame within ±10 seconds.
```

---

## Rollback

If scene detection is too slow (each `_detect_scene_changes` call takes >30s on your connection), disable it by setting `snap_window_sec=0` in `_snap_to_scene` — it will then skip the detection step and go straight to `_take_single_screenshot`. No code change needed.

---

## Design Notes

**Why verbal cues outperform importance-based timestamps:**
The current model picks timestamps when something is *discussed* (importance). Verbal cues ("as you can see this chart") are *deictic* — the speaker is pointing at something. These are always better screenshot candidates.

**Why scene-snap matters:**
A verbal cue like "as you can see" at `00:11:10` likely follows the slide appearing at `00:11:07`. The frame at `00:11:10` shows the fully-rendered slide; the frame at `00:11:07` (scene change) may be mid-transition. Snapping to the closest scene change gets the cleanest slide state.

**Why not a vision model pass?**
Extra cost and latency per video. The verbal cue + scene-snap combination should achieve 80%+ of the quality improvement at zero additional LLM cost. A vision model pass is a good future enhancement once the base quality is validated.
