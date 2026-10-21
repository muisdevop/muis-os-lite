from datetime import datetime, timezone

import pytest

from app.config import DEFAULT_BASE_URL, DEFAULT_MODEL_FAST, DEFAULT_MODEL_PLAN, Settings, get_settings
from app.llm import LLM, strip_think
from app.scheduler import next_run
from app.skills.loader import SkillLibrary, parse_skill, slugify
from app.tools.integrations import STUB_TOOLS
from app.tools.tavily_search import tavily_search
from tests.conftest import FakeOpenAI, FakeTavily, completion


def test_settings_defaults_point_at_token_factory(monkeypatch):
    for var in ("NEBIUS_BASE_URL", "NEMOTRON_FAST_MODEL", "NEMOTRON_PLAN_MODEL"):
        monkeypatch.delenv(var, raising=False)
    s = get_settings()
    assert s.nebius_base_url == DEFAULT_BASE_URL == "https://api.tokenfactory.nebius.com/v1/"
    assert s.model_fast == DEFAULT_MODEL_FAST and "Nemotron" in s.model_fast
    assert s.model_plan == DEFAULT_MODEL_PLAN and "nemotron" in s.model_plan


def test_settings_models_overridable_by_env(monkeypatch):
    monkeypatch.setenv("NEMOTRON_PLAN_MODEL", "nvidia/custom-super")
    monkeypatch.setenv("EMBEDDING_DIM", "4096")
    s = get_settings()
    assert s.model_plan == "nvidia/custom-super"
    assert s.embed_dim == 4096


def test_llm_routes_tiers_to_models():
    client = FakeOpenAI([completion("hi"), completion("<think>hmm</think>planned")])
    llm = LLM(Settings(), client=client)
    assert llm.complete("hello", tier="fast") == "hi"
    assert llm.complete("plan it", tier="plan") == "planned"
    reqs = client.chat.completions.requests
    assert reqs[0]["model"] == DEFAULT_MODEL_FAST
    assert reqs[1]["model"] == DEFAULT_MODEL_PLAN


def test_llm_requires_key_without_injected_client():
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        LLM(Settings(nebius_api_key="")).client


def test_llm_embed_uses_configured_model():
    client = FakeOpenAI()
    llm = LLM(Settings(embed_model="Qwen/Qwen3-Embedding-8B"), client=client)
    vecs = llm.embed(["a note", "another"])
    assert len(vecs) == 2
    assert client.embedding_requests[0]["model"] == "Qwen/Qwen3-Embedding-8B"


def test_strip_think():
    assert strip_think("<think>secret</think> Answer") == "Answer"
    assert strip_think(None) == ""


def test_memory_add_and_recall(memory):
    assert memory.recall("anything") == []  # empty store is fine
    memory.add("My sister Ayesha's birthday is on March 3", kind="person")
    memory.add("I prefer green tea in the morning", kind="preference")
    hits = memory.recall("When is Ayesha's birthday?", k=1)
    assert hits and "Ayesha" in hits[0]["text"]
    assert len(memory.recent()) == 2
    with pytest.raises(ValueError):
        memory.add("   ")


def test_skill_library_parse_match_save(tmp_path):
    lib = SkillLibrary(tmp_path)
    assert lib.all() == [] and lib.index_text() == "(no skills yet)"
    skill = lib.save("Book a Dentist!", "Find and book a dentist", ["Search", "Compare", "Book"],
                     triggers=["dentist"])
    assert skill.name == "book-a-dentist"
    assert lib.get("book a dentist").body.startswith("1. Search")
    assert lib.match("can you find me a dentist nearby")[0].name == "book-a-dentist"
    with pytest.raises(FileExistsError):
        lib.save("book-a-dentist", "dup", ["x"])
    parsed = parse_skill("no front matter here")
    assert parsed.name == "skill" and parsed.body == "no front matter here"
    assert slugify("  Hello World  ") == "hello-world"


def test_bundled_skills_load(settings):
    names = {s.name for s in SkillLibrary(settings.skills_dir).all()}
    assert {"research-and-summarize", "remember-a-fact", "daily-brief"} <= names


def test_tavily_search_shapes_results():
    fake = FakeTavily()
    out = tavily_search("nemotron", max_results=3, client=fake)
    assert fake.calls[0] == {"query": "nemotron", "max_results": 3, "include_answer": True}
    assert out["answer"].startswith("Nemotron")
    assert out["results"][0]["url"] == "https://example.com/nemotron"
    assert len(out["results"][0]["content"]) == 500


def test_tavily_search_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert "error" in tavily_search("x")


def test_integration_stubs_are_todo():
    for fn in STUB_TOOLS.values():
        assert fn.__name__ in STUB_TOOLS
    assert STUB_TOOLS["gmail_search"]("from:boss")["status"] == "not_implemented"


def test_scheduler_next_run():
    now = datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)  # 07:00 PKT
    assert next_run(now, "08:00").hour == 8 and next_run(now, "08:00").day == 21
    assert next_run(now, "06:00").day == 22
