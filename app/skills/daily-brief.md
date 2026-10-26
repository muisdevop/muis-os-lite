---
name: daily-brief
description: Build a short morning brief from memory, plans, calendar, mail and fresh web news
triggers: daily brief, morning brief, what's on today, brief me
---

1. Call `recall` for "plans, deadlines and open tasks" (k=8).
2. If `calendar_today` and `gmail_recent` are available, call them for today's meetings and unread mail.
3. For each followed topic (or active project in memory), call `web_search` only if fresh news would change what the user does today.
4. Write the brief: Today (max 4 items), Inbox (if mail), News you follow (with source URLs), One suggestion.
5. Keep it under 200 words. The Brief page can also generate this automatically every morning.
