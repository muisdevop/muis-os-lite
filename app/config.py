"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Nebius Token Factory, OpenAI-compatible endpoint.
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# NVIDIA Nemotron open models served on Token Factory (IDs from the public model catalog).
DEFAULT_MODEL_FAST = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # Nemotron 3 Nano: fast calls
DEFAULT_MODEL_PLAN = "nvidia/nemotron-3-super-120b-a12b"  # Nemotron 3 Super: planning + tool use
DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"  # Token Factory embedding model

DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str = ""
    nebius_base_url: str = DEFAULT_BASE_URL
    model_fast: str = DEFAULT_MODEL_FAST
    model_plan: str = DEFAULT_MODEL_PLAN
    embed_model: str = DEFAULT_EMBED_MODEL
    embed_dim: int = 0  # 0 = detect from the first embedding returned
    tavily_api_key: str = ""
    qdrant_url: str = ":memory:"  # ":memory:" runs an in-process Qdrant (dev/tests)
    qdrant_api_key: str = ""
    qdrant_collection: str = "muis_memory"
    skills_dir: Path = DEFAULT_SKILLS_DIR
    max_agent_steps: int = 6
    auto_learn_skills: bool = True
    daily_brief_time: str = "08:00"
    timezone: str = "Asia/Karachi"


def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        nebius_api_key=env("NEBIUS_API_KEY", ""),
        nebius_base_url=env("NEBIUS_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        model_fast=env("NEMOTRON_FAST_MODEL", DEFAULT_MODEL_FAST) or DEFAULT_MODEL_FAST,
        model_plan=env("NEMOTRON_PLAN_MODEL", DEFAULT_MODEL_PLAN) or DEFAULT_MODEL_PLAN,
        embed_model=env("EMBEDDING_MODEL", DEFAULT_EMBED_MODEL) or DEFAULT_EMBED_MODEL,
        embed_dim=_int(env("EMBEDDING_DIM"), 0),
        tavily_api_key=env("TAVILY_API_KEY", ""),
        qdrant_url=env("QDRANT_URL", ":memory:") or ":memory:",
        qdrant_api_key=env("QDRANT_API_KEY", ""),
        qdrant_collection=env("QDRANT_COLLECTION", "muis_memory") or "muis_memory",
        skills_dir=Path(env("SKILLS_DIR", "") or DEFAULT_SKILLS_DIR),
        max_agent_steps=_int(env("MAX_AGENT_STEPS"), 6),
        auto_learn_skills=_bool(env("AUTO_LEARN_SKILLS"), True),
        daily_brief_time=env("DAILY_BRIEF_TIME", "08:00") or "08:00",
        timezone=env("TZ_NAME", "Asia/Karachi") or "Asia/Karachi",
    )
