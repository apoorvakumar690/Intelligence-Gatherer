#!/usr/bin/env python3
"""
Extract all accessible Instagram post URLs from a profile.

Examples:
    python extract_urls.py https://www.instagram.com/hindu_saints/
    python extract_urls.py hindu_saints --login your_instagram_username
    python extract_urls.py hindu_saints --method browser --manual-login
    python extract_urls.py hindu_saints --limit 100 --output hindu_saints_posts.csv
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import instaloader


DEFAULT_PROFILE = "https://www.instagram.com/hindu_saints/"
DEFAULT_OUTPUT = "hindu_saints_posts.csv"


def extract_username(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9._]+", value):
        return value

    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return parts[0]

    raise ValueError(f"Could not extract Instagram username from: {value}")


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
        print(f"Loaded Instagram session for @{username}")
        return
    except FileNotFoundError:
        print(f"No saved session found for @{username}; logging in ...")
    except Exception as exc:
        print(f"Could not load saved session for @{username}: {exc}")
        print("Trying password login ...")

    password = os.environ.get(password_env)
    if not password:
        password = getpass.getpass(f"Instagram password for @{username}: ")

    loader.login(username, password)
    loader.save_session_to_file(filename=session_file)
    print(f"Logged in and saved session for @{username}")


def post_url(shortcode: str) -> str:
    return f"https://www.instagram.com/p/{shortcode}/"


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["index", "url", "shortcode", "date_utc", "typename", "is_video"],
        )
        writer.writeheader()
        writer.writerows(rows)


def shortcode_from_url(url: str) -> str:
    match = re.search(r"/(?:p|reel|tv)/([^/?#]+)/?", url)
    return match.group(1) if match else ""


def fetch_with_instaloader(args: argparse.Namespace, username: str) -> list[dict[str, str]]:
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    configure_login(loader, args.login, args.session_file, args.password_env)

    print(f"Fetching posts for @{username} with Instaloader ...")
    try:
        profile = instaloader.Profile.from_username(loader.context, username)
    except instaloader.exceptions.InstaloaderException as exc:
        raise RuntimeError(
            "Instagram blocked the profile metadata request or returned an empty response. "
            "Try `--login <your_instagram_username>` or `--method browser --manual-login`."
        ) from exc

    rows: list[dict[str, str]] = []
    for index, post in enumerate(profile.get_posts(), start=1):
        rows.append(
            {
                "index": str(index),
                "url": post_url(post.shortcode),
                "shortcode": post.shortcode,
                "date_utc": post.date_utc.isoformat(),
                "typename": post.typename,
                "is_video": str(post.is_video),
            }
        )

        if index % 50 == 0:
            print(f"  collected {index} posts ...")

        if args.limit and index >= args.limit:
            break

    return rows


def fetch_with_browser(args: argparse.Namespace, username: str) -> list[dict[str, str]]:
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("Browser mode requires Selenium. Install it with: pip install selenium") from exc

    profile_url = f"https://www.instagram.com/{username}/"
    options = Options()
    if not args.headless:
        options.add_argument("--start-maximized")
    else:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1440,1600")

    driver = webdriver.Chrome(options=options)
    hrefs: set[str] = set()

    try:
        driver.get(profile_url)
        time.sleep(args.initial_wait)

        if args.manual_login:
            input("Log in in the Chrome window, open the profile grid, then press Enter here to continue...")
            driver.get(profile_url)
            time.sleep(args.initial_wait)

        idle_rounds = 0
        last_count = 0
        last_height = 0

        for round_num in range(1, args.max_scrolls + 1):
            for element in driver.find_elements(By.TAG_NAME, "a"):
                href = element.get_attribute("href") or ""
                clean_href = href.split("?")[0]
                if re.search(r"/(?:p|reel|tv)/[^/?#]+/?$", clean_href):
                    hrefs.add(clean_href.rstrip("/") + "/")

            current_count = len(hrefs)
            current_height = driver.execute_script("return document.body.scrollHeight")
            if current_count == last_count and current_height == last_height:
                idle_rounds += 1
            else:
                idle_rounds = 0

            if current_count and current_count % 25 == 0:
                print(f"  collected {current_count} links ...")

            if args.limit and current_count >= args.limit:
                break
            if idle_rounds >= args.idle_rounds:
                break

            last_count = current_count
            last_height = current_height
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(args.scroll_wait)

        urls = sorted(hrefs, key=shortcode_from_url)
        if args.limit:
            urls = urls[: args.limit]

        return [
            {
                "index": str(index),
                "url": url,
                "shortcode": shortcode_from_url(url),
                "date_utc": "",
                "typename": "",
                "is_video": "",
            }
            for index, url in enumerate(urls, start=1)
        ]
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract all accessible post URLs from an Instagram profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("profile", nargs="?", default=DEFAULT_PROFILE, help="Instagram profile URL or username")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV output file")
    parser.add_argument(
        "--method",
        choices=["auto", "instaloader", "browser"],
        default="auto",
        help="Extraction method",
    )
    parser.add_argument("--login", metavar="USERNAME", help="Instagram username for private/rate-limited profiles")
    parser.add_argument("--session-file", help="Path to an Instaloader session file")
    parser.add_argument(
        "--password-env",
        default="INSTAGRAM_PASSWORD",
        help="Environment variable containing the Instagram password for --login",
    )
    parser.add_argument("--limit", type=int, help="Stop after this many posts")
    parser.add_argument("--manual-login", action="store_true", help="Pause browser mode so you can log in manually")
    parser.add_argument("--headless", action="store_true", help="Run browser mode without a visible Chrome window")
    parser.add_argument("--initial-wait", type=float, default=5.0, help="Seconds to wait after opening the profile")
    parser.add_argument("--scroll-wait", type=float, default=2.5, help="Seconds to wait after each scroll")
    parser.add_argument("--idle-rounds", type=int, default=8, help="Stop after this many scrolls with no new links")
    parser.add_argument("--max-scrolls", type=int, default=500, help="Maximum browser scroll attempts")
    args = parser.parse_args()

    username = extract_username(args.profile)
    output_path = Path(args.output)

    rows: list[dict[str, str]]
    if args.method == "browser":
        rows = fetch_with_browser(args, username)
    else:
        try:
            rows = fetch_with_instaloader(args, username)
        except RuntimeError as exc:
            if args.method == "instaloader":
                raise SystemExit(f"ERROR: {exc}") from exc
            print(f"Instaloader failed: {exc}")
            print("Falling back to browser scrolling. Use --manual-login if it still stops early.")
            rows = fetch_with_browser(args, username)

    write_csv(output_path, rows)
    print(f"Found {len(rows)} post URLs")
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
