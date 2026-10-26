def test_ui_is_served(make_client):
    client, _ = make_client(llm=False)
    resp = client.get("/")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    html = resp.text
    for view in ("view-chat", "view-memory", "view-skills", "view-brief", "view-settings"):
        assert f'id="{view}"' in html
    assert 'name="viewport"' in html  # mobile-friendly
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "/api/chat/stream" in js.text and "tool_call" in js.text
    css = client.get("/static/app.css")
    assert css.status_code == 200 and "@media" in css.text


def test_ui_references_only_existing_endpoints(make_client):
    import re

    client, _ = make_client(llm=False)
    js = client.get("/static/app.js").text
    paths = set(re.findall(r'"(/api/[a-z/]+)', js))
    routes = {r.path for r in client.app.routes}
    for p in paths:
        assert any(r == p or r.startswith(p.rstrip("/") + "/{") or r.startswith(p) for r in routes), p
