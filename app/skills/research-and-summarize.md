---
name: research-and-summarize
description: Research a topic on the web with Tavily and give a short sourced summary
triggers: research, look up, find out, what's new, latest
---

1. Restate the question in one line and pick 1-2 focused search queries.
2. Call `web_search` for each query (max 5 results each).
3. Keep only facts that appear in the results; drop anything unsourced.
4. Answer in 3-6 bullets, each ending with its source URL.
5. If the topic is something the user will come back to, call `remember` with a one-line takeaway.
