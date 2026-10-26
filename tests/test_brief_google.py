import json

import httpx

from app.store import Store
from app.tools.google import GoogleClient
from tests.conftest import completion


def fake_google_http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer at"
        if "/messages/" in url:
            return httpx.Response(200, json={"snippet": "Your submission is due Friday", "payload": {
                "headers": [{"name": "From", "value": "Devpost <hi@devpost.com>"},
                            {"name": "Subject", "value": "Deadline reminder"}]}})
        if url.endswith("/messages") or "/messages?" in url:
            assert "is%3Aunread" in url or "is:unread" in url
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if "calendar/v3" in url:
            assert "singleEvents=true" in url
            return httpx.Response(200, json={"items": [{
                "summary": "Demo recording", "start": {"dateTime": "2026-10-30T15:00:00+05:00"},
                "end": {"dateTime": "2026-10-30T16:00:00+05:00"}}]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def google_client() -> GoogleClient:
    return GoogleClient("id", "secret", "refresh", http=fake_google_http())


def test_google_client_reads_mail_and_calendar():
    g = google_client()
    mail = g.gmail_recent()
    assert mail["messages"][0]["subject"] == "Deadline reminder"
    assert mail["messages"][0]["from"].startswith("Devpost")
    cal = g.calendar_today("2026-10-30")
    assert cal["events"][0]["summary"] == "Demo recording" and cal["date"] == "2026-10-30"


def test_google_tools_registered_only_when_configured(make_services):
    assert "gmail_recent" not in make_services(llm=False).toolbox.names
    svc = make_services(llm=False, google=google_client())
    assert {"gmail_recent", "calendar_today"} <= set(svc.toolbox.names)
    out = svc.toolbox.call("calendar_today", "{}")
    assert "events" in out


def test_google_settings_flag():
    from app.config import Settings

    assert not Settings().google_configured
    assert Settings(google_client_id="a", google_client_secret="b",
                    google_refresh_token="c").google_configured


def test_brief_without_llm_is_a_digest(make_services):
    svc = make_services(llm=False, google=google_client())
    svc.memory.add("Hackathon submission due Oct 30 22:00 PKT", kind="plan")
    svc.store.add_topic("NVIDIA Nemotron")
    brief = svc.brief.build()
    assert "Hackathon submission" in brief["content"]
    assert "Demo recording" in brief["content"] and "Deadline reminder" in brief["content"]
    assert "NVIDIA Nemotron" in brief["content"]
    assert brief["sources"][0]["url"] == "https://example.com/nemotron"
    assert brief["generated_by"].startswith("digest")
    assert svc.brief.tavily_client.calls[0]["topic"] == "news"
    assert svc.store.list_briefs()[0]["id"] == brief["id"]


def test_brief_with_llm_uses_nano_and_context(make_services):
    svc = make_services([completion("**Today**\n- Finish the demo video")])
    svc.memory.add("Record the demo video on Thursday", kind="plan")
    svc.store.add_topic("Tavily")
    brief = svc.brief.build(trigger="scheduled")
    assert brief["content"].startswith("**Today**") and brief["trigger"] == "scheduled"
    req = svc.fake_client.chat.completions.requests[0]
    assert req["model"] == svc.settings.model_fast
    prompt = req["messages"][-1]["content"]
    assert "Record the demo video" in prompt and "Topic: Tavily" in prompt


def test_brief_llm_failure_falls_back(make_services):
    svc = make_services([RuntimeError("down")])
    brief = svc.brief.build()
    assert brief["generated_by"].startswith("digest")
    assert any("LLM failed" in w for w in brief["warnings"])


def test_brief_default_topics_and_no_tavily(make_services):
    svc = make_services(llm=False, brief_topics=["AI agents"])
    assert svc.brief.topics() == ["AI agents"]
    svc.brief.tavily_client = None
    brief = svc.brief.build()
    assert any("TAVILY_API_KEY" in w for w in brief["warnings"])
    assert "Nothing saved yet" in brief["content"]


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "x.db")
    cid = store.create_conversation("  Plan   my week  ")
    store.add_message(cid, "user", "hi")
    store.add_message(cid, "assistant", "hello", {"tool_calls": []})
    store.add_message(cid, "error", "oops")
    assert store.get_conversation(cid)["title"] == "Plan my week"
    assert store.history(cid) == [{"role": "user", "content": "hi"},
                                  {"role": "assistant", "content": "hello"}]
    b = store.add_brief("brief", [{"url": "u"}])
    assert store.get_brief(b["id"])["sources"] == [{"url": "u"}]
    store.add_topic("ai"), store.add_topic("ai")
    assert store.topics() == ["ai"] and store.remove_topic("ai")
    assert store.delete_conversation(cid) and store.messages(cid) == []
    assert json.loads(json.dumps(store.list_conversations())) == []
