"""Stub hooks for personal-data tools. Not registered with the agent yet.

TODO: wire each one to OAuth (Google Gmail / Calendar, GitHub) and add its spec
to the toolbox in app/tools/__init__.py once it returns real data.
"""

from __future__ import annotations

from typing import Any


def _todo(tool: str, plan: str, **kwargs: Any) -> dict[str, Any]:
    return {"tool": tool, "status": "not_implemented", "todo": plan, "args": kwargs}


def gmail_search(query: str, max_results: int = 10) -> dict[str, Any]:
    # TODO: Gmail API users.messages.list(q=query) + messages.get(format="metadata")
    return _todo("gmail_search", "Gmail OAuth + users.messages.list", query=query,
                 max_results=max_results)


def calendar_agenda(date: str | None = None) -> dict[str, Any]:
    # TODO: Calendar API events.list(timeMin/timeMax for the day, singleEvents=True)
    return _todo("calendar_agenda", "Google Calendar OAuth + events.list", date=date)


def github_issues(repo: str, state: str = "open") -> dict[str, Any]:
    # TODO: GitHub REST GET /repos/{repo}/issues with a fine-grained token
    return _todo("github_issues", "GitHub token + /repos/{repo}/issues", repo=repo, state=state)


STUB_TOOLS = {
    "gmail_search": gmail_search,
    "calendar_agenda": calendar_agenda,
    "github_issues": github_issues,
}
