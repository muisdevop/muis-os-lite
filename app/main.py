"""FastAPI entrypoint: JSON + SSE API under /api, the built-in web UI at /."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.agent import Agent
from app.brief import BriefBuilder
from app.config import Settings, get_settings
from app.llm import LLM, LLMUnavailable
from app.memory import MemoryStore, local_embed
from app.skills.loader import SkillLibrary
from app.store import Store
from app.tools import Toolbox, build_toolbox

log = logging.getLogger("muis")
STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "muis_session"


# --- request bodies ----------------------------------------------------------------------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)  # used only without an id


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: str = "note"
    tags: list[str] = Field(default_factory=list)


class SkillIn(BaseModel):
    name: str = Field(min_length=1)
    description: str
    steps: list[str] = Field(min_length=1)
    triggers: list[str] = Field(default_factory=list)


class SkillEdit(BaseModel):
    markdown: str = Field(min_length=1)


class TopicIn(BaseModel):
    topic: str = Field(min_length=1, max_length=80)


class LoginIn(BaseModel):
    password: str


# --- wiring ------------------------------------------------------------------------------
@dataclass
class Services:
    settings: Settings
    llm: LLM
    memory: MemoryStore
    skills: SkillLibrary
    store: Store
    toolbox: Toolbox
    agent: Agent
    brief: BriefBuilder
    google: Any | None = None
    embedder: str = "local"


def build_services(
    settings: Settings,
    llm: LLM | None = None,
    memory: MemoryStore | None = None,
    store: Store | None = None,
    tavily_client: Any | None = None,
    google: Any | None = None,
) -> Services:
    llm = llm or LLM(settings)
    remote = settings.embed_provider == "remote" or (
        settings.embed_provider == "auto" and llm.available
    )
    embedder = f"remote ({settings.embed_model})" if remote else "local (hashing, offline)"
    if memory is None:
        memory = MemoryStore(
            embed_fn=llm.embed if remote else local_embed,
            url=settings.qdrant_url,
            collection=settings.qdrant_collection if remote else f"{settings.qdrant_collection}_local",
            api_key=settings.qdrant_api_key,
            dim=settings.embed_dim if remote else 0,
            path=settings.data_dir / "qdrant",
        )
    store = store or Store(settings.db_path)
    if google is None and settings.google_configured:
        from app.tools.google import GoogleClient

        google = GoogleClient(settings.google_client_id, settings.google_client_secret,
                              settings.google_refresh_token, tz=settings.timezone)
    skills = SkillLibrary(settings.skills_dir)
    toolbox = build_toolbox(memory, skills, tavily_api_key=settings.tavily_api_key,
                            tavily_client=tavily_client, store=store, google=google)
    agent = Agent(llm, memory, skills, toolbox, max_steps=settings.max_agent_steps,
                  auto_learn=settings.auto_learn_skills, timezone=settings.timezone)
    brief = BriefBuilder(llm, memory, skills, store, tavily_api_key=settings.tavily_api_key,
                         tavily_client=tavily_client, google=google, timezone=settings.timezone,
                         default_topics=settings.brief_topics)
    return Services(settings, llm, memory, skills, store, toolbox, agent, brief, google, embedder)


def _session_token(password: str) -> str:
    return hmac.new(password.encode(), b"muis-os-lite-session-v1", hashlib.sha256).hexdigest()


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str, ensure_ascii=False)}\n\n"


def create_app(services: Services | None = None, settings: Settings | None = None) -> FastAPI:
    settings = services.settings if services else (settings or get_settings())
    svc = services or build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if settings.enable_scheduler:
            try:
                from app.scheduler import start_scheduler

                scheduler = start_scheduler(lambda: svc.brief.build("scheduled"),
                                            settings.daily_brief_time, settings.timezone)
            except Exception as exc:  # never block startup on the scheduler
                log.warning("scheduler not started: %s", exc)
        app.state.scheduler = scheduler
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="MUIS OS Lite", version=__version__, lifespan=lifespan)
    app.state.services = svc

    # --- optional password auth ----------------------------------------------------------
    password = settings.app_password
    token = _session_token(password) if password else ""
    public_api = {"/api/login", "/api/logout", "/api/auth"}

    def _authed(request: Request) -> bool:
        if not password:
            return True
        if hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), token):
            return True
        auth = request.headers.get("authorization", "")
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], password)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in public_api and not _authed(request):
            return JSONResponse({"detail": "login required"}, status_code=401)
        return await call_next(request)

    @app.get("/api/auth")
    def auth_state(request: Request) -> dict[str, bool]:
        return {"required": bool(password), "authenticated": _authed(request)}

    @app.post("/api/login")
    def login(body: LoginIn) -> JSONResponse:
        if not password:
            return JSONResponse({"ok": True})
        if not hmac.compare_digest(body.password, password):
            raise HTTPException(status_code=401, detail="wrong password")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30)
        return resp

    @app.post("/api/logout")
    def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # --- health --------------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        try:
            memory_count = svc.memory.count()
            memory_ok = True
        except Exception:
            memory_count, memory_ok = 0, False
        return {
            "status": "ok",
            "version": __version__,
            "provider": settings.provider_label,
            "base_url": settings.nebius_base_url,
            "models": {
                "fast": settings.model_fast,
                "plan": settings.model_plan,
                "embedding": settings.embed_model,
            },
            "configured": {
                "nebius": bool(settings.nebius_api_key) or svc.llm._client is not None,
                "tavily": bool(settings.tavily_api_key) or svc.brief.tavily_client is not None,
                "google": svc.google is not None,
                "auth": bool(password),
            },
            "memory": {"ok": memory_ok, "count": memory_count, "backend": svc.memory.mode,
                       "embedder": svc.embedder, "note": svc.memory.fallback_reason},
            "skills": len(svc.skills.all()),
            "scheduler": {
                "enabled": settings.enable_scheduler,
                "daily_brief_time": settings.daily_brief_time,
                "timezone": settings.timezone,
            },
            "tools": svc.toolbox.names,
        }

    # --- chat ----------------------------------------------------------------------------
    def _prepare(body: ChatIn) -> tuple[str, list[dict[str, str]]]:
        if body.conversation_id and svc.store.get_conversation(body.conversation_id):
            cid = body.conversation_id
            history = svc.store.history(cid)
        else:
            cid = svc.store.create_conversation(body.message)
            history = body.history[-20:]
        svc.store.add_message(cid, "user", body.message)
        return cid, history

    def _persist(cid: str, done: dict[str, Any] | None, error: str | None) -> None:
        if done is not None:
            svc.store.add_message(cid, "assistant", done["reply"], {
                "tool_calls": done["tool_calls"], "skill_saved": done["skill_saved"],
                "skills_matched": done["skills_matched"],
            })
        elif error:
            svc.store.add_message(cid, "error", error)

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        if not svc.llm.available:
            raise HTTPException(status_code=503, detail=str(LLMUnavailable(
                "NEBIUS_API_KEY is not set; add it to your .env to enable chat")))
        cid, history = _prepare(body)
        try:
            result = svc.agent.run(body.message, history)
        except LLMUnavailable as exc:
            _persist(cid, None, str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            _persist(cid, None, str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        done = {"reply": result.reply, "tool_calls": result.tool_calls,
                "skill_saved": result.skill_saved, "skills_matched": result.skills_matched}
        _persist(cid, done, None)
        return {**done, "conversation_id": cid, "memories_used": result.memories_used}

    @app.post("/api/chat/stream")
    def chat_stream(body: ChatIn) -> StreamingResponse:
        cid, history = _prepare(body)

        def events() -> Iterator[str]:
            yield _sse({"type": "conversation", "id": cid})
            done, error = None, None
            for ev in svc.agent.stream(body.message, history):
                if ev["type"] == "done":
                    done = ev
                elif ev["type"] == "error":
                    error = ev["message"]
                yield _sse(ev)
            _persist(cid, done, error)

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    @app.get("/api/conversations")
    def conversations() -> dict[str, Any]:
        return {"conversations": svc.store.list_conversations()}

    @app.get("/api/conversations/{cid}")
    def conversation(cid: str) -> dict[str, Any]:
        conv = svc.store.get_conversation(cid)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conv

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: str) -> dict[str, bool]:
        if not svc.store.delete_conversation(cid):
            raise HTTPException(status_code=404, detail="conversation not found")
        return {"deleted": True}

    # --- memory --------------------------------------------------------------------------
    def _memory_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"memory unavailable: {exc}") from exc

    @app.post("/api/memory")
    def add_memory(body: MemoryIn) -> dict[str, str]:
        return {"id": _memory_call(svc.memory.add, body.text, kind=body.kind, tags=body.tags)}

    @app.get("/api/memory")
    def get_memory(q: str | None = None, k: int = 10, kind: str | None = None) -> dict[str, Any]:
        if q:
            items = _memory_call(svc.memory.recall, q, k=max(1, min(k, 50)), min_score=0.01)
        else:
            items = _memory_call(svc.memory.recent, limit=200, kind=kind)
        return {"memories": items, "total": _memory_call(svc.memory.count)}

    @app.delete("/api/memory/{point_id}")
    def delete_memory(point_id: str) -> dict[str, bool]:
        if not _memory_call(svc.memory.delete, point_id):
            raise HTTPException(status_code=404, detail="memory not found")
        return {"deleted": True}

    # --- skills --------------------------------------------------------------------------
    @app.get("/api/skills")
    def list_skills() -> dict[str, Any]:
        return {"skills": [s.as_dict() for s in svc.skills.all()]}

    @app.get("/api/skills/{name}")
    def get_skill(name: str) -> dict[str, Any]:
        skill = svc.skills.get(name)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        return skill.as_dict(with_body=True)

    @app.post("/api/skills", status_code=201)
    def save_skill(body: SkillIn) -> dict[str, Any]:
        try:
            skill = svc.skills.save(body.name, body.description, body.steps, body.triggers)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return skill.as_dict(with_body=True)

    @app.put("/api/skills/{name}")
    def edit_skill(name: str, body: SkillEdit) -> dict[str, Any]:
        try:
            skill = svc.skills.write_markdown(name, body.markdown)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return skill.as_dict(with_body=True)

    @app.delete("/api/skills/{name}")
    def delete_skill(name: str) -> dict[str, bool]:
        if not svc.skills.delete(name):
            raise HTTPException(status_code=404, detail="skill not found")
        return {"deleted": True}

    # --- brief + topics ------------------------------------------------------------------
    @app.post("/api/brief")
    def make_brief() -> dict[str, Any]:
        return svc.brief.build("manual")

    @app.get("/api/brief/latest")
    def latest_brief() -> dict[str, Any]:
        briefs = svc.store.list_briefs(limit=1)
        return {"brief": briefs[0] if briefs else None}

    @app.get("/api/briefs")
    def list_briefs() -> dict[str, Any]:
        return {"briefs": svc.store.list_briefs()}

    @app.get("/api/topics")
    def topics() -> dict[str, Any]:
        return {"topics": svc.store.topics(), "defaults": settings.brief_topics}

    @app.post("/api/topics", status_code=201)
    def add_topic(body: TopicIn) -> dict[str, Any]:
        svc.store.add_topic(body.topic)
        return {"topics": svc.store.topics()}

    @app.delete("/api/topics/{name}")
    def remove_topic(name: str) -> dict[str, Any]:
        if not svc.store.remove_topic(name):
            raise HTTPException(status_code=404, detail="topic not found")
        return {"topics": svc.store.topics()}

    # --- web UI --------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _lazy_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return create_app()


app = _lazy_app()
