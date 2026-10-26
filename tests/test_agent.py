import json

import pytest

from app.agent import summarize_result
from app.config import DEFAULT_MODEL_FAST, DEFAULT_MODEL_PLAN
from app.llm import LLMUnavailable
from tests.conftest import chunk, completion, tool_call


def test_agent_answers_directly(make_agent):
    agent = make_agent([completion("Hello MUIS!")])
    result = agent.run("hi")
    assert result.reply == "Hello MUIS!"
    assert result.tool_calls == []
    req = agent.fake_client.chat.completions.requests[0]
    assert req["model"] == DEFAULT_MODEL_PLAN and req["stream"] is True
    names = {t["function"]["name"] for t in req["tools"]}
    assert names >= {"web_search", "remember", "recall", "read_skill", "save_skill",
                     "list_skills", "follow_topic"}


def test_stream_event_sequence(make_agent):
    agent = make_agent([
        completion(tool_calls=[tool_call("c1", "web_search", {"query": "nemotron"})]),
        completion("Nemotron is NVIDIA's open model family."),
    ], auto_learn=False)
    events = list(agent.stream("what is nemotron?"))
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "done"
    assert types.index("tool_call") < types.index("tool_result") < types.index("token")
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "web_search" and call["arguments"] == {"query": "nemotron"}
    res = next(e for e in events if e["type"] == "tool_result")
    assert res["ok"] and "https://example.com/nemotron" in res["summary"]
    streamed = "".join(e["text"] for e in events if e["type"] == "token")
    assert streamed == events[-1]["reply"] == "Nemotron is NVIDIA's open model family."


def test_agent_tool_loop_remembers_and_recalls(make_agent):
    agent = make_agent(
        [
            completion(tool_calls=[tool_call("c1", "remember",
                                             {"text": "My dentist is Dr. Khan in Lahore",
                                              "kind": "person"})]),
            completion("Saved: your dentist is Dr. Khan."),
        ],
        auto_learn=False,
    )
    result = agent.run("Remember that my dentist is Dr. Khan in Lahore")
    assert result.reply.startswith("Saved")
    assert result.tool_calls[0]["tool"] == "remember"
    second = agent.fake_client.chat.completions.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "remember"
    agent.fake_client.chat.completions.script.append(completion("Dr. Khan."))
    result = agent.run("Who is my dentist Dr?")
    system = agent.fake_client.chat.completions.requests[-1]["messages"][0]["content"]
    assert "Dr. Khan" in system and result.memories_used >= 1


def test_agent_web_search_then_learns_skill_and_follows_it_next_time(make_agent):
    skill_json = json.dumps({
        "save": True, "name": "compare-gpu-prices", "description": "Compare GPU cloud prices",
        "triggers": ["gpu price", "compare gpu"],
        "steps": ["1. Search each provider with web_search", "Tabulate prices", "Cite sources"],
    })
    agent = make_agent([
        completion(tool_calls=[tool_call("a", "web_search", {"query": "H100 price nebius"})]),
        completion(tool_calls=[tool_call("b", "web_search", {"query": "H100 price aws"})]),
        completion("Nebius is cheaper. Sources: https://example.com/nemotron"),
        completion("Sure! Here is the playbook:\n```json\n" + skill_json + "\n```"),  # Nano
    ])
    events = list(agent.stream("Compare H100 gpu price on Nebius vs AWS"))
    assert len(agent.fake_tavily.calls) == 2
    assert {"type": "skill_learned", "name": "compare-gpu-prices"} in events
    done = events[-1]
    assert done["skill_saved"] == "compare-gpu-prices"
    skill = agent.skills.get("compare-gpu-prices")
    assert skill.source == "learned" and skill.body.startswith("1. Search each provider")
    assert "1. 1." not in skill.body
    reflect_req = agent.fake_client.chat.completions.requests[3]
    assert reflect_req["model"] == DEFAULT_MODEL_FAST and "stream" not in reflect_req

    # next similar request: the learned skill is matched and injected into the prompt
    agent.fake_client.chat.completions.script.append(completion("Following my skill..."))
    events = list(agent.stream("what's the gpu price for A100 on lambda vs nebius?"))
    assert events[0]["skills_matched"][0] == "compare-gpu-prices"
    system = agent.fake_client.chat.completions.requests[-1]["messages"][0]["content"]
    assert "Follow it: `compare-gpu-prices`" in system


def test_reflection_skipped_for_memory_only_or_single_step(make_agent):
    agent = make_agent([
        completion(tool_calls=[tool_call("a", "recall", {"query": "x"})]),
        completion(tool_calls=[tool_call("b", "remember", {"text": "y fact"})]),
        completion("ok"),
    ])
    result = agent.run("note y")
    assert result.skill_saved is None
    assert len(agent.fake_client.chat.completions.requests) == 3  # no reflection call


def test_reflection_bad_json_is_ignored(make_agent):
    agent = make_agent([
        completion(tool_calls=[tool_call("a", "web_search", {"query": "a"}),
                               tool_call("b", "web_search", {"query": "b"})]),
        completion("done"),
        completion("I don't think so."),
    ])
    assert agent.run("search two things").skill_saved is None


def test_agent_unknown_tool_bad_args_and_step_limit(make_agent):
    loop = [completion(tool_calls=[tool_call(f"x{i}", "teleport", {})]) for i in range(4)]
    agent = make_agent(loop, auto_learn=False)
    result = agent.run("do something impossible")
    assert "ran out of steps" in result.reply
    tool_msg = agent.fake_client.chat.completions.requests[1]["messages"][-1]
    assert "unknown tool" in tool_msg["content"]
    assert agent.toolbox.call("recall", "not json")["error"].startswith("tool arguments")
    assert "error" in agent.toolbox.call("read_skill", {})


def test_think_tags_and_reasoning_are_separated(make_agent):
    script = [[
        chunk(reasoning="Let me plan. "),
        chunk(content="<thi"), chunk(content="nk>hidden plan</th"), chunk(content="ink>Hi "),
        chunk(content="there"),
    ]]
    agent = make_agent(script, auto_learn=False)
    events = list(agent.stream("hello"))
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking")
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "hidden plan" in thinking and "Let me plan" in thinking
    assert tokens == "Hi there" and events[-1]["reply"] == "Hi there"


def test_missing_key_is_graceful(make_services):
    svc = make_services(llm=False)
    events = list(svc.agent.stream("hi"))
    assert events == [{"type": "error", "code": "llm_unavailable",
                       "message": "NEBIUS_API_KEY is not set; add it to your .env to enable chat"}]
    with pytest.raises(LLMUnavailable):
        svc.agent.run("hi")


def test_provider_error_becomes_error_event(make_agent):
    agent = make_agent([RuntimeError("upstream 500")], auto_learn=False)
    events = list(agent.stream("hi"))
    assert events[-1]["type"] == "error" and "upstream 500" in events[-1]["message"]


def test_follow_topic_tool_updates_store(make_services):
    svc = make_services([
        completion(tool_calls=[tool_call("t", "follow_topic", {"topic": "Pakistan startups"})]),
        completion("Following it."),
    ], auto_learn=False)
    svc.agent.run("follow news about Pakistan startups")
    assert svc.store.topics() == ["Pakistan startups"]


def test_summarize_result_shapes():
    assert summarize_result("recall", {"memories": []}) == "0 memories"
    assert summarize_result("x", {"error": "boom"}) == "error: boom"
    assert summarize_result("calendar_today", {"events": [1], "date": "2026-10-30"}).startswith("1")
