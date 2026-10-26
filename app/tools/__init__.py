"""The agent's toolbox: OpenAI-format tool specs plus their Python handlers."""

from __future__ import annotations

import json
from typing import Any, Callable

from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.tools.tavily_search import TOOL_SPEC as WEB_SEARCH_SPEC
from app.tools.tavily_search import tavily_search


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


class Toolbox:
    def __init__(self) -> None:
        self.specs: list[dict[str, Any]] = []
        self.handlers: dict[str, Callable[..., Any]] = {}

    def register(self, spec: dict[str, Any], handler: Callable[..., Any]) -> None:
        self.specs.append(spec)
        self.handlers[spec["function"]["name"]] = handler

    @property
    def names(self) -> list[str]:
        return list(self.handlers)

    def call(self, name: str, arguments: str | dict | None) -> Any:
        handler = self.handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool '{name}'"}
        try:
            if isinstance(arguments, str):
                args = json.loads(arguments) if arguments.strip() else {}
            else:
                args = arguments or {}
            if not isinstance(args, dict):
                raise json.JSONDecodeError("not an object", str(arguments), 0)
        except json.JSONDecodeError:
            return {"error": "tool arguments were not valid JSON"}
        try:
            return handler(**args)
        except TypeError as exc:  # wrong/extra argument names from the model
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # report tool failures back to the model
            return {"error": f"{type(exc).__name__}: {exc}"}


def build_toolbox(
    memory: MemoryStore,
    skills: SkillLibrary,
    tavily_api_key: str | None = None,
    tavily_client: Any | None = None,
    store: Any | None = None,
    google: Any | None = None,
) -> Toolbox:
    box = Toolbox()

    box.register(
        WEB_SEARCH_SPEC,
        lambda query, max_results=5, **_: tavily_search(
            query, max_results, api_key=tavily_api_key, client=tavily_client
        ),
    )
    box.register(
        _fn(
            "remember",
            "Save one durable fact about the user's life (people, plans, preferences, projects) "
            "to long-term memory. Write it as a self-contained sentence.",
            {
                "text": {"type": "string"},
                "kind": {"type": "string", "description": "person|preference|plan|project|note"},
            },
            ["text"],
        ),
        lambda text, kind="note", tags=None, **_: {
            "saved": memory.add(text, kind=kind, tags=tags), "text": text
        },
    )
    box.register(
        _fn(
            "recall",
            "Search long-term memory for notes related to a query.",
            {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}},
            ["query"],
        ),
        lambda query, k=5, **_: {
            "memories": [{"text": m["text"], "kind": m.get("kind"), "score": m.get("score")}
                         for m in memory.recall(query, k=int(k))]
        },
    )
    box.register(
        _fn("list_skills", "List the names and descriptions of every saved skill playbook.", {}, []),
        lambda **_: {"skills": [s.as_dict() for s in skills.all()]},
    )
    box.register(
        _fn(
            "read_skill",
            "Read the full steps of a saved skill by name.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        lambda name, **_: (s.as_dict(with_body=True) if (s := skills.get(name)) else
                           {"error": f"no skill named '{name}'"}),
    )
    box.register(
        _fn(
            "save_skill",
            "Save a reusable playbook for a kind of task you just solved. Only when the user "
            "asks, or the task took several steps and will clearly come up again.",
            {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "triggers": {"type": "array", "items": {"type": "string"}},
            },
            ["name", "description", "steps"],
        ),
        lambda name, description, steps, triggers=None, **_: {
            "saved": skills.save(name, description, steps, triggers, overwrite=True,
                                 source="learned").name
        },
    )
    if store is not None:
        box.register(
            _fn(
                "follow_topic",
                "Add a topic the user wants fresh news about in their daily brief.",
                {"topic": {"type": "string"}},
                ["topic"],
            ),
            lambda topic, **_: {"following": store.add_topic(topic), "all": store.topics()},
        )
    if google is not None:
        from app.tools.google import CALENDAR_SPEC, GMAIL_SPEC

        box.register(GMAIL_SPEC, lambda query="is:unread newer_than:2d", max_results=8, **_:
                     google.gmail_recent(query, max_results))
        box.register(CALENDAR_SPEC, lambda day=None, **_: google.calendar_today(day))
    return box
