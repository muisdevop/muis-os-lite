"""Optional Google integration: Gmail (read-only summary) and Calendar (today's events).

Enabled only when GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN are set.
The refresh token needs these read-only scopes:
  https://www.googleapis.com/auth/gmail.readonly
  https://www.googleapis.com/auth/calendar.readonly
See docs/GOOGLE.md for a 5-minute way to mint one with the OAuth Playground.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class GoogleClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 tz: str = "Asia/Karachi", http: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.tz = tz
        self.http = http or httpx.Client(timeout=15)
        self._token: str | None = None
        self._expires = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        resp = self.http.get(url, params=params,
                             headers={"Authorization": f"Bearer {self._access_token()}"})
        resp.raise_for_status()
        return resp.json()

    def gmail_recent(self, query: str = "is:unread newer_than:2d", max_results: int = 8
                     ) -> dict[str, Any]:
        """Subjects, senders and snippets of recent mail matching a Gmail search query."""
        max_results = max(1, min(int(max_results), 20))
        listing = self._get(f"{GMAIL}/messages", {"q": query, "maxResults": max_results})
        out = []
        for ref in listing.get("messages", [])[:max_results]:
            msg = self._get(f"{GMAIL}/messages/{ref['id']}", {
                "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"],
            })
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            out.append({
                "from": headers.get("from", ""),
                "subject": headers.get("subject", "(no subject)"),
                "date": headers.get("date", ""),
                "snippet": (msg.get("snippet") or "")[:240],
            })
        return {"query": query, "count": len(out), "messages": out}

    def calendar_today(self, day: str | None = None) -> dict[str, Any]:
        """Events on the primary calendar for a day (YYYY-MM-DD, default today)."""
        zone = ZoneInfo(self.tz)
        start = (datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=zone) if day
                 else datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0))
        end = start + timedelta(days=1)
        data = self._get(CALENDAR, {
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 25,
        })
        events = []
        for ev in data.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            events.append({
                "summary": ev.get("summary", "(busy)"),
                "start": s.get("dateTime") or s.get("date"),
                "end": e.get("dateTime") or e.get("date"),
                "location": ev.get("location", ""),
                "meet": ev.get("hangoutLink", ""),
            })
        return {"date": start.date().isoformat(), "timezone": self.tz, "events": events}


GMAIL_SPEC = {
    "type": "function",
    "function": {
        "name": "gmail_recent",
        "description": "Read-only: list recent Gmail messages (sender, subject, snippet) "
                       "matching a Gmail search query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "is:unread newer_than:2d"},
                "max_results": {"type": "integer", "default": 8},
            },
            "required": [],
        },
    },
}

CALENDAR_SPEC = {
    "type": "function",
    "function": {
        "name": "calendar_today",
        "description": "Read-only: list the user's Google Calendar events for a day.",
        "parameters": {
            "type": "object",
            "properties": {"day": {"type": "string", "description": "YYYY-MM-DD; default today"}},
            "required": [],
        },
    },
}
