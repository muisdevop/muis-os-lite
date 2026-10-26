"""Daily brief: memory + saved skills + Tavily news on followed topics (+ Google if connected).

Gathering is deterministic Python (no tool-calling round trips), then Nemotron Nano writes the
brief in one call. Without an LLM key the brief is still produced from the gathered data, so the
feature is usable (and testable) offline.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.llm import LLM
from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.store import Store
from app.tools.tavily_search import tavily_search

log = logging.getLogger(__name__)

BRIEF_SYSTEM = """You write a short, warm morning brief for one person, from their own private data.
Use only the facts given. Markdown, under 220 words, these sections when they have content:
**Today** (max 4 bullets: meetings, deadlines, plans), **Inbox** (only if mail is given),
**News you follow** (1 bullet per topic with the source URL in parentheses),
**One suggestion** (a single concrete next step, may point to a saved skill by name).
No preamble, no sign-off."""


class BriefBuilder:
    def __init__(self, llm: LLM, memory: MemoryStore, skills: SkillLibrary, store: Store,
                 tavily_api_key: str = "", tavily_client: Any | None = None,
                 google: Any | None = None, timezone: str = "Asia/Karachi",
                 default_topics: list[str] | None = None):
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.store = store
        self.tavily_api_key = tavily_api_key
        self.tavily_client = tavily_client
        self.google = google
        self.timezone = timezone
        self.default_topics = default_topics or []

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_client or self.tavily_api_key)

    def topics(self) -> list[str]:
        saved = self.store.topics()
        return saved or self.default_topics

    def gather(self) -> dict[str, Any]:
        data: dict[str, Any] = {"memories": [], "news": [], "events": [], "mail": [],
                                "skills": [], "warnings": []}
        try:
            seen: set[str] = set()
            for q in ("plans deadlines meetings tasks this week", "projects goals working on"):
                for m in self.memory.recall(q, k=6):
                    if m["id"] not in seen and m.get("kind") != "daily_brief":
                        seen.add(m["id"])
                        data["memories"].append(m["text"])
            for m in self.memory.recent(limit=5):
                if m["id"] not in seen and m.get("kind") != "daily_brief":
                    seen.add(m["id"])
                    data["memories"].append(m["text"])
        except Exception as exc:
            data["warnings"].append(f"memory unavailable: {exc}")
        data["skills"] = [s.name for s in self.skills.all() if s.source == "learned"][-5:]

        topics = self.topics()[:5]
        if topics and not self.tavily_enabled:
            data["warnings"].append("TAVILY_API_KEY not set: news skipped")
        elif topics:
            for topic in topics:
                try:
                    res = tavily_search(topic, 3, api_key=self.tavily_api_key,
                                        client=self.tavily_client, topic="news")
                except Exception as exc:
                    data["warnings"].append(f"news for '{topic}' failed: {exc}")
                    continue
                if res.get("error"):
                    data["warnings"].append(res["error"])
                    continue
                data["news"].append({
                    "topic": topic,
                    "answer": res.get("answer") or "",
                    "results": [{"title": r["title"], "url": r["url"]}
                                for r in res.get("results", [])[:3]],
                })
        if self.google is not None:
            try:
                data["events"] = self.google.calendar_today()["events"]
            except Exception as exc:
                data["warnings"].append(f"calendar failed: {exc}")
            try:
                data["mail"] = self.google.gmail_recent(max_results=6)["messages"]
            except Exception as exc:
                data["warnings"].append(f"gmail failed: {exc}")
        return data

    @staticmethod
    def _context(data: dict[str, Any], today: str) -> str:
        lines = [f"Date: {today}", "", "Memories:"]
        lines += [f"- {m}" for m in data["memories"]] or ["- (none)"]
        if data["events"]:
            lines += ["", "Calendar today:"]
            lines += [f"- {e['start']} {e['summary']}" for e in data["events"]]
        if data["mail"]:
            lines += ["", "Unread mail:"]
            lines += [f"- {m['from']}: {m['subject']} | {m['snippet'][:120]}" for m in data["mail"]]
        if data["news"]:
            lines += ["", "News on followed topics:"]
            for n in data["news"]:
                lines.append(f"- Topic: {n['topic']}. Summary: {n['answer']}")
                lines += [f"  - {r['title']} ({r['url']})" for r in n["results"]]
        if data["skills"]:
            lines += ["", "Skills learned recently: " + ", ".join(data["skills"])]
        return "\n".join(lines)

    @staticmethod
    def _plain(data: dict[str, Any], today: str) -> str:
        """Brief without a model: a tidy digest of the gathered data."""
        out = [f"### Brief for {today}", ""]
        if data["events"]:
            out.append("**Today**")
            out += [f"- {e['start'][11:16] if 'T' in (e['start'] or '') else 'All day'} "
                    f"{e['summary']}" for e in data["events"]]
            out.append("")
        if data["memories"]:
            out.append("**On your mind**")
            out += [f"- {m}" for m in data["memories"][:5]]
            out.append("")
        if data["mail"]:
            out.append("**Inbox**")
            out += [f"- {m['subject']} ({m['from']})" for m in data["mail"][:5]]
            out.append("")
        if data["news"]:
            out.append("**News you follow**")
            for n in data["news"]:
                first = n["results"][0] if n["results"] else None
                answer = n["answer"] or (first["title"] if first else "no results")
                link = f" ({first['url']})" if first else ""
                out.append(f"- **{n['topic']}**: {answer}{link}")
            out.append("")
        if not any((data["events"], data["memories"], data["mail"], data["news"])):
            out.append("Nothing saved yet. Tell me about your plans, or follow a topic, "
                       "and tomorrow's brief will have something to say.")
        return "\n".join(out).strip()

    def build(self, trigger: str = "manual") -> dict[str, Any]:
        today = datetime.now(ZoneInfo(self.timezone)).strftime("%A %d %B %Y")
        data = self.gather()
        content = None
        generated_by = "digest (no LLM key)"
        if self.llm.available:
            try:
                content = self.llm.complete(self._context(data, today), tier="fast",
                                            system=BRIEF_SYSTEM, temperature=0.4)
                generated_by = self.llm.model_for("fast")
            except Exception as exc:
                log.warning("brief LLM call failed: %s", exc)
                data["warnings"].append(f"LLM failed, showing digest: {exc}"[:200])
        if not content:
            content = self._plain(data, today)
        sources, seen = [], set()
        for n in data["news"]:
            for r in n["results"]:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    sources.append({"title": r["title"], "url": r["url"], "topic": n["topic"]})
        brief = self.store.add_brief(content, sources, trigger)
        brief["warnings"] = data["warnings"]
        brief["generated_by"] = generated_by
        return brief
