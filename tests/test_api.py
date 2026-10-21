from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import completion


def client_for(make_agent, settings, script=None):
    agent = make_agent(script, auto_learn=False)
    return TestClient(create_app(agent=agent, settings=settings)), agent


def test_health(make_agent, settings):
    client, _ = client_for(make_agent, settings)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["base_url"] == "https://api.tokenfactory.nebius.com/v1/"
    assert "web_search" in body["tools"]
    assert body["configured"] == {"nebius": False, "tavily": False}


def test_chat(make_agent, settings):
    client, _ = client_for(make_agent, settings, [completion("Assalam o Alaikum!")])
    resp = client.post("/chat", json={"message": "salam"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Assalam o Alaikum!"
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_memory_endpoints(make_agent, settings):
    client, _ = client_for(make_agent, settings)
    assert client.post("/memory", json={"text": "Hackathon due Oct 30 22:00 PKT"}).status_code == 200
    recalled = client.get("/memory", params={"q": "hackathon due"}).json()["memories"]
    assert recalled[0]["text"].startswith("Hackathon")
    assert len(client.get("/memory").json()["memories"]) == 1


def test_skill_endpoints(make_agent, settings):
    client, _ = client_for(make_agent, settings)
    names = [s["name"] for s in client.get("/skills").json()["skills"]]
    assert "daily-brief" in names
    new = {"name": "pay-electric-bill", "description": "Pay the LESCO bill", "steps": ["Open", "Pay"]}
    assert client.post("/skills", json=new).status_code == 201
    assert client.post("/skills", json=new).status_code == 409
    assert client.get("/skills/pay-electric-bill").json()["body"].startswith("1. Open")
    assert client.get("/skills/nope").status_code == 404


def test_chat_without_key_returns_503(settings):
    client = TestClient(create_app(settings=settings))  # real LLM, no NEBIUS_API_KEY
    resp = client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert "NEBIUS_API_KEY" in resp.json()["detail"]
