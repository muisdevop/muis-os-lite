import json

from app.config import DEFAULT_MODEL_FAST, DEFAULT_MODEL_PLAN
from tests.conftest import completion, tool_call


def test_agent_answers_directly(make_agent):
    agent = make_agent([completion("Hello MUIS!")])
    result = agent.run("hi")
    assert result.reply == "Hello MUIS!"
    assert result.tool_calls == []
    req = agent.fake_client.chat.completions.requests[0]
    assert req["model"] == DEFAULT_MODEL_PLAN
    assert {t["function"]["name"] for t in req["tools"]} >= {"web_search", "remember", "recall"}


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
    # tool result was fed back to the model as a tool message
    second = agent.fake_client.chat.completions.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    # memory is now recalled into the next prompt
    agent.fake_client.chat.completions.script.append(completion("Dr. Khan."))
    agent.run("Who is my dentist Dr?")
    system = agent.fake_client.chat.completions.requests[-1]["messages"][0]["content"]
    assert "Dr. Khan" in system


def test_agent_web_search_then_learns_skill(make_agent):
    skill_json = json.dumps({
        "save": True, "name": "compare-gpu-prices", "description": "Compare GPU cloud prices",
        "triggers": ["gpu price"], "steps": ["Search two providers", "Tabulate", "Cite"],
    })
    agent = make_agent([
        completion(tool_calls=[tool_call("a", "web_search", {"query": "H100 price nebius"})]),
        completion(tool_calls=[tool_call("b", "web_search", {"query": "H100 price aws"})]),
        completion("Nebius is cheaper. Sources: https://example.com/nemotron"),
        completion(skill_json),  # Nano reflection
    ])
    result = agent.run("Compare H100 gpu price on Nebius vs AWS")
    assert len(agent.fake_tavily.calls) == 2
    assert result.skill_saved == "compare-gpu-prices"
    assert agent.skills.get("compare-gpu-prices") is not None
    reflect_req = agent.fake_client.chat.completions.requests[-1]
    assert reflect_req["model"] == DEFAULT_MODEL_FAST


def test_agent_unknown_tool_and_step_limit(make_agent):
    loop = [completion(tool_calls=[tool_call(f"x{i}", "teleport", {})]) for i in range(4)]
    agent = make_agent(loop, auto_learn=False)
    result = agent.run("do something impossible")
    assert "ran out of steps" in result.reply
    tool_msg = agent.fake_client.chat.completions.requests[1]["messages"][-1]
    assert "unknown tool" in tool_msg["content"]
