#!/usr/bin/env python3
"""
Instagram Post Extractor
========================
Downloads media and text from a public Instagram post/reel URL.

Usage:
    python instagram_post_extractor.py https://www.instagram.com/p/DYv7LNajDcX/
    python instagram_post_extractor.py <url> --login your_instagram_username
    python instagram_post_extractor.py <url> --ocr
    python instagram_post_extractor.py <url> --vision-ocr
    python instagram_post_extractor.py <url> --ocr --crop-pictures

Output:
    output/instagram/<shortcode>/
      caption.txt
      metadata.json
      report.md
      media/
        *.jpg / *.mp4
      ocr_text.txt       (only with --ocr, when tesseract is installed)
      slides/
        slide_text.md
        crops/
          slide_01_crop_01.jpg
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import base64
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def _pip_install(*packages: str) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ensure_package(module: str, package: str) -> None:
    if importlib.util.find_spec(module):
        return
    print(f"Installing missing package: {package} ...")
    _pip_install(package)


_ensure_package("instaloader", "instaloader")

import instaloader


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_VISION_OCR_MODEL = "google/gemini-2.0-flash-001"


def extract_shortcode(value: str) -> str:
    """Extract the Instagram shortcode from a post/reel/tv URL or raw shortcode."""
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{5,}", value):
        return value

    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("p", "reel", "tv"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]

    raise ValueError(f"Could not extract Instagram shortcode from: {value}")


def safe_filename(text: str, fallback: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:80] or fallback).strip()


def configure_login(
    loader: instaloader.Instaloader,
    username: str | None,
    session_file: str | None,
    password_env: str,
) -> None:
    if not username:
        return

    try:
        loader.load_session_from_file(username, filename=session_file)
        print(f"  Loaded Instagram session for @{username}")
        return
    except FileNotFoundError:
        print(f"  No saved session found for @{username}; logging in ...")
    except Exception as exc:
        print(f"  Could not load saved session for @{username}: {exc}")
        print("  Trying password login ...")

    password = os.environ.get(password_env)
    if not password:
        password = getpass.getpass(f"Instagram password for @{username}: ")

    loader.login(username, password)
    loader.save_session_to_file(filename=session_file)
    print(f"  Logged in and saved session for @{username}")


def post_to_metadata(post: instaloader.Post, source_url: str) -> dict:
    location = None
    if post.location:
        location = {
            "id": post.location.id,
            "name": post.location.name,
            "slug": post.location.slug,
        }

    return {
        "source_url": source_url,
        "shortcode": post.shortcode,
        "owner_username": post.owner_username,
        "owner_id": post.owner_id,
        "date_utc": post.date_utc.replace(tzinfo=timezone.utc).isoformat(),
        "typename": post.typename,
        "caption": post.caption or "",
        "hashtags": list(post.caption_hashtags),
        "mentions": list(post.caption_mentions),
        "is_video": post.is_video,
        "media_count": post.mediacount,
        "likes": post.likes,
        "comments": post.comments,
        "url": post.url,
        "video_url": post.video_url if post.is_video else None,
        "location": location,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def image_files(media_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in media_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ),
        key=slide_sort_key,
    )


def slide_sort_key(path: Path) -> tuple[int, int | str, str]:
    """Sort Instaloader carousel slides by the trailing _<number> in the filename."""
    match = re.search(r"_(\d+)$", path.stem)
    if match:
        return (0, int(match.group(1)), path.name)
    return (1, path.name, path.name)


def run_ocr(media_dir: Path, lang: str) -> list[dict]:
    if not shutil.which("tesseract"):
        print("  [!] tesseract not found on PATH; OCR skipped.")
        print("      Install it first, then rerun with --ocr.")
        return []

    _ensure_package("pytesseract", "pytesseract")
    from PIL import Image
    import pytesseract

    results = []
    for image_path in image_files(media_dir):
        try:
            text = pytesseract.image_to_string(Image.open(image_path), lang=lang).strip()
        except Exception as exc:
            results.append({"file": image_path.name, "error": str(exc), "text": ""})
            continue
        results.append({"file": image_path.name, "text": text})

    return results


def run_vision_ocr(media_dir: Path, api_key: str, model: str) -> list[dict]:
    if not api_key:
        raise SystemExit(
            "ERROR: --vision-ocr requires an OpenRouter API key.\n"
            "Set OPENROUTER_API_KEY or pass --api-key <key>."
        )

    _ensure_package("openai", "openai")
    from openai import OpenAI

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    results = []

    for image_path in image_files(media_dir):
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            "Extract all readable text from this Instagram carousel slide. "
            "Preserve the natural reading order. Include headings and body text. "
            "Do not describe the image. Return only the extracted text."
        )
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=1500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{encoded}",
                                },
                            },
                        ],
                    }
                ],
            )
            text = response.choices[0].message.content.strip()
            results.append({"file": image_path.name, "text": text, "method": "vision"})
        except Exception as exc:
            results.append({"file": image_path.name, "error": str(exc), "text": "", "method": "vision"})

    return results


def parse_crop_box(value: str, width: int, height: int) -> tuple[int, int, int, int]:
    """Parse x1,y1,x2,y2 as pixels or percentages like 10%,20%,90%,80%."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("Crop box must have four values: x1,y1,x2,y2")

    dims = [width, height, width, height]
    coords = []
    for part, dim in zip(parts, dims):
        if part.endswith("%"):
            coords.append(round(float(part[:-1]) / 100 * dim))
        else:
            coords.append(round(float(part)))

    x1, y1, x2, y2 = coords
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"Crop box outside image bounds: {value}")
    return x1, y1, x2, y2


def _rect_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix1 >= ix2 or iy1 >= iy2:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    a_area = (ax2 - ax1) * (ay2 - ay1)
    b_area = (bx2 - bx1) * (by2 - by1)
    return inter / max(1, min(a_area, b_area))


def auto_picture_boxes(image_path: Path, min_area_ratio: float = 0.015) -> list[tuple[int, int, int, int]]:
    """Find likely embedded picture/portrait regions in designed slides.

    The heuristic looks for large non-background connected regions.
    Small text fragments are filtered out by area, shape, and fill density.
    """
    _ensure_package("cv2", "opencv-python")
    import cv2
    import numpy as np

    image = cv2.imread(str(image_path))
    if image is None:
        return []

    height, width = image.shape[:2]
    area = width * height

    def dominant_from_pixels(pixels: np.ndarray, count: int) -> np.ndarray:
        quantized = (pixels // 24) * 24
        colors, counts = np.unique(quantized.reshape(-1, 3), axis=0, return_counts=True)
        return colors[np.argsort(counts)[-count:]]

    def non_background_mask(dominant: np.ndarray) -> np.ndarray:
        diff_to_bg = np.min(
            np.linalg.norm(
                image[:, :, None, :].astype(np.int16) - dominant[None, None, :, :].astype(np.int16),
                axis=3,
            ),
            axis=2,
        )
        return (diff_to_bg > 42).astype("uint8") * 255

    preview = cv2.resize(image, (90, 110), interpolation=cv2.INTER_AREA)
    global_dominant = dominant_from_pixels(preview, 6)

    border_samples = []
    sample = max(40, round(min(width, height) * 0.045))
    for x1, y1, x2, y2 in (
        (0, 0, sample * 2, sample * 2),
        (width - sample * 2, 0, width, sample * 2),
        (0, height - sample * 2, sample * 2, height),
        (width - sample * 2, height - sample * 2, width, height),
        (0, 0, sample, height),
        (width - sample, 0, width, height),
        (0, height - sample, width, height),
    ):
        border_samples.append(image[y1:y2, x1:x2].reshape(-1, 3))
    border_dominant = dominant_from_pixels(np.vstack(border_samples), 8)

    masks = [
        (non_background_mask(global_dominant), 0.32),
        (non_background_mask(border_dominant), 0.25),
    ]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    boxes: list[tuple[int, int, int, int]] = []
    for raw_mask, min_fill in masks:
        mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        mask = cv2.dilate(mask, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
            x, y, w, h = cv2.boundingRect(contour)
            box_area = w * h
            if box_area < area * min_area_ratio:
                continue
            if w < width * 0.08 or h < height * 0.08:
                continue
            if box_area > area * 0.55:
                continue
            aspect = w / max(1, h)
            if aspect < 0.18 or aspect > 3.8:
                continue
            fill = cv2.countNonZero(raw_mask[y:y + h, x:x + w]) / max(1, box_area)
            if fill < min_fill:
                continue

            pad = 8
            box = (
                max(0, x - pad),
                max(0, y - pad),
                min(width, x + w + pad),
                min(height, y + h + pad),
            )
            if any(_rect_overlap(box, existing) > 0.55 for existing in boxes):
                continue
            boxes.append(box)

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes[:6]


def crop_slide_pictures(
    media_dir: Path,
    output_dir: Path,
    manual_crop_box: str | None = None,
    min_area_ratio: float = 0.015,
) -> list[dict]:
    from PIL import Image

    crops_dir = output_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    for old_crop in crops_dir.glob("*.jpg"):
        old_crop.unlink()
    results: list[dict] = []

    for slide_num, image_path in enumerate(image_files(media_dir), 1):
        image = Image.open(image_path).convert("RGB")
        if manual_crop_box:
            boxes = [parse_crop_box(manual_crop_box, image.width, image.height)]
        else:
            boxes = auto_picture_boxes(image_path, min_area_ratio=min_area_ratio)

        slide_crops = []
        for crop_num, box in enumerate(boxes, 1):
            crop_path = crops_dir / f"slide_{slide_num:02d}_crop_{crop_num:02d}.jpg"
            image.crop(box).save(crop_path, quality=95)
            slide_crops.append({"file": crop_path.name, "box": box})

        results.append({"slide": image_path.name, "crops": slide_crops})

    return results


def write_slide_text(output_dir: Path, ocr_results: list[dict]) -> Path:
    lines = ["# Slide Text", ""]
    for idx, item in enumerate(ocr_results, 1):
        lines += [
            f"## Slide {idx}: {item['file']}",
            "",
            item.get("text") or "_No text detected._",
            "",
        ]
    path = output_dir / "slide_text.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def relative_report_path(path: Path, out_dir: Path) -> str:
    return path.relative_to(out_dir).as_posix()


def markdown_table_cell(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "_No text detected._"
    text = re.sub(r"\s*\n+\s*", " ", text)
    return text.replace("|", "\\|")


def markdown_image(path: str, alt: str) -> str:
    return f"![{alt.replace('|', '-')}]({path})"


def write_report(
    out_dir: Path,
    metadata: dict,
    media_files: list[Path],
    ocr_results: list[dict],
    crop_results: list[dict] | None = None,
) -> Path:
    lines = [
        f"# Instagram Post: {metadata['shortcode']}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Account | @{metadata['owner_username']} |",
        f"| Date UTC | {metadata['date_utc']} |",
        f"| Type | {metadata['typename']} |",
        f"| Media count | {metadata['media_count']} |",
        f"| Likes | {metadata['likes']} |",
        f"| Comments | {metadata['comments']} |",
        f"| URL | {metadata['source_url']} |",
        "",
        "## Caption",
        "",
        metadata["caption"] or "_No caption found._",
        "",
    ]

    if metadata["hashtags"]:
        lines += ["## Hashtags", "", " ".join(f"#{tag}" for tag in metadata["hashtags"]), ""]

    if metadata["mentions"]:
        lines += ["## Mentions", "", " ".join(f"@{name}" for name in metadata["mentions"]), ""]

    image_media = sorted(
        (
            media_file
            for media_file in media_files
            if media_file.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ),
        key=slide_sort_key,
    )
    video_media = [
        media_file
        for media_file in media_files
        if media_file.suffix.lower() in {".mp4", ".mov", ".webm"}
    ]

    if image_media:
        ocr_by_file = {item["file"]: item for item in ocr_results}
        crops_by_slide = {item["slide"]: item for item in (crop_results or [])}
        lines += [
            "## Slides",
            "",
            "| Original slide | Extracted text | Cropped picture |",
            "|---|---|---|",
        ]

        for idx, media_file in enumerate(image_media, 1):
            slide_rel = relative_report_path(media_file, out_dir)
            ocr_item = ocr_by_file.get(media_file.name, {})
            crop_item = crops_by_slide.get(media_file.name, {})
            crops = crop_item.get("crops", [])

            if crops:
                crop_parts = []
                for crop in crops:
                    crop_path = out_dir / "slides" / "crops" / crop["file"]
                    crop_rel = relative_report_path(crop_path, out_dir)
                    crop_parts.append(markdown_image(crop_rel, crop["file"]))
                crop_cell = " ".join(crop_parts)
            else:
                crop_cell = "_No picture regions detected._"

            slide_cell = f"**Slide {idx}** {markdown_image(slide_rel, media_file.name)} `{media_file.name}`"
            text_cell = f"**Slide {idx}** {markdown_table_cell(ocr_item.get('text', ''))} `{media_file.name}`"
            lines.append(f"| {slide_cell} | {text_cell} | {crop_cell} |")

        lines.append("")

    if video_media:
        lines += ["## Videos", ""]
        for media_file in video_media:
            rel = relative_report_path(media_file, out_dir)
            lines += [f"- [{media_file.name}]({rel})"]
        lines.append("")

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract media, caption, hashtags, mentions, and optional OCR text from an Instagram post.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="Instagram post/reel URL or shortcode")
    parser.add_argument("--output-dir", default="output/instagram", help="Base output directory")
    parser.add_argument("--login", metavar="USERNAME", help="Instagram username for private/rate-limited posts")
    parser.add_argument("--session-file", help="Path to an Instaloader session file")
    parser.add_argument(
        "--password-env",
        default="INSTAGRAM_PASSWORD",
        help="Environment variable containing the Instagram password for --login",
    )
    parser.add_argument("--no-videos", action="store_true", help="Skip video downloads")
    parser.add_argument("--comments", action="store_true", help="Ask Instaloader to download comments metadata")
    parser.add_argument("--ocr", action="store_true", help="Run OCR on downloaded images if tesseract is installed")
    parser.add_argument("--ocr-lang", default="eng", help="Tesseract language code, e.g. eng or eng+hin")
    parser.add_argument(
        "--vision-ocr",
        action="store_true",
        help="Extract slide text with a vision LLM via OpenRouter instead of local tesseract",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key for --vision-ocr (default: $OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--vision-model",
        default=DEFAULT_VISION_OCR_MODEL,
        help=f"Vision model for --vision-ocr (default: {DEFAULT_VISION_OCR_MODEL})",
    )
    parser.add_argument("--crop-pictures", action="store_true", help="Extract likely embedded picture regions from each slide")
    parser.add_argument(
        "--manual-crop",
        metavar="X1,Y1,X2,Y2",
        help="Use one fixed crop box for every slide. Values can be pixels or percentages, e.g. 45%%,18%%,92%%,82%%",
    )
    parser.add_argument(
        "--min-crop-area",
        type=float,
        default=0.015,
        help="Minimum auto-detected crop area as a fraction of the slide (default: 0.015)",
    )
    args = parser.parse_args()

    shortcode = extract_shortcode(args.url)
    out_dir = Path(args.output_dir) / shortcode
    media_dir = out_dir / "media"
    slides_dir = out_dir / "slides"
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(exist_ok=True)
    slides_dir.mkdir(exist_ok=True)

    print("\nInstagram Post Extractor")
    print(f"  Shortcode : {shortcode}")
    print(f"  Output    : {out_dir.resolve()}")

    loader = instaloader.Instaloader(
        dirname_pattern=str(media_dir),
        download_pictures=True,
        download_videos=not args.no_videos,
        download_video_thumbnails=True,
        download_geotags=False,
        download_comments=args.comments,
        save_metadata=False,
        compress_json=False,
        quiet=False,
    )

    configure_login(loader, args.login, args.session_file, args.password_env)

    print("\n[1/4] Fetching post metadata ...")
    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
    except instaloader.exceptions.LoginRequiredException as exc:
        raise SystemExit(
            "ERROR: Instagram requires login for this post.\n"
            "Try: python instagram_post_extractor.py <url> --login <your_username>"
        ) from exc
    except instaloader.exceptions.ConnectionException as exc:
        raise SystemExit(f"ERROR: Could not fetch Instagram post: {exc}") from exc

    metadata = post_to_metadata(post, args.url)
    print(f"      Account : @{metadata['owner_username']}")
    print(f"      Type    : {metadata['typename']}")
    print(f"      Media   : {metadata['media_count']}")

    print("\n[2/4] Downloading media ...")
    loader.download_post(post, target=str(media_dir))
    media_files = sorted(
        path
        for path in media_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4"}
    )
    print(f"      Saved {len(media_files)} media file(s)")

    print("\n[3/4] Writing text files ...")
    (out_dir / "caption.txt").write_text(metadata["caption"], encoding="utf-8")
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print("      caption.txt")
    print("      metadata.json")

    ocr_results: list[dict] = []
    if args.ocr and args.vision_ocr:
        raise SystemExit("ERROR: Use either --ocr or --vision-ocr, not both.")

    if args.vision_ocr:
        print("\n[4/4] Running vision OCR ...")
        ocr_results = run_vision_ocr(media_dir, args.api_key, args.vision_model)
        if ocr_results:
            (out_dir / "ocr.json").write_text(json.dumps(ocr_results, indent=2, ensure_ascii=False), encoding="utf-8")
            ocr_text = "\n\n".join(
                f"## {item['file']}\n\n{item.get('text', '')}".strip()
                for item in ocr_results
            )
            (out_dir / "ocr_text.txt").write_text(ocr_text, encoding="utf-8")
            write_slide_text(slides_dir, ocr_results)
            print("      ocr.json")
            print("      ocr_text.txt")
            print("      slides/slide_text.md")
    elif args.ocr:
        print("\n[4/4] Running OCR ...")
        ocr_results = run_ocr(media_dir, args.ocr_lang)
        if ocr_results:
            (out_dir / "ocr.json").write_text(json.dumps(ocr_results, indent=2, ensure_ascii=False), encoding="utf-8")
            ocr_text = "\n\n".join(
                f"## {item['file']}\n\n{item.get('text', '')}".strip()
                for item in ocr_results
            )
            (out_dir / "ocr_text.txt").write_text(ocr_text, encoding="utf-8")
            write_slide_text(slides_dir, ocr_results)
            print("      ocr.json")
            print("      ocr_text.txt")
            print("      slides/slide_text.md")
    else:
        print("\n[4/4] OCR skipped (use --ocr to extract text embedded inside images).")

    crop_results: list[dict] = []
    if args.crop_pictures:
        print("\n[extra] Cropping embedded pictures ...")
        crop_results = crop_slide_pictures(
            media_dir,
            slides_dir,
            manual_crop_box=args.manual_crop,
            min_area_ratio=args.min_crop_area,
        )
        (out_dir / "picture_crops.json").write_text(
            json.dumps(crop_results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        count = sum(len(item["crops"]) for item in crop_results)
        print(f"      Saved {count} crop(s) -> {slides_dir / 'crops'}")
        print("      picture_crops.json")

    report_path = write_report(out_dir, metadata, media_files, ocr_results, crop_results)

    print("\nDONE")
    print(f"  Output folder : {out_dir.resolve()}")
    print(f"  Report        : {report_path}")


if __name__ == "__main__":
    main()
