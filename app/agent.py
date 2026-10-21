"""A simple tool-calling loop on NVIDIA Nemotron (via Nebius Token Factory).

1. Recall relevant memories and matching skills into the system prompt.
2. Loop with Nemotron Super: call tools until the model answers (or step limit).
3. After a multi-step task, ask Nemotron Nano whether it is worth saving as a skill.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.llm import LLM, strip_think
from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.tools import Toolbox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are MUIS OS Lite, a private second brain running on the user's own server.
You remember the user's life, follow saved skills, and run small errands with tools.
Rules:
- Use `recall` before answering questions about the user's past, people or plans.
- Use `remember` when the user shares a durable fact about their life.
- Use `web_search` for anything current; cite source URLs.
- If a saved skill fits the task, follow its steps.
- Be brief and concrete.

What you remember that may be relevant:
{memories}

Skills you know:
{skill_index}
{matched_skills}"""

REFLECT_PROMPT = """A task was just completed with these tool calls:
{trace}

User request: {request}

Would a reusable playbook help next time a similar request comes in? Existing skills:
{skill_index}

Reply with JSON only:
{{"save": true|false, "name": "kebab-case-name", "description": "one line",
  "triggers": ["phrase", ...], "steps": ["step 1", "step 2", ...]}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memories_used: int = 0
    skill_saved: str | None = None


class Agent:
    def __init__(
        self,
        llm: LLM,
        memory: MemoryStore,
        skills: SkillLibrary,
        toolbox: Toolbox,
        max_steps: int = 6,
        auto_learn: bool = True,
    ):
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.auto_learn = auto_learn

    def _system_prompt(self, message: str) -> tuple[str, int]:
        try:
            memories = self.memory.recall(message, k=5)
        except Exception as exc:  # memory is helpful, never fatal
            log.warning("memory recall failed: %s", exc)
            memories = []
        mem_text = "\n".join(f"- {m['text']}" for m in memories) or "(nothing yet)"
        matched = self.skills.match(message)
        matched_text = "".join(f"\nSkill `{s.name}`:\n{s.body}\n" for s in matched)
        prompt = SYSTEM_PROMPT.format(
            memories=mem_text, skill_index=self.skills.index_text(), matched_skills=matched_text
        )
        return prompt, len(memories)

    def run(self, message: str, history: list[dict[str, str]] | None = None) -> AgentResult:
        system, n_memories = self._system_prompt(message)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages += history or []
        messages.append({"role": "user", "content": message})

        trace: list[dict[str, Any]] = []
        reply = "I ran out of steps before finishing. Try narrowing the request."
        for _ in range(self.max_steps):
            resp = self.llm.chat(messages, tier="plan", tools=self.toolbox.specs)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                reply = strip_think(msg.content)
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                result = self.toolbox.call(tc.function.name, tc.function.arguments)
                trace.append({"tool": tc.function.name, "arguments": tc.function.arguments})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str)[:6000],
                    }
                )

        result = AgentResult(reply=reply, tool_calls=trace, memories_used=n_memories)
        already_saved = any(t["tool"] == "save_skill" for t in trace)
        if self.auto_learn and len(trace) >= 2 and not already_saved:
            result.skill_saved = self.maybe_learn_skill(message, trace)
        return result

    def maybe_learn_skill(self, request: str, trace: list[dict[str, Any]]) -> str | None:
        """Ask the fast model if this task should become a reusable skill; save it if so."""
        prompt = REFLECT_PROMPT.format(
            trace=json.dumps(trace, indent=1)[:3000],
            request=request,
            skill_index=self.skills.index_text(),
        )
        try:
            raw = self.llm.complete(prompt, tier="fast")
            match = _JSON_RE.search(raw)
            data = json.loads(match.group(0)) if match else {}
        except Exception as exc:
            log.warning("skill reflection failed: %s", exc)
            return None
        if not data.get("save") or not data.get("name") or not data.get("steps"):
            return None
        try:
            skill = self.skills.save(
                data["name"], data.get("description", ""), data["steps"], data.get("triggers")
            )
        except FileExistsError:
            return None
        return skill.name
