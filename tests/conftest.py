"""Offline fakes: no Nebius, Tavily, Google or Qdrant server is ever contacted."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Keep `app.main`'s module-level app hermetic: temp data dir, RAM Qdrant, no scheduler, no keys.
_TMP = tempfile.mkdtemp(prefix="muis-test-")
os.environ.update({"DATA_DIR": _TMP, "QDRANT_URL": ":memory:", "ENABLE_SCHEDULER": "false"})
for _var in ("NEBIUS_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "TAVILY_API_KEY", "APP_PASSWORD",
             "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "BRIEF_TOPICS"):
    os.environ.pop(_var, None)

import pytest  # noqa: E402

from app.config import DEFAULT_SKILLS_DIR, Settings  # noqa: E402
from app.llm import LLM  # noqa: E402
from app.main import build_services, create_app  # noqa: E402
from app.memory import MemoryStore  # noqa: E402
from app.store import Store  # noqa: E402

DIM = 32


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words hash embedding, good enough for recall tests."""
    out = []
    for text in texts:
        vec = [0.0] * DIM
        for word in text.lower().split():
            h = int(hashlib.md5(word.strip(".,!?'\"").encode()).hexdigest(), 16)
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


def chunk(content=None, tool_calls=None, reasoning=None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def to_chunks(resp: SimpleNamespace) -> list[SimpleNamespace]:
    """Turn a scripted non-streaming completion into OpenAI-style stream deltas."""
    msg = resp.choices[0].message
    out = []
    text = msg.content or ""
    for i in range(0, len(text), 7):
        out.append(chunk(content=text[i:i + 7]))
    for idx, tc in enumerate(msg.tool_calls or []):
        args = tc.function.arguments
        half = len(args) // 2
        out.append(chunk(tool_calls=[SimpleNamespace(
            index=idx, id=tc.id, type="function",
            function=SimpleNamespace(name=tc.function.name, arguments=args[:half]))]))
        out.append(chunk(tool_calls=[SimpleNamespace(
            index=idx, id=None, type=None,
            function=SimpleNamespace(name=None, arguments=args[half:]))]))
    out.append(SimpleNamespace(choices=[]))  # usage-only trailing chunk
    return out


class FakeCompletions:
    """Replays scripted responses (streamed when asked) and records every request."""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0) if self.script else completion('{"save": false}')
        if isinstance(item, Exception):
            raise item
        if kwargs.get("stream"):
            return iter(item if isinstance(item, list) else to_chunks(item))
        return item


class FakeOpenAI:
    def __init__(self, script: list | None = None):
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
        q = kwargs.get("query", "")
        return {
            "answer": f"Latest on {q}: Nemotron 3 Super is a 120B hybrid MoE model.",
            "results": [
                {"title": f"{q} news", "url": "https://example.com/nemotron", "content": "x" * 900}
            ],
        }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    skills_dir = tmp_path / "skills"
    shutil.copytree(DEFAULT_SKILLS_DIR, skills_dir,
                    ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    return Settings(skills_dir=skills_dir, data_dir=tmp_path / "data", qdrant_url=":memory:",
                    nebius_api_key="", tavily_api_key="", enable_scheduler=False)


@pytest.fixture
def memory() -> MemoryStore:
    return MemoryStore(embed_fn=fake_embed, url=":memory:", collection="test_memory")


@pytest.fixture
def make_services(settings: Settings, memory: MemoryStore):
    """Full service graph with a scripted Nemotron, fake Tavily and in-RAM stores."""

    def _make(script: list | None = None, auto_learn: bool = True, llm: bool = True,
              google=None, **overrides):
        s = Settings(**{**settings.__dict__, "auto_learn_skills": auto_learn,
                        "max_agent_steps": 4, **overrides})
        client = FakeOpenAI(script) if llm else None
        svc = build_services(s, llm=LLM(s, client=client), memory=memory, store=Store(":memory:"),
                             tavily_client=FakeTavily(), google=google)
        svc.fake_client = client
        return svc

    return _make


@pytest.fixture
def make_agent(make_services):
    def _make(script: list | None = None, auto_learn: bool = True):
        svc = make_services(script, auto_learn=auto_learn)
        agent = svc.agent
        agent.fake_client = svc.fake_client
        agent.fake_tavily = svc.brief.tavily_client
        return agent

    return _make


@pytest.fixture
def make_client(make_services):
    from fastapi.testclient import TestClient

    def _make(script: list | None = None, **kwargs):
        svc = make_services(script, **kwargs)
        return TestClient(create_app(services=svc)), svc

    return _make


def sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
