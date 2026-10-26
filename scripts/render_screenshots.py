#!/usr/bin/env python3
"""Render README screenshots of the web UI into docs/screenshots/ with headless Chromium.

The model is a scripted stand-in (no API key needed) so the shots are reproducible; everything
else (FastAPI, SSE streaming, SQLite, embedded Qdrant + local embedder, skills on disk, the UI)
is the real app. Requires `pip install playwright` and a Chromium (`playwright install chromium`
or set CHROMIUM_PATH).

    python scripts/render_screenshots.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "screenshots"
TMP = Path(tempfile.mkdtemp(prefix="muis-shots-"))
os.environ.update({"DATA_DIR": str(TMP / "data"), "QDRANT_URL": ":memory:",
                   "ENABLE_SCHEDULER": "false"})

import uvicorn  # noqa: E402

from app.config import DEFAULT_SKILLS_DIR, Settings  # noqa: E402
from app.llm import LLM  # noqa: E402
from app.main import build_services, create_app  # noqa: E402
from app.memory import MemoryStore, local_embed  # noqa: E402
from app.store import Store  # noqa: E402


def _chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _text_stream(text):
    return [_chunk(content=text[i:i + 6]) for i in range(0, len(text), 6)]


def _tool(idx, cid, name, args):
    fn = SimpleNamespace(name=name, arguments=json.dumps(args))
    return [_chunk(tool_calls=[SimpleNamespace(index=idx, id=cid, type="function", function=fn)])]


ANSWER = """Saved. Here's a plan to land the demo by **Friday 22:00 PKT**:

1. **Tue**: freeze features, get CI green, deploy to your VPS with `scripts/deploy.sh`.
2. **Wed**: record the 3-minute video (chat, memory, a learned skill, the brief).
3. **Thu**: write the Devpost story and add screenshots; test the public URL on your phone.
4. **Fri morning**: submit early and leave the evening as buffer.

The submission needs a public repo, a demo video and a description of how you used the
sponsor tech (source: https://help.devpost.com/)."""

SKILL = {
    "save": True, "name": "plan-a-deadline-week",
    "description": "Turn a deadline into a day-by-day plan with sources",
    "triggers": ["plan my week", "deadline", "due on"],
    "steps": ["Save the deadline with remember (date, time, timezone)",
              "recall related plans and projects",
              "web_search the official requirements for the deliverable",
              "Work back from the deadline: one focus per day, buffer before the due time",
              "Answer as a numbered day-by-day list and cite the source URL"],
}

BRIEF = """**Today**
- Hackathon demo for MUIS OS Lite is due **Friday 30 Oct, 22:00 PKT**: today is CI + deploy day.
- Ayesha's birthday is on March 3: nothing to do yet.

**News you follow**
- **NVIDIA Nemotron**: Nemotron 3 Super and Nano are served on Nebius Token Factory through an OpenAI-compatible API (https://nebius.com/)

**One suggestion**
- Run the `plan-a-deadline-week` skill again on Thursday to re-check what's left."""


class ScriptedCompletions:
    def __init__(self):
        self.n = 0

    def create(self, **kw):
        if not kw.get("stream"):  # Nano: reflection or the brief
            user = kw["messages"][-1]["content"]
            text = json.dumps(SKILL) if "playbook" in user else BRIEF
            msg = SimpleNamespace(content=text, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        self.n += 1
        time.sleep(0.4)
        if self.n == 1:
            return iter(_tool(0, "c1", "remember", {
                "text": "Hackathon demo for MUIS OS Lite is due Friday 30 Oct, 22:00 PKT",
                "kind": "plan"}))
        if self.n == 2:
            return iter(_tool(0, "c2", "recall", {"query": "hackathon plans projects"}))
        if self.n == 3:
            return iter(_tool(0, "c3", "web_search", {
                "query": "Devpost hackathon submission requirements demo video"}))
        return iter(_text_stream(ANSWER))


class ScriptedOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=ScriptedCompletions())


class ScriptedTavily:
    def search(self, **kw):
        return {"answer": "Submissions need a repo, a demo video and a project description.",
                "results": [{"title": "Devpost Help Center", "url": "https://help.devpost.com/",
                             "content": "How to submit a project to a hackathon."},
                            {"title": "Nebius Token Factory", "url": "https://nebius.com/",
                             "content": "Open models via an OpenAI-compatible API."}]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    from playwright.sync_api import sync_playwright

    skills_dir = TMP / "skills"
    shutil.copytree(DEFAULT_SKILLS_DIR, skills_dir, ignore=shutil.ignore_patterns("*.py", "__*"))
    settings = Settings(nebius_api_key="scripted", tavily_api_key="scripted",
                        skills_dir=skills_dir, data_dir=TMP / "data", enable_scheduler=True)
    memory = MemoryStore(local_embed, url=":memory:", collection="shots")
    svc = build_services(settings, llm=LLM(settings, client=ScriptedOpenAI()), memory=memory,
                         store=Store(":memory:"), tavily_client=ScriptedTavily())
    svc.embedder = "remote (Qwen/Qwen3-Embedding-8B)"
    for text, kind in [
        ("My sister Ayesha's birthday is on March 3 and she loves green tea", "person"),
        ("I prefer meetings after 11 AM on weekdays", "preference"),
        ("GID Studio is my software company in Lahore", "project"),
        ("Power cuts usually hit between 7 and 9 PM, so schedule calls before 7", "note"),
        ("My dentist is Dr. Khan on Begum Road, Lahore", "person"),
    ]:
        svc.memory.add(text, kind=kind)
    svc.store.add_topic("NVIDIA Nemotron")
    svc.store.add_topic("Pakistan tech startups")

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(services=svc), port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    OUT.mkdir(parents=True, exist_ok=True)

    exe = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe \
            else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                                timezone_id="Asia/Karachi")
        hide_toast = "document.querySelector('#toast').classList.add('hidden')"
        page.goto(base + "/#chat")
        page.wait_for_selector("#status-text:has-text('connected')")
        page.screenshot(path=str(OUT / "01-empty.png"))
        page.fill("#input", "Remember my hackathon demo is due Friday at 10 PM and plan my week "
                            "so I finish on time.")
        page.keyboard.press("Enter")
        page.wait_for_selector(".tag.learned", timeout=20000)
        page.wait_for_timeout(600)
        page.evaluate(hide_toast)
        page.screenshot(path=str(OUT / "02-chat.png"))

        page.click(".sidebar .nav-item[data-view=memory]")
        page.wait_for_selector("#mem-list .item")
        page.fill("#mem-q", "when is my sister's birthday")
        page.click("#mem-search button[type=submit]")
        page.wait_for_selector(".score")
        page.screenshot(path=str(OUT / "03-memory.png"))

        page.click(".sidebar .nav-item[data-view=skills]")
        page.wait_for_selector("#skill-list .item.fresh")
        page.click("#skill-list .item.fresh")
        page.wait_for_selector("#skill-view li")
        page.evaluate(hide_toast)
        page.screenshot(path=str(OUT / "04-skills.png"))

        page.click(".sidebar .nav-item[data-view=brief]")
        page.click("#brief-run")
        page.wait_for_selector("#brief-current .answer strong")
        page.wait_for_timeout(300)
        page.evaluate(hide_toast)
        page.screenshot(path=str(OUT / "05-brief.png"))

        page.click(".sidebar .nav-item[data-view=settings]")
        page.wait_for_selector("#providers .provider")
        page.screenshot(path=str(OUT / "06-settings.png"))

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True, timezone_id="Asia/Karachi")
        cid = svc.store.list_conversations()[0]["id"]
        mobile.goto(base + "/#chat")
        mobile.wait_for_selector("#status-text:has-text('connected')")
        mobile.evaluate(f"document.querySelector('.conv') && document.querySelector('.conv').click()")
        mobile.wait_for_selector(".msg.bot .answer")
        mobile.wait_for_timeout(400)
        mobile.screenshot(path=str(OUT / "07-mobile-chat.png"))
        browser.close()
    server.should_exit = True
    print(f"conversation {cid}; screenshots in {OUT}")
    for f in sorted(OUT.glob("*.png")):
        print(" ", f.relative_to(ROOT), f.stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
