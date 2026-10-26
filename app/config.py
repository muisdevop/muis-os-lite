"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Nebius Token Factory, OpenAI-compatible endpoint.
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# NVIDIA Nemotron open models served on Token Factory (IDs from the public model catalog).
DEFAULT_MODEL_FAST = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # Nemotron 3 Nano: fast calls
DEFAULT_MODEL_PLAN = "nvidia/nemotron-3-super-120b-a12b"  # Nemotron 3 Super: planning + tool use
DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"  # Token Factory embedding model

DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"
DEFAULT_DATA_DIR = Path(os.environ.get("HOME", ".")) / ".muis-os-lite"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # LLM provider (Nebius Token Factory by default; any OpenAI-compatible endpoint works)
    nebius_api_key: str = ""
    nebius_base_url: str = DEFAULT_BASE_URL
    model_fast: str = DEFAULT_MODEL_FAST
    model_plan: str = DEFAULT_MODEL_PLAN
    llm_extra_body: dict = field(default_factory=dict)
    # Embeddings (default: same provider as the LLM)
    embed_model: str = DEFAULT_EMBED_MODEL
    embed_dim: int = 0  # 0 = detect from the first embedding returned
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_provider: str = "auto"  # auto | remote | local (offline hashing embedder)
    # Tools
    tavily_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    # Storage
    qdrant_url: str = ""  # "" = embedded Qdrant on disk in DATA_DIR; ":memory:" = RAM only
    qdrant_api_key: str = ""
    qdrant_collection: str = "muis_memory"
    data_dir: Path = DEFAULT_DATA_DIR
    skills_dir: Path = DEFAULT_SKILLS_DIR
    # Agent
    max_agent_steps: int = 6
    auto_learn_skills: bool = True
    # Daily brief
    daily_brief_time: str = "08:00"
    timezone: str = "Asia/Karachi"
    enable_scheduler: bool = False
    brief_topics: list[str] = field(default_factory=list)
    # Web UI
    app_password: str = ""
    provider_label: str = "Nebius Token Factory"

    @property
    def llm_configured(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "muis.db"


def get_settings() -> Settings:
    env = os.environ.get
    base_url = env("LLM_BASE_URL") or env("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
    api_key = env("NEBIUS_API_KEY") or env("LLM_API_KEY") or ""
    label = "Nebius Token Factory" if "nebius" in base_url else "OpenAI-compatible"
    return Settings(
        nebius_api_key=api_key,
        nebius_base_url=base_url,
        model_fast=env("NEMOTRON_FAST_MODEL") or DEFAULT_MODEL_FAST,
        model_plan=env("NEMOTRON_PLAN_MODEL") or DEFAULT_MODEL_PLAN,
        llm_extra_body=_json(env("LLM_EXTRA_BODY")),
        embed_model=env("EMBEDDING_MODEL") or DEFAULT_EMBED_MODEL,
        embed_dim=_int(env("EMBEDDING_DIM"), 0),
        embed_base_url=env("EMBEDDING_BASE_URL", "") or "",
        embed_api_key=env("EMBEDDING_API_KEY", "") or "",
        embed_provider=(env("EMBEDDING_PROVIDER") or "auto").lower(),
        tavily_api_key=env("TAVILY_API_KEY", "") or "",
        google_client_id=env("GOOGLE_CLIENT_ID", "") or "",
        google_client_secret=env("GOOGLE_CLIENT_SECRET", "") or "",
        google_refresh_token=env("GOOGLE_REFRESH_TOKEN", "") or "",
        qdrant_url=env("QDRANT_URL", "") or "",
        qdrant_api_key=env("QDRANT_API_KEY", "") or "",
        qdrant_collection=env("QDRANT_COLLECTION") or "muis_memory",
        data_dir=Path(env("DATA_DIR") or DEFAULT_DATA_DIR),
        skills_dir=Path(env("SKILLS_DIR") or DEFAULT_SKILLS_DIR),
        max_agent_steps=_int(env("MAX_AGENT_STEPS"), 6),
        auto_learn_skills=_bool(env("AUTO_LEARN_SKILLS"), True),
        daily_brief_time=env("DAILY_BRIEF_TIME") or "08:00",
        timezone=env("TZ_NAME") or "Asia/Karachi",
        enable_scheduler=_bool(env("ENABLE_SCHEDULER"), True),
        brief_topics=_list(env("BRIEF_TOPICS")),
        app_password=env("APP_PASSWORD", "") or "",
        provider_label=label,
    )
