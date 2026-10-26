"""LLM client: the OpenAI SDK pointed at Nebius Token Factory.

Two tiers of NVIDIA Nemotron open models:
  * "fast" -> Nemotron Nano  (quick calls: skill reflection, summaries, the daily brief)
  * "plan" -> Nemotron Super (planning and the tool-calling agent loop)
Embeddings go through a Token Factory embedding model. Every model ID is set by env, and the
base URL can point at any OpenAI-compatible endpoint (LLM_BASE_URL) for local testing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from app.config import Settings

Tier = Literal["fast", "plan"]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMUnavailable(RuntimeError):
    """Raised when no API key is configured for the model provider."""


def strip_think(text: str | None) -> str:
    """Remove any <think>...</think> reasoning block a model may emit inline."""
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:  # opening tag was in the prompt template; drop the leftover
        text = text.split("</think>", 1)[1]
    return text.strip()


class ThinkFilter:
    """Streaming filter that separates <think>...</think> reasoning from answer text."""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, chunk: str) -> tuple[str, str]:
        """Returns (answer_text, thinking_text) that is safe to emit now."""
        self.buf += chunk
        answer, thinking = "", ""
        while self.buf:
            tag = "</think>" if self.inside else "<think>"
            idx = self.buf.find(tag)
            if idx >= 0:
                part, self.buf = self.buf[:idx], self.buf[idx + len(tag):]
                if self.inside:
                    thinking += part
                else:
                    answer += part
                self.inside = not self.inside
                continue
            # keep a tail that might be the start of a tag
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            emit, self.buf = self.buf[: len(self.buf) - keep], self.buf[len(self.buf) - keep:]
            if self.inside:
                thinking += emit
            else:
                answer += emit
            break
        return answer, thinking

    def flush(self) -> tuple[str, str]:
        rest, self.buf = self.buf, ""
        return ("", rest) if self.inside else (rest, "")


@dataclass
class StreamEvent:
    kind: Literal["token", "thinking", "message"]
    text: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class LLM:
    def __init__(self, settings: Settings, client: Any | None = None,
                 embed_client: Any | None = None):
        self.settings = settings
        self._client = client
        self._embed_client = embed_client

    @property
    def available(self) -> bool:
        return self._client is not None or bool(self.settings.nebius_api_key)

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.settings.nebius_api_key:
                raise LLMUnavailable(
                    "NEBIUS_API_KEY is not set; add it to your .env to enable chat"
                )
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.settings.nebius_base_url,
                api_key=self.settings.nebius_api_key,
                timeout=120,
            )
        return self._client

    @property
    def embed_client(self) -> Any:
        if self._embed_client is not None:
            return self._embed_client
        if self.settings.embed_base_url:
            from openai import OpenAI

            self._embed_client = OpenAI(
                base_url=self.settings.embed_base_url,
                api_key=self.settings.embed_api_key or self.settings.nebius_api_key,
            )
            return self._embed_client
        return self.client

    def model_for(self, tier: Tier) -> str:
        return self.settings.model_plan if tier == "plan" else self.settings.model_fast

    def _params(self, messages, tier, tools, temperature, **kwargs) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model_for(tier),
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if self.settings.llm_extra_body:
            params["extra_body"] = self.settings.llm_extra_body
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return params

    def chat(
        self,
        messages: list[dict[str, Any]],
        tier: Tier = "fast",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.6,
        **kwargs: Any,
    ) -> Any:
        return self.client.chat.completions.create(
            **self._params(messages, tier, tools, temperature, **kwargs)
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tier: Tier = "plan",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.6,
    ) -> Iterator[StreamEvent]:
        """Stream one assistant turn.

        Yields `token` / `thinking` events as text arrives, then exactly one `message` event
        holding the full answer text and any tool calls assembled from the deltas.
        """
        resp = self.client.chat.completions.create(
            **self._params(messages, tier, tools, temperature, stream=True)
        )
        think = ThinkFilter()
        content = ""
        calls: dict[int, dict[str, Any]] = {}
        for chunk in resp:
            choices = _get(chunk, "choices") or []
            if not choices:
                continue
            delta = _get(choices[0], "delta")
            if delta is None:
                continue
            reasoning = _get(delta, "reasoning_content") or _get(delta, "reasoning")
            if reasoning:
                yield StreamEvent("thinking", text=reasoning)
            text = _get(delta, "content")
            if text:
                answer, thinking = think.feed(text)
                if thinking:
                    yield StreamEvent("thinking", text=thinking)
                if answer:
                    content += answer
                    yield StreamEvent("token", text=answer)
            for tc in _get(delta, "tool_calls") or []:
                idx = _get(tc, "index", 0) or 0
                slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if _get(tc, "id"):
                    slot["id"] = _get(tc, "id")
                fn = _get(tc, "function")
                if fn is not None:
                    if _get(fn, "name"):
                        slot["name"] += _get(fn, "name")
                    if _get(fn, "arguments"):
                        slot["arguments"] += _get(fn, "arguments")
        answer, thinking = think.flush()
        if thinking:
            yield StreamEvent("thinking", text=thinking)
        if answer:
            content += answer
            yield StreamEvent("token", text=answer)
        tool_calls = []
        for i, slot in sorted(calls.items()):
            if not slot["name"]:
                continue
            tool_calls.append({
                "id": slot["id"] or f"call_{i}",
                "name": slot["name"],
                "arguments": slot["arguments"] or "{}",
            })
        yield StreamEvent("message", content=content.strip(), tool_calls=tool_calls)

    def complete(self, prompt: str, tier: Tier = "fast", system: str | None = None,
                 temperature: float = 0.4) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self.chat(messages, tier=tier, temperature=temperature)
        return strip_think(resp.choices[0].message.content)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.embed_client.embeddings.create(model=self.settings.embed_model, input=texts)
        return [list(item.embedding) for item in resp.data]


def parse_json_object(raw: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates prose and code fences)."""
    raw = strip_think(raw)
    start = raw.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(raw[start : i + 1])
                        return data if isinstance(data, dict) else {}
                    except json.JSONDecodeError:
                        break
        start = raw.find("{", start + 1)
    return {}
