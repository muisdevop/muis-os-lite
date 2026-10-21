---
name: daily-brief
description: Build a short morning brief from memory, plans and fresh web news
triggers: daily brief, morning brief, what's on today, brief me
---

1. Call `recall` for "plans, deadlines and open tasks" (k=8).
2. For each active project in memory, call `web_search` only if fresh news would change what the user does today.
3. (TODO) Pull today's meetings from the Calendar tool and unread priority mail from Gmail once those hooks exist.
4. Write the brief: Today (max 3 items), Deadlines, One thing worth knowing (with source URL).
5. Keep it under 150 words.
