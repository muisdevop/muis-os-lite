#!/usr/bin/env python3
"""Live smoke test: a real chat + memory + web-search round trip against the real providers.

    set -a && source .env && set +a
    python scripts/smoke_live.py

Needs NEBIUS_API_KEY (or LLM_API_KEY + LLM_BASE_URL for any OpenAI-compatible endpoint) and
TAVILY_API_KEY. Checks whose key is missing are skipped, not failed. Uses an in-memory Qdrant
and a temp data dir, so nothing on your real server is touched.

Testing with Gemini's OpenAI-compatible endpoint instead of Nebius:
    LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    LLM_API_KEY=<gemini key>
    NEMOTRON_PLAN_MODEL=gemini-2.5-flash  NEMOTRON_FAST_MODEL=gemini-2.5-flash
    EMBEDDING_MODEL=gemini-embedding-001
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="muis-smoke-"))
os.environ["QDRANT_URL"] = ":memory:"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["QDRANT_COLLECTION"] = "smoke_" + str(int(time.time()))

from app.config import get_settings  # noqa: E402
from app.main import build_services  # noqa: E402
from app.tools.tavily_search import tavily_search  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""), flush=True)


def run_chat(svc, message: str) -> dict:
    print(f"\n> {message}")
    done: dict = {"tool_calls": [], "reply": ""}
    for ev in svc.agent.stream(message):
        t = ev["type"]
        if t == "tool_call":
            print(f"  -> {ev['name']}({ev['arguments']})")
        elif t == "tool_result":
            print(f"  <- {'ok' if ev['ok'] else 'FAIL'} {ev['summary'][:120]}")
        elif t == "skill_learned":
            print(f"  ** learned skill: {ev['name']}")
        elif t == "error":
            print(f"  !! {ev['message']}")
            return {"error": ev["message"], "tool_calls": [], "reply": ""}
        elif t == "done":
            done = ev
    print(f"< {done['reply'][:600]}\n")
    return done


def main() -> int:
    s = get_settings()
    print(f"Provider: {s.provider_label} {s.nebius_base_url}")
    print(f"Models: plan={s.model_plan} fast={s.model_fast} embed={s.embed_model}\n")
    svc = build_services(s)

    # 1. Tavily
    if s.tavily_api_key:
        t0 = time.time()
        try:
            res = tavily_search("NVIDIA Nemotron 3 Super", 3, api_key=s.tavily_api_key)
        except Exception as exc:
            res = {"error": f"{type(exc).__name__}: {exc}", "results": []}
        ok = bool(res.get("results")) and not res.get("error")
        check("tavily web search", ok,
              f"{len(res.get('results', []))} results in {time.time() - t0:.1f}s; "
              f"first: {(res.get('results') or [{}])[0].get('url', res.get('error'))}")
    else:
        check("tavily web search", None, "TAVILY_API_KEY not set")

    if not s.llm_configured:
        check("embeddings", None, "no LLM key")
        check("chat: remember", None, "no LLM key")
        check("chat: recall + web search", None, "no LLM key")
    else:
        # 2. Embeddings + memory
        try:
            t0 = time.time()
            vec = svc.llm.embed(["hello"])[0]
            check("embeddings", len(vec) > 0, f"dim={len(vec)} in {time.time() - t0:.1f}s ({s.embed_model})")
        except Exception as exc:
            check("embeddings", False, f"{type(exc).__name__}: {exc}"[:300])
            print("  (switching memory to the local embedder so the chat checks can continue)")
            os.environ["EMBEDDING_PROVIDER"] = "local"
            svc = build_services(get_settings())

        # 3. Chat with tools
        a = run_chat(svc, "Please remember that my favourite tea is Kashmiri chai and my "
                          "hackathon demo is due on Friday.")
        tools = [t["tool"] for t in a["tool_calls"]]
        check("chat: remember", "remember" in tools and not a.get("error"),
              f"tools={tools}; memory count={svc.memory.count()}")

        b = run_chat(svc, "What tea do I like? Then search the web for one health benefit of "
                          "that tea and give me the source URL.")
        tools = [t["tool"] for t in b["tool_calls"]]
        reply = b.get("reply", "").lower()
        check("chat: recall + web search",
              "kashmiri" in reply and ("web_search" in tools or not s.tavily_api_key)
              and "http" in reply,
              f"tools={tools}; skill_saved={b.get('skill_saved')}")

    # 4. Brief (works with or without keys)
    svc.store.add_topic("NVIDIA Nemotron")
    brief = svc.brief.build()
    check("daily brief", bool(brief["content"]),
          f"by {brief['generated_by']}; {len(brief['sources'])} sources; warnings={brief['warnings']}")
    print("\n" + brief["content"][:800])

    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed or skipped, "
          f"{len(failed)} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
