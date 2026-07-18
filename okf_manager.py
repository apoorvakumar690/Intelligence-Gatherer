#!/usr/bin/env python3
"""
OKF Manager — Open Knowledge Format
=====================================
Create and manage a shared OKF knowledge base from transcripts, insights,
and other research sources.

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

Usage:
    python okf_manager.py init <bundle_path>
    python okf_manager.py list <bundle_path>
    python okf_manager.py search <bundle_path> <query>
    python okf_manager.py add <bundle_path> --type <type> --title <title> [options]
    python okf_manager.py export-youtube <analysis_json> <bundle_path>
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime


OKF_VERSION = "0.1"
RESERVED    = {"index.md", "log.md"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def _yaml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # Quote if it contains YAML-special chars or looks ambiguous
    if any(c in s for c in ':#{}[]|>&*!,"\'') or s.startswith(" ") or s in ("true", "false", "null", ""):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _write_md(path: Path, frontmatter: dict, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_yaml_value(item)}")
        else:
            lines.append(f"{k}: {_yaml_value(v)}")
    lines.append("---")
    if body:
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_fm(path: Path) -> dict:
    """Parse YAML frontmatter from an OKF concept file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("---", 3)
    except ValueError:
        return {}
    fm: dict = {}
    current_list: str | None = None
    for line in text[3:end].splitlines():
        if line.startswith("  - ") and current_list:
            fm[current_list].append(line[4:].strip().strip('"'))
        elif ": " in line and not line.startswith(" "):
            k, _, raw = line.partition(": ")
            v = raw.strip().strip('"')
            if v == "":
                current_list = k.strip()
                fm[current_list] = []
            else:
                current_list = None
                fm[k.strip()] = v
        elif line.rstrip().endswith(":") and not line.startswith(" "):
            current_list = line.rstrip()[:-1].strip()
            fm[current_list] = []
    return fm


def _append_log(bundle_path: Path, message: str) -> None:
    log_path = bundle_path / "log.md"
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8")
    entry = f"\n## {_now()}\n\n{message}\n"
    if text.startswith("---"):
        try:
            end = text.index("---", 3) + 3
            log_path.write_text(text[:end] + entry + text[end:], encoding="utf-8")
            return
        except ValueError:
            pass
    log_path.write_text(text + entry, encoding="utf-8")


# ─── Commands ─────────────────────────────────────────────────────────────────

def init_bundle(bundle_path: Path) -> None:
    bundle_path.mkdir(parents=True, exist_ok=True)
    index = bundle_path / "index.md"
    if not index.exists():
        _write_md(index, {
            "okf_version": OKF_VERSION,
            "type":        "bundle",
            "title":       bundle_path.name,
            "description": "Shared research knowledge base",
            "timestamp":   _now(),
        }, "# Knowledge Base\n\nOKF bundle — auto-managed by `okf_manager.py`.")
        _write_md(bundle_path / "log.md", {
            "type":  "log",
            "title": "Change Log",
        }, f"## {_now()}\n\nBundle initialized.")
        print(f"OKF bundle initialized -> {bundle_path.resolve()}")
    else:
        print(f"Bundle already exists at {bundle_path.resolve()}")


def list_bundle(bundle_path: Path) -> None:
    docs = sorted(bundle_path.rglob("*.md"))
    concepts = [d for d in docs if d.name not in RESERVED]
    print(f"Bundle: {bundle_path.resolve()}\n{len(concepts)} concept(s)\n")
    for doc in concepts:
        fm  = _parse_fm(doc)
        rel = doc.relative_to(bundle_path)
        typ = fm.get("type", "?")
        tit = fm.get("title", doc.stem)
        tags = fm.get("tags", [])
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  {str(rel):<55} [{typ}] {tit}{tag_str}")


def search_bundle(bundle_path: Path, query: str) -> None:
    q = query.lower()
    docs = sorted(bundle_path.rglob("*.md"))
    hits = []
    for doc in docs:
        if doc.name in RESERVED:
            continue
        text = doc.read_text(encoding="utf-8").lower()
        if q in text:
            hits.append((doc, _parse_fm(doc)))
    print(f"Search '{query}' — {len(hits)} hit(s)\n")
    for doc, fm in hits:
        rel = doc.relative_to(bundle_path)
        print(f"  [{fm.get('type','?'):10}] {fm.get('title', doc.stem)}")
        if resource := fm.get("resource"):
            print(f"               {resource}")
        print(f"               {rel}\n")


def add_concept(bundle_path: Path, args) -> None:
    slug = _slugify(args.title)
    sub  = getattr(args, "subdir", "") or ""
    out  = bundle_path / sub / f"{slug}.md" if sub else bundle_path / f"{slug}.md"

    fm: dict = {"type": args.type, "title": args.title, "timestamp": _now()}
    if getattr(args, "description", ""):
        fm["description"] = args.description
    if getattr(args, "resource", ""):
        fm["resource"] = args.resource
    if raw_tags := getattr(args, "tags", ""):
        fm["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]

    body = ""
    if bf := getattr(args, "body_file", ""):
        body = Path(bf).read_text(encoding="utf-8")

    _write_md(out, fm, body)
    rel = out.relative_to(bundle_path)
    print(f"Added: {rel}")
    _append_log(bundle_path, f"Added concept: {args.title} [{args.type}] -> {rel}")


# ─── YouTube export ───────────────────────────────────────────────────────────

def export_youtube_to_okf(
    analysis: dict,
    metadata: dict,
    video_url: str,
    bundle_path: Path,
) -> int:
    """
    Export a YouTube analysis dict to the OKF bundle.
    Returns the number of concept docs created.
    """
    video_type = analysis.get("video_type", "informative")
    title      = metadata.get("title", "Untitled")
    channel    = metadata.get("channel", "")
    lang       = metadata.get("lang", "")
    url        = metadata.get("url", video_url)

    slug      = _slugify(title)
    video_dir = bundle_path / "youtube" / slug

    # ── root video concept ──────────────────────────────────────────────────
    summary = analysis.get("summary", "")
    tags    = ["youtube", video_type]
    if lang:
        tags.append(lang)

    _write_md(video_dir / "index.md", {
        "type":        "video",
        "title":       title,
        "description": summary[:200],
        "resource":    url,
        "tags":        tags,
        "timestamp":   _now(),
        "channel":     channel,
        "lang":        lang,
        "video_type":  video_type,
    }, f"# {title}\n\n**Channel:** {channel}\n\n## Summary\n\n{summary}")

    count = 1  # index.md

    # ── informative: topics + insights ─────────────────────────────────────
    if video_type == "informative":
        for topic in analysis.get("key_topics", []):
            t_name = topic.get("topic", "topic")
            t_slug = _slugify(t_name)
            _write_md(video_dir / f"topic-{t_slug}.md", {
                "type":        "topic",
                "title":       t_name,
                "description": topic.get("description", ""),
                "resource":    url,
                "tags":        ["youtube", "topic", topic.get("importance", "medium")],
                "timestamp":   _now(),
                "source_ts":   topic.get("timestamp", ""),
            }, topic.get("description", ""))
            count += 1

        for i, ins in enumerate(analysis.get("key_insights", []), 1):
            text  = ins.get("insight", "")
            i_slug = _slugify(text[:60]) or f"insight-{i}"
            _write_md(video_dir / f"insight-{i_slug}.md", {
                "type":        "insight",
                "title":       text[:120],
                "description": ins.get("context", ""),
                "resource":    url,
                "tags":        ["youtube", "insight"],
                "timestamp":   _now(),
                "source_ts":   ins.get("timestamp", ""),
            }, f"> {text}\n\n{ins.get('context', '')}")
            count += 1

    # ── interview: Q&A + insights ───────────────────────────────────────────
    else:
        for i, qa in enumerate(analysis.get("qa_pairs", []), 1):
            q    = qa.get("question", "")
            q_slug = _slugify(q[:60]) or f"qa-{i}"
            _write_md(video_dir / f"qa-{i:02d}-{q_slug}.md", {
                "type":        "qa",
                "title":       q[:120],
                "description": qa.get("insight", ""),
                "resource":    url,
                "tags":        ["youtube", "interview", "qa"],
                "timestamp":   _now(),
                "source_ts":   qa.get("timestamp", ""),
            }, f"**Q:** {q}\n\n**A:** {qa.get('answer','')}\n\n> {qa.get('insight','')}")
            count += 1

        for i, ins in enumerate(analysis.get("key_insights", []), 1):
            text  = ins.get("insight", "")
            i_slug = _slugify(text[:60]) or f"insight-{i}"
            _write_md(video_dir / f"insight-{i_slug}.md", {
                "type":        "insight",
                "title":       text[:120],
                "resource":    url,
                "tags":        ["youtube", "insight"],
                "timestamp":   _now(),
                "source_ts":   ins.get("timestamp", ""),
            }, f"> {text}")
            count += 1

    _append_log(bundle_path, f"Imported YouTube: {title} ({count} concepts) -> youtube/{slug}/")
    return count


def cmd_export_youtube(analysis_path: Path, bundle_path: Path) -> None:
    analysis  = json.loads(analysis_path.read_text(encoding="utf-8"))
    meta_path = analysis_path.parent / "metadata.json"
    metadata  = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    url       = metadata.get("url", "")

    if not (bundle_path / "index.md").exists():
        init_bundle(bundle_path)

    count = export_youtube_to_okf(analysis, metadata, url, bundle_path)
    slug  = _slugify(metadata.get("title", analysis_path.parent.name))
    print(f"Exported {count} concept docs -> {bundle_path / 'youtube' / slug}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="OKF Manager — Open Knowledge Format shared knowledge base",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    s = sub.add_parser("init", help="Initialise a new OKF bundle")
    s.add_argument("bundle", help="Directory path for the bundle")

    # list
    s = sub.add_parser("list", help="List all concept docs in a bundle")
    s.add_argument("bundle")

    # search
    s = sub.add_parser("search", help="Full-text search across bundle")
    s.add_argument("bundle")
    s.add_argument("query")

    # add
    s = sub.add_parser("add", help="Add a concept doc manually")
    s.add_argument("bundle")
    s.add_argument("--type",        required=True, help="Concept type (e.g. insight, note, topic)")
    s.add_argument("--title",       required=True)
    s.add_argument("--description", default="")
    s.add_argument("--resource",    default="", help="Source URL")
    s.add_argument("--tags",        default="", help="Comma-separated tags")
    s.add_argument("--body-file",   default="", metavar="PATH", help="Markdown body file")
    s.add_argument("--subdir",      default="", help="Subdirectory within bundle")

    # export-youtube
    s = sub.add_parser("export-youtube", help="Export a YouTube analysis.json into the bundle")
    s.add_argument("analysis", help="Path to analysis.json (output/<video>/analysis.json)")
    s.add_argument("bundle",   help="Bundle path (created if not exists)")

    args = p.parse_args()

    if args.cmd == "init":
        init_bundle(Path(args.bundle))
    elif args.cmd == "list":
        list_bundle(Path(args.bundle))
    elif args.cmd == "search":
        search_bundle(Path(args.bundle), args.query)
    elif args.cmd == "add":
        bundle = Path(args.bundle)
        if not (bundle / "index.md").exists():
            init_bundle(bundle)
        add_concept(bundle, args)
    elif args.cmd == "export-youtube":
        cmd_export_youtube(Path(args.analysis), Path(args.bundle))


if __name__ == "__main__":
    main()
