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
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError:
            return {"error": "tool arguments were not valid JSON"}
        try:
            return handler(**args)
        except Exception as exc:  # report tool failures back to the model
            return {"error": f"{type(exc).__name__}: {exc}"}


def build_toolbox(
    memory: MemoryStore,
    skills: SkillLibrary,
    tavily_api_key: str | None = None,
    tavily_client: Any | None = None,
) -> Toolbox:
    box = Toolbox()

    box.register(
        WEB_SEARCH_SPEC,
        lambda query, max_results=5: tavily_search(
            query, max_results, api_key=tavily_api_key, client=tavily_client
        ),
    )
    box.register(
        _fn(
            "remember",
            "Save a durable fact about the user's life to long-term memory.",
            {
                "text": {"type": "string"},
                "kind": {"type": "string", "description": "person|preference|plan|project|note"},
            },
            ["text"],
        ),
        lambda text, kind="note", tags=None: {"saved": memory.add(text, kind=kind, tags=tags)},
    )
    box.register(
        _fn(
            "recall",
            "Search long-term memory for notes related to a query.",
            {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}},
            ["query"],
        ),
        lambda query, k=5: {"memories": memory.recall(query, k=k)},
    )
    box.register(
        _fn(
            "read_skill",
            "Read the full steps of a saved skill by name.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        lambda name: (s.as_dict(with_body=True) if (s := skills.get(name)) else
                      {"error": f"no skill named '{name}'"}),
    )
    box.register(
        _fn(
            "save_skill",
            "Save a reusable playbook for a kind of task you just solved.",
            {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "triggers": {"type": "array", "items": {"type": "string"}},
            },
            ["name", "description", "steps"],
        ),
        lambda name, description, steps, triggers=None: {
            "saved": skills.save(name, description, steps, triggers).name
        },
    )
    return box
