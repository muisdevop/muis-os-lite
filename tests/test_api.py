from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_services, create_app
from tests.conftest import completion, sse_events, tool_call


def test_health(make_client):
    client, _ = make_client()
    assert client.get("/health").json()["status"] == "ok"
    body = client.get("/api/health").json()
    assert body["base_url"] == "https://api.tokenfactory.nebius.com/v1/"
    assert "web_search" in body["tools"] and "gmail_recent" not in body["tools"]
    assert body["configured"]["nebius"] is True  # scripted client injected
    assert body["configured"]["google"] is False and body["configured"]["auth"] is False
    assert body["memory"]["ok"] and body["scheduler"]["timezone"] == "Asia/Karachi"


def test_chat_json_persists_conversation(make_client):
    client, svc = make_client([completion("Assalam o Alaikum!"), completion("Second reply")],
                              auto_learn=False)
    resp = client.post("/api/chat", json={"message": "salam"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Assalam o Alaikum!"
    cid = data["conversation_id"]
    client.post("/api/chat", json={"message": "again", "conversation_id": cid})
    # history from SQLite was sent to the model on the second turn
    msgs = svc.fake_client.chat.completions.requests[-1]["messages"]
    assert [m["content"] for m in msgs[1:]] == ["salam", "Assalam o Alaikum!", "again"]
    conv = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
    assert client.get("/api/conversations").json()["conversations"][0]["id"] == cid
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.delete(f"/api/conversations/{cid}").json() == {"deleted": True}
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_chat_stream_sse(make_client):
    client, svc = make_client([
        completion(tool_calls=[tool_call("c1", "remember", {"text": "I love biryani"})]),
        completion("Noted: you love biryani."),
    ], auto_learn=False)
    with client.stream("POST", "/api/chat/stream", json={"message": "I love biryani"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = sse_events(resp.read().decode())
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and types[-1] == "done"
    assert "tool_call" in types and "token" in types
    cid = events[0]["id"]
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert msgs[-1]["content"] == "Noted: you love biryani."
    assert msgs[-1]["meta"]["tool_calls"][0]["tool"] == "remember"
    assert svc.memory.recall("biryani")[0]["text"] == "I love biryani"


def test_chat_without_key(make_client):
    client, _ = make_client(llm=False)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 503 and "NEBIUS_API_KEY" in resp.json()["detail"]
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as resp:
        events = sse_events(resp.read().decode())
    assert events[-1]["type"] == "error" and events[-1]["code"] == "llm_unavailable"
    conv = client.get(f"/api/conversations/{events[0]['id']}").json()
    assert conv["messages"][-1]["role"] == "error"


def test_upstream_failure_is_502(make_client):
    client, _ = make_client([RuntimeError("rate limited")], auto_learn=False)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 502 and "rate limited" in resp.json()["detail"]


def test_memory_endpoints(make_client):
    client, _ = make_client(llm=False)
    mid = client.post("/api/memory", json={"text": "Hackathon due Oct 30 22:00 PKT",
                                           "kind": "plan"}).json()["id"]
    client.post("/api/memory", json={"text": "I prefer green tea"})
    recalled = client.get("/api/memory", params={"q": "hackathon due"}).json()
    assert recalled["memories"][0]["text"].startswith("Hackathon")
    assert recalled["total"] == 2
    assert len(client.get("/api/memory", params={"kind": "plan"}).json()["memories"]) == 1
    assert client.delete(f"/api/memory/{mid}").json() == {"deleted": True}
    assert client.delete(f"/api/memory/{mid}").status_code == 404
    assert client.get("/api/memory").json()["total"] == 1
    assert client.post("/api/memory", json={"text": "   "}).status_code == 422


def test_skill_endpoints(make_client):
    client, _ = make_client(llm=False)
    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert "daily-brief" in names
    new = {"name": "pay-electric-bill", "description": "Pay the LESCO bill", "steps": ["Open", "Pay"]}
    assert client.post("/api/skills", json=new).status_code == 201
    assert client.post("/api/skills", json=new).status_code == 409
    skill = client.get("/api/skills/pay-electric-bill").json()
    assert skill["body"].startswith("1. Open") and skill["source"] == "user"
    edited = skill["markdown"].replace("2. Pay", "2. Pay with JazzCash\n3. Save the receipt")
    resp = client.put("/api/skills/pay-electric-bill", json={"markdown": edited})
    assert resp.status_code == 200 and "JazzCash" in resp.json()["body"]
    assert resp.json()["source"] == "user"
    assert client.put("/api/skills/nope", json={"markdown": "x"}).status_code == 404
    assert client.get("/api/skills/nope").status_code == 404
    assert client.delete("/api/skills/pay-electric-bill").json() == {"deleted": True}
    assert client.delete("/api/skills/pay-electric-bill").status_code == 404


def test_topics_and_brief_endpoints(make_client):
    client, _ = make_client(llm=False)
    assert client.post("/api/topics", json={"topic": "NVIDIA Nemotron"}).status_code == 201
    assert client.get("/api/topics").json()["topics"] == ["NVIDIA Nemotron"]
    assert client.get("/api/brief/latest").json() == {"brief": None}
    brief = client.post("/api/brief").json()
    assert "NVIDIA Nemotron" in brief["content"] and brief["sources"]
    assert client.get("/api/brief/latest").json()["brief"]["id"] == brief["id"]
    assert len(client.get("/api/briefs").json()["briefs"]) == 1
    assert client.delete("/api/topics/NVIDIA Nemotron").json() == {"topics": []}
    assert client.delete("/api/topics/NVIDIA Nemotron").status_code == 404


def test_password_auth(make_client):
    client, _ = make_client(llm=False, app_password="s3cret")
    assert client.get("/api/auth").json() == {"required": True, "authenticated": False}
    assert client.get("/api/memory").status_code == 401
    assert client.get("/health").status_code == 200  # docker healthcheck stays open
    assert client.get("/").status_code == 200  # UI shell loads, shows login
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.get("/api/memory", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/auth").json()["authenticated"] is True
    assert client.get("/api/memory").status_code == 200
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/memory").status_code == 401


def test_default_build_without_any_keys(tmp_path, settings):
    """The real wiring with no keys: local embedder + embedded Qdrant on disk, chat disabled."""
    s = Settings(**{**settings.__dict__, "qdrant_url": "", "data_dir": tmp_path / "d"})
    svc = build_services(s)
    client = TestClient(create_app(services=svc))
    health = client.get("/api/health").json()
    assert health["configured"]["nebius"] is False
    assert health["memory"]["embedder"].startswith("local")
    assert health["memory"]["backend"] == "embedded (disk)"
    client.post("/api/memory", json={"text": "My cat is called Biscuit"})
    hits = client.get("/api/memory", params={"q": "what is my cat called"}).json()["memories"]
    assert hits[0]["text"] == "My cat is called Biscuit"
    assert (tmp_path / "d" / "muis.db").exists() and (tmp_path / "d" / "qdrant").exists()
    assert client.post("/api/brief").status_code == 200
    svc.memory.client.close()
