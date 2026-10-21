"""Offline fakes: no Nebius, Tavily or Qdrant server is ever contacted."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import Agent
from app.config import DEFAULT_SKILLS_DIR, Settings
from app.llm import LLM
from app.memory import MemoryStore
from app.skills.loader import SkillLibrary
from app.tools import build_toolbox

DIM = 32


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words hash embedding, good enough for recall tests."""
    out = []
    for text in texts:
        vec = [0.0] * DIM
        for word in text.lower().split():
            h = int(hashlib.md5(word.strip(".,!?").encode()).hexdigest(), 16)
            vec[h % DIM] += 1.0
        out.append(vec if any(vec) else [1.0] + [0.0] * (DIM - 1))
    return out


def tool_call(call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def completion(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeCompletions:
    """Replays scripted responses and records every request."""

    def __init__(self, script: list[SimpleNamespace]):
        self.script = list(script)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.script:
            return completion('{"save": false}')
        return self.script.pop(0)


class FakeOpenAI:
    def __init__(self, script: list[SimpleNamespace] | None = None):
        self.chat = SimpleNamespace(completions=FakeCompletions(script or []))
        self.embeddings = SimpleNamespace(create=self._embed)
        self.embedding_requests: list[dict] = []

    def _embed(self, model, input):
        self.embedding_requests.append({"model": model, "input": input})
        texts = input if isinstance(input, list) else [input]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in fake_embed(texts)])


class FakeTavily:
    def __init__(self):
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "answer": "Nemotron 3 Super is a 120B hybrid MoE model.",
            "results": [
                {"title": "Nemotron", "url": "https://example.com/nemotron", "content": "x" * 900}
            ],
        }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    skills_dir = tmp_path / "skills"
    shutil.copytree(DEFAULT_SKILLS_DIR, skills_dir, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    return Settings(skills_dir=skills_dir, nebius_api_key="", tavily_api_key="")


@pytest.fixture
def memory() -> MemoryStore:
    return MemoryStore(embed_fn=fake_embed, url=":memory:", collection="test_memory")


@pytest.fixture
def make_agent(settings: Settings, memory: MemoryStore):
    def _make(script: list[SimpleNamespace] | None = None, auto_learn: bool = True):
        client = FakeOpenAI(script)
        llm = LLM(settings, client=client)
        skills = SkillLibrary(settings.skills_dir)
        tavily = FakeTavily()
        toolbox = build_toolbox(memory, skills, tavily_client=tavily)
        agent = Agent(llm, memory, skills, toolbox, max_steps=4, auto_learn=auto_learn)
        agent.fake_client = client
        agent.fake_tavily = tavily
        return agent

    return _make
