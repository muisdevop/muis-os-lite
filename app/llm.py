"""LLM client: the OpenAI SDK pointed at Nebius Token Factory.

Two tiers of NVIDIA Nemotron open models:
  * "fast" -> Nemotron Nano  (quick calls: reflection, summaries, extraction)
  * "plan" -> Nemotron Super (planning and the tool-calling agent loop)
Embeddings go through a Token Factory embedding model. Every model ID is set by env.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.config import Settings

Tier = Literal["fast", "plan"]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str | None) -> str:
    """Remove any <think>...</think> reasoning block a model may emit inline."""
    return _THINK_RE.sub("", text or "").strip()


class LLM:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.settings.nebius_api_key:
                raise RuntimeError("NEBIUS_API_KEY is not set; add it to your .env")
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.settings.nebius_base_url,
                api_key=self.settings.nebius_api_key,
            )
        return self._client

    def model_for(self, tier: Tier) -> str:
        return self.settings.model_plan if tier == "plan" else self.settings.model_fast

    def chat(
        self,
        messages: list[dict[str, Any]],
        tier: Tier = "fast",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.6,
        **kwargs: Any,
    ) -> Any:
        params: dict[str, Any] = {
            "model": self.model_for(tier),
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return self.client.chat.completions.create(**params)

    def complete(self, prompt: str, tier: Tier = "fast", system: str | None = None) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self.chat(messages, tier=tier)
        return strip_think(resp.choices[0].message.content)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.settings.embed_model, input=texts)
        return [list(item.embedding) for item in resp.data]
