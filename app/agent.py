"""The MUIS OS Lite agent: a streaming tool-calling loop on NVIDIA Nemotron.

1. Recall relevant memories and matching skills into the system prompt.
2. Loop on Nemotron Super (via Nebius Token Factory): stream text, run tool calls, feed results
   back, until the model answers or the step limit is hit.
3. After a multi-step task, ask Nemotron Nano whether the steps are worth saving as a reusable
   markdown skill. Next time a similar request comes in, the skill is matched and followed.

`stream()` yields plain-dict events (used for SSE); `run()` collects them into an AgentResult.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from app.llm import LLM, LLMUnavailable, parse_json_object
from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.tools import Toolbox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are MUIS OS Lite, a private second brain running on the user's own server.
You remember the user's life, follow saved skills, and run small errands with tools.
Now: {now} ({tz}).

Rules:
- Use `recall` before answering questions about the user's past, people, plans or preferences
  when the memories below are not enough.
- Use `remember` when the user shares a durable fact about their life (one fact per call).
- Use `web_search` for anything current or factual you are not sure of; cite source URLs.
- If a skill below matches the task, follow its steps in order.
- When the user wants news on a subject in their brief, use `follow_topic` if available.
- Never invent tool results. Be brief, warm and concrete. Use markdown lists for multiple items.

What you remember that may be relevant:
{memories}

Skills you know (use `read_skill` for the steps):
{skill_index}
{matched_skills}"""

REFLECT_PROMPT = """You review an AI assistant's finished task and decide whether to save a reusable
playbook (a "skill") so the next similar request is handled faster and the same way.

User request: {request}
Tool calls made, in order:
{trace}

Existing skills:
{skill_index}

Save a skill only if the task is a repeatable kind of errand (not a one-off fact) and no existing
skill already covers it. Steps must be generic (no specific names/dates from this request) and
mention the tools to call. Reply with JSON only, no prose:
{{"save": true, "name": "kebab-case-name", "description": "one line",
  "triggers": ["short phrase a user would say", "..."], "steps": ["step 1", "step 2", "..."]}}
or {{"save": false}}"""

MEMORY_TOOLS = {"remember", "recall", "list_skills", "read_skill", "follow_topic"}


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memories_used: int = 0
    skill_saved: str | None = None
    skills_matched: list[str] = field(default_factory=list)


def summarize_result(name: str, result: Any) -> str:
    """One-line human summary of a tool result for the UI's step list."""
    if isinstance(result, dict) and result.get("error"):
        return f"error: {result['error']}"[:200]
    try:
        if name == "web_search":
            urls = [r.get("url", "") for r in result.get("results", [])]
            return f"{len(urls)} results" + (f": {', '.join(urls[:3])}" if urls else "")
        if name == "recall":
            mems = result.get("memories", [])
            return f"{len(mems)} memories" + (f": {mems[0]['text'][:80]}" if mems else "")
        if name == "remember":
            return f"saved: {result.get('text', '')[:100]}"
        if name in ("save_skill",):
            return f"skill saved: {result.get('saved')}"
        if name == "read_skill":
            return f"read skill {result.get('name')}"
        if name == "list_skills":
            return f"{len(result.get('skills', []))} skills"
        if name == "follow_topic":
            return f"following: {result.get('following')}"
        if name == "gmail_recent":
            return f"{result.get('count', 0)} emails"
        if name == "calendar_today":
            return f"{len(result.get('events', []))} events on {result.get('date')}"
    except Exception:  # pragma: no cover - summary is cosmetic
        pass
    return json.dumps(result, default=str)[:160]


class Agent:
    def __init__(
        self,
        llm: LLM,
        memory: MemoryStore,
        skills: SkillLibrary,
        toolbox: Toolbox,
        max_steps: int = 6,
        auto_learn: bool = True,
        timezone: str = "Asia/Karachi",
    ):
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.auto_learn = auto_learn
        self.timezone = timezone

    def _system_prompt(self, message: str) -> tuple[str, int, list[str]]:
        try:
            memories = self.memory.recall(message, k=6)
        except Exception as exc:  # memory is helpful, never fatal
            log.warning("memory recall failed: %s", exc)
            memories = []
        mem_text = "\n".join(f"- {m['text']}" for m in memories) or "(nothing yet)"
        matched = self.skills.match(message)
        matched_text = "".join(
            f"\nA saved skill matches this request. Follow it: `{s.name}`\n{s.body}\n"
            for s in matched
        )
        now = datetime.now(ZoneInfo(self.timezone)).strftime("%A %d %B %Y, %H:%M")
        prompt = SYSTEM_PROMPT.format(
            now=now, tz=self.timezone, memories=mem_text,
            skill_index=self.skills.index_text(), matched_skills=matched_text,
        )
        return prompt, len(memories), [s.name for s in matched]

    # --- streaming loop ------------------------------------------------------------------
    def stream(self, message: str, history: list[dict[str, str]] | None = None
               ) -> Iterator[dict[str, Any]]:
        """Run the agent, yielding events. Errors become an `error` event, never an exception."""
        try:
            yield from self._loop(message, history)
        except LLMUnavailable as exc:
            yield {"type": "error", "code": "llm_unavailable", "message": str(exc)}
        except Exception as exc:
            log.exception("agent failed")
            yield {"type": "error", "code": "agent_error",
                   "message": f"The model call failed: {type(exc).__name__}: {exc}"[:500]}

    def _loop(self, message: str, history: list[dict[str, str]] | None
              ) -> Iterator[dict[str, Any]]:
        if not self.llm.available:
            raise LLMUnavailable("NEBIUS_API_KEY is not set; add it to your .env to enable chat")
        system, n_memories, matched = self._system_prompt(message)
        yield {"type": "start", "memories_used": n_memories, "skills_matched": matched}

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages += history or []
        messages.append({"role": "user", "content": message})

        trace: list[dict[str, Any]] = []
        reply = ""
        finished = False
        for step in range(self.max_steps):
            final = None
            for ev in self.llm.stream(messages, tier="plan", tools=self.toolbox.specs):
                if ev.kind == "token":
                    yield {"type": "token", "text": ev.text}
                elif ev.kind == "thinking":
                    yield {"type": "thinking", "text": ev.text}
                else:
                    final = ev
            if final is None or not final.tool_calls:
                reply = (final.content if final else "") or ""
                finished = True
                break
            if final.content:
                yield {"type": "interim", "text": final.content}
            messages.append({
                "role": "assistant",
                "content": final.content or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in final.tool_calls
                ],
            })
            for tc in final.tool_calls:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = tc["arguments"]
                yield {"type": "tool_call", "id": tc["id"], "name": tc["name"],
                       "arguments": args, "step": step + 1}
                result = self.toolbox.call(tc["name"], tc["arguments"])
                ok = not (isinstance(result, dict) and result.get("error"))
                summary = summarize_result(tc["name"], result)
                trace.append({"tool": tc["name"], "arguments": tc["arguments"],
                              "ok": ok, "summary": summary})
                yield {"type": "tool_result", "id": tc["id"], "name": tc["name"],
                       "ok": ok, "summary": summary}
                if tc["name"] == "save_skill" and ok:
                    yield {"type": "skill_learned", "name": result.get("saved")}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str, ensure_ascii=False)[:8000],
                })

        if not finished:
            reply = "I ran out of steps before finishing. Try narrowing the request."
            yield {"type": "token", "text": reply}
        elif not reply:
            reply = "(The model returned an empty answer. Try rephrasing.)"
            yield {"type": "token", "text": reply}

        skill_saved = None
        if self._should_reflect(trace):
            yield {"type": "reflecting"}
            skill_saved = self.maybe_learn_skill(message, trace)
            if skill_saved:
                yield {"type": "skill_learned", "name": skill_saved}
        yield {
            "type": "done",
            "reply": reply,
            "tool_calls": trace,
            "memories_used": n_memories,
            "skills_matched": matched,
            "skill_saved": skill_saved,
        }

    def _should_reflect(self, trace: list[dict[str, Any]]) -> bool:
        if not self.auto_learn or len(trace) < 2:
            return False
        if any(t["tool"] == "save_skill" for t in trace):
            return False
        errand_tools = [t for t in trace if t["tool"] not in MEMORY_TOOLS]
        return bool(errand_tools) or len(trace) >= 3

    def run(self, message: str, history: list[dict[str, str]] | None = None) -> AgentResult:
        """Non-streaming convenience wrapper. Raises LLMUnavailable / RuntimeError on failure."""
        done: dict[str, Any] | None = None
        for ev in self.stream(message, history):
            if ev["type"] == "error":
                if ev["code"] == "llm_unavailable":
                    raise LLMUnavailable(ev["message"])
                raise RuntimeError(ev["message"])
            if ev["type"] == "done":
                done = ev
        assert done is not None
        return AgentResult(
            reply=done["reply"], tool_calls=done["tool_calls"],
            memories_used=done["memories_used"], skill_saved=done["skill_saved"],
            skills_matched=done["skills_matched"],
        )

    # --- skill learning ------------------------------------------------------------------
    def maybe_learn_skill(self, request: str, trace: list[dict[str, Any]]) -> str | None:
        """Ask the fast model if this task should become a reusable skill; save it if so."""
        slim = [{"tool": t["tool"], "arguments": t["arguments"], "result": t.get("summary")}
                for t in trace]
        prompt = REFLECT_PROMPT.format(
            trace=json.dumps(slim, indent=1, ensure_ascii=False)[:3500],
            request=request,
            skill_index=self.skills.index_text(),
        )
        try:
            data = parse_json_object(self.llm.complete(prompt, tier="fast", temperature=0.2))
        except Exception as exc:
            log.warning("skill reflection failed: %s", exc)
            return None
        steps = data.get("steps")
        if not data.get("save") or not data.get("name") or not steps:
            return None
        if isinstance(steps, str):
            steps = [s for s in steps.splitlines() if s.strip()]
        triggers = data.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",")]
        try:
            skill = self.skills.save(
                str(data["name"]), str(data.get("description", "")), [str(s) for s in steps],
                [str(t) for t in triggers], source="learned",
            )
        except FileExistsError:
            return None
        return skill.name
