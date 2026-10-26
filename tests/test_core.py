from datetime import datetime, timezone

import pytest

from app.config import DEFAULT_BASE_URL, DEFAULT_MODEL_FAST, DEFAULT_MODEL_PLAN, Settings, get_settings
from app.llm import LLM, strip_think
from app.scheduler import next_run
from app.skills.loader import SkillLibrary, parse_skill, slugify
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
    tavily_search("x", 50, client=fake, topic="news")
    assert fake.calls[1]["max_results"] == 10 and fake.calls[1]["topic"] == "news"
    assert "Nemotron" in out["answer"]
    assert out["results"][0]["url"] == "https://example.com/nemotron"
    assert len(out["results"][0]["content"]) == 500


def test_tavily_search_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert "error" in tavily_search("x")


def test_scheduler_next_run():
    now = datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)  # 07:00 PKT
    assert next_run(now, "08:00").hour == 8 and next_run(now, "08:00").day == 21
    assert next_run(now, "06:00").day == 22


def test_llm_base_url_fallback_and_extra_body(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    s = get_settings()
    assert s.nebius_base_url.startswith("https://generativelanguage")
    assert s.llm_configured and s.provider_label == "OpenAI-compatible"
    client = FakeOpenAI([completion("ok")])
    LLM(s, client=client).complete("hi")
    assert client.chat.completions.requests[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}}


def test_parse_json_object():
    from app.llm import parse_json_object

    assert parse_json_object('Sure ```json\n{"a": {"b": 1}}\n``` done') == {"a": {"b": 1}}
    assert parse_json_object("<think>{bad}</think> {\"save\": false}") == {"save": False}
    assert parse_json_object("nothing here") == {}


def test_local_embedder_recall(tmp_path):
    from app.memory import MemoryStore, local_embed

    vecs = local_embed(["hello world", ""])
    assert len(vecs[0]) == 1024 and abs(sum(v * v for v in vecs[0]) - 1) < 1e-6
    mem = MemoryStore(local_embed, url="", path=tmp_path / "q", collection="t")
    mem.add("My sister Ayesha's birthday is on March 3", kind="person")
    mem.add("The car needs an oil change in December", kind="plan")
    assert "Ayesha" in mem.recall("when is Ayesha's birthday", k=1)[0]["text"]
    assert mem.count() == 2 and mem.mode == "embedded (disk)"
    mem.client.close()


def test_qdrant_server_down_falls_back_to_memory():
    from app.memory import MemoryStore
    from tests.conftest import fake_embed

    mem = MemoryStore(fake_embed, url="http://127.0.0.1:9", collection="t", connect_retries=1)
    mem.add("still works")
    assert mem.mode == "in-memory (fallback)" and mem.count() == 1


def test_skill_edit_and_delete(tmp_path):
    lib = SkillLibrary(tmp_path)
    lib.save("plan-trip", "Plan a trip", ["Ask dates", "Search flights"], ["trip"], source="learned")
    md = lib.get("plan-trip").to_markdown()
    assert "source: learned" in md and "created:" in md
    edited = lib.write_markdown("plan-trip", md.replace("Search flights", "Search trains"))
    assert "trains" in edited.body and edited.source == "learned"
    with pytest.raises(KeyError):
        lib.write_markdown("nope", "x")
    with pytest.raises(ValueError):
        lib.write_markdown("plan-trip", "---\nname: plan-trip\n---\n")
    assert lib.delete("plan-trip") and not lib.delete("plan-trip")


def test_skill_match_needs_real_overlap(tmp_path):
    lib = SkillLibrary(tmp_path)
    lib.save("compare-gpu-prices", "Compare GPU cloud prices", ["x"], ["gpu price"])
    assert lib.match("hello there") == []
    assert lib.match("compare prices of gpus")[0].name == "compare-gpu-prices"


def test_scheduler_start_and_bad_time():
    from app.scheduler import parse_hhmm, start_scheduler

    assert parse_hhmm("25:99") == (8, 0)
    sched = start_scheduler(lambda: None, "07:30", "Asia/Karachi")
    try:
        job = sched.get_job("daily_brief")
        assert job is not None and job.next_run_time.hour == 7 and job.next_run_time.minute == 30
    finally:
        sched.shutdown(wait=False)
