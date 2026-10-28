#!/usr/bin/env python3
"""
Hiring Agent — Hermes-driven browser agent for Indeed.

Attaches to the Chrome you already have open (logged in to Indeed Employer),
walks through your jobs one after another, and saves every candidate's resume
into  <resumes-dir>/<Role>/<Candidate>.pdf

Setup (once):
  1. Close Chrome fully, then start it with remote debugging:
       Windows:  chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\chrome-agent"
     (log in to Indeed in that window)
  2. export OPENROUTER_API_KEY="sk-or-..."
  3. python hiring_agent.py

WSL note: if http://localhost:9222 is not reachable from WSL, enable mirrored
networking (.wslconfig: [wsl2] networkingMode=mirrored) or pass --cdp-url with
the Windows host IP.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

for _pkg, _mod in (("requests", "requests"), ("playwright", "playwright")):
    try:
        __import__(_mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _pkg])

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DEFAULT_MODEL = "nousresearch/hermes-4-405b"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def default_resume_dir() -> Path:
    wsl = Path("/mnt/c/Users/apoor/Documents/resumes")
    return wsl if Path("/mnt/c").exists() else Path(r"C:\Users\apoor\Documents\resumes")


def safe_name(s: str, fallback: str = "unknown") -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "").strip(" .")
    return re.sub(r"\s+", " ", s)[:80] or fallback


SYSTEM_PROMPT = """You are a hiring assistant controlling the user's Chrome browser, which is \
already logged in to Indeed Employer. Your job: download the resume of every candidate for \
every job the user is hiring for, saving each into a folder named after the job role.

Work through tasks strictly one after another:
1. Call snapshot to see the page. Elements are numbered; use those numbers with click / download_resume.
2. Find the list of jobs (Indeed Employer -> Candidates / Jobs). Pick ONE job at a time.
3. Call set_role with that job's title BEFORE downloading anything for it.
4. Open the candidate list for that job. For each candidate: open them (click), call
   download_resume with the number of the resume/download button and the candidate's full name, then go_back.
5. Go to the next page of candidates until the job is exhausted, then move to the next job.
6. Skip anyone already listed as downloaded (the tool will tell you). Never click Reject, Message, \
Schedule, Hire, or any other action that contacts or changes the status of a candidate.
7. When all jobs are done, or you are stuck after retrying, call finish with a summary.
Take one action per step and re-snapshot after navigation. Do not invent element numbers."""

TOOLS = [
    {"name": "snapshot", "description": "Return the current tab URL, title, visible text excerpt and numbered clickable elements.", "parameters": {"type": "object", "properties": {}}},
    {"name": "goto", "description": "Navigate the current tab to a URL.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "click", "description": "Click element number N from the latest snapshot.", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}},
    {"name": "type_text", "description": "Type text into input element number N and press Enter if submit is true.", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}, "text": {"type": "string"}, "submit": {"type": "boolean"}}, "required": ["index", "text"]}},
    {"name": "scroll", "description": "Scroll the page down (or up) to load more content.", "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["down", "up"]}}}},
    {"name": "go_back", "description": "Browser back.", "parameters": {"type": "object", "properties": {}}},
    {"name": "set_role", "description": "Set the job role whose folder resumes are saved into. Call before downloading for each job.", "parameters": {"type": "object", "properties": {"role": {"type": "string"}}, "required": ["role"]}},
    {"name": "download_resume", "description": "Click element N (a resume download/view button) and save the resulting file under the current role folder.", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}, "candidate_name": {"type": "string"}}, "required": ["index", "candidate_name"]}},
    {"name": "finish", "description": "Stop; all jobs are done or progress is impossible.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]

# Collect visible interactive elements and tag them so Playwright can find them again.
SNAPSHOT_JS = """
() => {
  document.querySelectorAll('[data-ha]').forEach(e => e.removeAttribute('data-ha'));
  const out = [];
  const sel = 'a[href], button, [role=button], [role=tab], input:not([type=hidden]), select, summary';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || el.placeholder || '').trim().replace(/\\s+/g, ' ').slice(0, 90);
    if (!label && el.tagName !== 'INPUT') continue;
    const i = out.length;
    el.setAttribute('data-ha', i);
    out.push({i, tag: el.tagName.toLowerCase(), label, href: el.tagName === 'A' ? (el.getAttribute('href') || '').slice(0, 100) : ''});
    if (out.length >= 150) break;
  }
  return {text: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 2500), els: out};
}
"""


class Browser:
    def __init__(self, cdp_url: str, out_dir: Path, headless_launch: bool = False):
        self.out_dir = out_dir
        self.role = None
        self.manifest_path = out_dir / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            sys.exit(f"Could not attach to Chrome at {cdp_url}: {e}\n"
                     "Start Chrome with --remote-debugging-port=9222 (see docstring).")
        self.ctx = self.browser.contexts[0]
        pages = self.ctx.pages
        self.page = next((p for p in pages if "indeed" in p.url), pages[0] if pages else self.ctx.new_page())

    def _save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False))

    def _el(self, index: int):
        return self.page.locator(f'[data-ha="{int(index)}"]').first

    def _adopt_newest_tab(self):
        """Clicks on Indeed often open a new tab; follow it."""
        if self.ctx.pages[-1] != self.page:
            self.page = self.ctx.pages[-1]
            self.page.bring_to_front()

    def snapshot(self):
        self._adopt_newest_tab()
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PWTimeout:
            pass
        d = self.page.evaluate(SNAPSHOT_JS)
        lines = [f"[{e['i']}] <{e['tag']}> {e['label']}" + (f" -> {e['href']}" if e['href'] else "") for e in d["els"]]
        return (f"URL: {self.page.url}\nTITLE: {self.page.title()}\nCURRENT ROLE: {self.role}\n"
                f"TEXT: {d['text']}\nELEMENTS:\n" + "\n".join(lines))

    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        return f"Navigated to {self.page.url}"

    def click(self, index):
        self._el(index).click(timeout=8000)
        time.sleep(1.5)
        self._adopt_newest_tab()
        return f"Clicked [{index}]. Now at {self.page.url}"

    def type_text(self, index, text, submit=False):
        el = self._el(index)
        el.fill(text, timeout=8000)
        if submit:
            el.press("Enter")
        time.sleep(1.5)
        return "Typed."

    def scroll(self, direction="down"):
        self.page.mouse.wheel(0, 1500 if direction == "down" else -1500)
        time.sleep(1)
        return f"Scrolled {direction}."

    def go_back(self):
        if len(self.ctx.pages) > 1 and self.page != self.ctx.pages[0]:
            self.page.close()
            self.page = self.ctx.pages[-1]
        else:
            self.page.go_back()
        time.sleep(1)
        return f"Back at {self.page.url}"

    def set_role(self, role):
        self.role = safe_name(role, "Unsorted")
        (self.out_dir / self.role).mkdir(parents=True, exist_ok=True)
        return f"Role folder set: {self.out_dir / self.role}"

    def download_resume(self, index, candidate_name):
        if not self.role:
            return "ERROR: call set_role first."
        name = safe_name(candidate_name, "candidate")
        key = f"{self.role}/{name}"
        if key in self.manifest:
            return f"SKIP: {name} already downloaded for {self.role}."
        folder = self.out_dir / self.role
        el = self._el(index)
        known = set(self.ctx.pages)
        dest = None
        try:
            with self.page.expect_download(timeout=10000) as dl:
                el.click(timeout=8000)
            d = dl.value
            ext = Path(d.suggested_filename).suffix or ".pdf"
            dest = folder / f"{name}{ext}"
            d.save_as(str(dest))
        except PWTimeout:
            # No download event: the resume may have opened as a PDF/viewer tab.
            time.sleep(1.5)
            new = [p for p in self.ctx.pages if p not in known]
            if new:
                try:
                    r = self.ctx.request.get(new[0].url)
                    if r.ok and "pdf" in r.headers.get("content-type", "").lower():
                        dest = folder / f"{name}.pdf"
                        dest.write_bytes(r.body())
                finally:
                    new[0].close()
        if not dest:
            return "FAILED: no file was produced. Try another element (look for 'Download resume')."
        self.manifest[key] = {"file": str(dest), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._save_manifest()
        return f"SAVED {dest}"


def call_llm(api_key, model, messages):
    r = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages,
              "tools": [{"type": "function", "function": t} for t in TOOLS], "temperature": 0.2},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def run(args):
    api_key = os.environ.get("OPENROUTER_API_KEY") or sys.exit("Set OPENROUTER_API_KEY")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    b = Browser(args.cdp_url, out_dir)

    task = args.task or "Download all candidate resumes for all my open jobs, one job at a time."
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{task}\nSave under: {out_dir}"}]

    for step in range(1, args.max_steps + 1):
        msg = call_llm(api_key, args.model, messages)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "user", "content": "Continue using the tools, or call finish."})
            continue
        for c in calls[:1]:  # strictly one action per step
            fn = c["function"]["name"]
            try:
                a = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                a = {}
            print(f"[{step}] {fn} {a}")
            if fn == "finish":
                print("\nDONE:", a.get("summary", ""))
                return
            try:
                result = getattr(b, fn)(**a)
            except Exception as e:  # report to the model so it can recover
                result = f"ERROR: {type(e).__name__}: {str(e)[:300]}"
            print("    ->", result.splitlines()[0][:160])
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:7000]})
        # keep context bounded: drop old snapshots, keep the last few exchanges
        if len(messages) > 40:
            messages = messages[:2] + messages[-30:]
            while messages[2].get("role") == "tool":
                messages.pop(2)
    print("Stopped: max steps reached. Re-run to continue (already-saved resumes are skipped).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Hermes browser agent: Indeed resumes -> role folders")
    ap.add_argument("--out", default=str(default_resume_dir()), help="Resume root folder")
    ap.add_argument("--cdp-url", default="http://localhost:9222", help="Chrome remote-debugging URL")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-steps", type=int, default=300)
    ap.add_argument("--task", help="Override the instruction, e.g. only for the 'Data Analyst' job")
    run(ap.parse_args())
