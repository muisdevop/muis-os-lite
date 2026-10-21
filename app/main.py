"""FastAPI entrypoint: /chat, /memory, /skills, /health."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import __version__
from app.agent import Agent
from app.config import Settings, get_settings
from app.llm import LLM
from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.tools import build_toolbox


class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] = Field(default_factory=list)


class MemoryIn(BaseModel):
    text: str = Field(min_length=1)
    kind: str = "note"
    tags: list[str] = Field(default_factory=list)


class SkillIn(BaseModel):
    name: str = Field(min_length=1)
    description: str
    steps: list[str] = Field(min_length=1)
    triggers: list[str] = Field(default_factory=list)


def build_agent(settings: Settings) -> Agent:
    llm = LLM(settings)
    memory = MemoryStore(
        embed_fn=llm.embed,
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        api_key=settings.qdrant_api_key,
        dim=settings.embed_dim,
    )
    skills = SkillLibrary(settings.skills_dir)
    toolbox = build_toolbox(memory, skills, tavily_api_key=settings.tavily_api_key)
    return Agent(
        llm, memory, skills, toolbox,
        max_steps=settings.max_agent_steps, auto_learn=settings.auto_learn_skills,
    )


def create_app(agent: Agent | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    agent = agent or build_agent(settings)
    app = FastAPI(title="MUIS OS Lite", version=__version__)
    app.state.agent = agent
    app.state.settings = settings

    def _unavailable(exc: Exception) -> HTTPException:
        return HTTPException(status_code=503, detail=str(exc))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "provider": "nebius-token-factory",
            "base_url": settings.nebius_base_url,
            "models": {
                "fast": settings.model_fast,
                "plan": settings.model_plan,
                "embedding": settings.embed_model,
            },
            "configured": {
                "nebius": bool(settings.nebius_api_key),
                "tavily": bool(settings.tavily_api_key),
            },
            "tools": agent.toolbox.names,
        }

    @app.post("/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        try:
            result = agent.run(body.message, body.history)
        except RuntimeError as exc:
            raise _unavailable(exc) from exc
        return {
            "reply": result.reply,
            "tool_calls": result.tool_calls,
            "memories_used": result.memories_used,
            "skill_saved": result.skill_saved,
        }

    @app.post("/memory")
    def add_memory(body: MemoryIn) -> dict[str, str]:
        try:
            return {"id": agent.memory.add(body.text, kind=body.kind, tags=body.tags)}
        except RuntimeError as exc:
            raise _unavailable(exc) from exc

    @app.get("/memory")
    def get_memory(q: str | None = None, k: int = 5) -> dict[str, Any]:
        try:
            items = agent.memory.recall(q, k=k) if q else agent.memory.recent()
        except RuntimeError as exc:
            raise _unavailable(exc) from exc
        return {"memories": items}

    @app.get("/skills")
    def list_skills() -> dict[str, Any]:
        return {"skills": [s.as_dict() for s in agent.skills.all()]}

    @app.get("/skills/{name}")
    def get_skill(name: str) -> dict[str, Any]:
        skill = agent.skills.get(name)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        return skill.as_dict(with_body=True)

    @app.post("/skills", status_code=201)
    def save_skill(body: SkillIn) -> dict[str, Any]:
        try:
            skill = agent.skills.save(body.name, body.description, body.steps, body.triggers)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return skill.as_dict(with_body=True)

    return app


app = create_app()
