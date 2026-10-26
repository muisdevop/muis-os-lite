# MUIS OS Lite: 3-minute demo video script

**Goal:** show the full loop (remember, act with tools, learn a skill, follow it, morning brief) on
the real app with Nemotron on Nebius Token Factory, in under 3:00.

## Before you record (10 minutes)

1. App running with real keys (`NEBIUS_API_KEY`, `TAVILY_API_KEY`), ideally on your HTTPS domain.
2. Start clean so the "learned skill" moment is real:
   - Skills page: delete any learned skills (keep the 3 bundled ones).
   - Memory page: delete old notes, then add these three (Memory page, Remember button):
     - `My sister Ayesha's birthday is on March 3 and she loves green tea` (person)
     - `I prefer meetings after 11 AM on weekdays` (preference)
     - `Power cuts usually hit between 7 and 9 PM, so schedule calls before 7` (note)
   - Brief page: follow `NVIDIA Nemotron` and `Pakistan tech startups`.
3. Do one dry run of every prompt below, then delete the learned skill and that chat again.
4. Browser at 1440x900, zoom 110%, dark mode, notifications off. Phone on the same URL for the
   mobile shot (optional).
5. Screen recorder with mic. Speak slowly; cut dead air while the model thinks.

## Script

### 0:00 to 0:20 — Hook (Chat page, empty state)

**Show:** the empty chat with the MUIS logo and suggestion chips. Point at the green dot
"Nebius Token Factory · connected" bottom left.

**Say:** "This is MUIS OS Lite, a private second brain that runs on my own server. It remembers my
life, runs errands with tools, and learns reusable skills, using NVIDIA Nemotron on Nebius Token
Factory, with Tavily for live search."

### 0:20 to 0:50 — It remembers (Chat)

**Type:** `Remember that my hackathon demo is due Friday at 10 PM Pakistan time.`
**Show:** the `remember` step appears with a check, then the short confirmation streams in.

**Type:** `What should I get my sister for her birthday, and when is it?`
**Show:** the "memories recalled" tag, the reply mentions March 3 and green tea.

**Say:** "I told it about my sister weeks ago. Memories are embedded with Qwen3 embeddings on Token
Factory and stored in my own Qdrant, then recalled into every chat."

### 0:50 to 1:40 — It runs an errand and learns a skill (Chat)

**Type:** `Plan my week so I finish the hackathon demo on time. Check what Devpost needs for a submission and cite the source.`

**Show, in order (let it play, it is the money shot; Nano picks the skill name, so it may differ from `plan-a-deadline-week`):**
1. Steps appearing live: `recall`, then `web_search` with the Devpost query, each turning to a check.
   Expand the `web_search` step once to show the URLs.
2. The day-by-day plan streaming in, with the source link.
3. The **"✨ learned skill: plan-a-deadline-week"** tag and the toast.

**Say:** "Nemotron 3 Super plans and calls tools; every step is visible. Because this was a
multi-step errand, Nemotron 3 Nano reflected on what it did and saved it as a reusable skill."

### 1:40 to 2:05 — Skills are plain markdown (Skills page)

**Click:** Skills (it has a "new" badge) → the highlighted learned skill.
**Show:** triggers and numbered steps. Click **Edit markdown**, add a step like
`Block the last evening as buffer`, click **Save**.

**Say:** "Skills are just markdown files on my server. I can read them, edit them, and the agent
follows them."

### 2:05 to 2:25 — It follows the skill next time (Chat)

**Click:** `+` New chat. **Type:** `My visa documents are due on the 15th, plan my week for it.`
**Show:** the **"following skill: plan-a-deadline-week"** tag at the top of the reply, and the
plan following the same structure, including your new buffer step.

**Say:** "Next time, it matches the skill and follows the same playbook, including my edit."

### 2:25 to 2:45 — Morning brief (Brief page)

**Click:** Brief → **Generate now**.
**Show:** Today (the hackathon deadline from memory), News you follow with Tavily sources, One
suggestion. Point at "auto daily at 08:00 (Asia/Karachi)".

**Say:** "Every morning it writes me a brief from my memory and fresh Tavily news on the topics I
follow, plus my calendar and inbox if I connect Google, read-only."

### 2:45 to 3:00 — Yours (Settings, then terminal or phone)

**Click:** Settings → show the provider cards (Nebius, Tavily, Qdrant memory, Google optional,
password lock) and the model list. Optionally flash the phone showing the same chat.

**Say:** "Everything runs from one docker compose file or one deploy command, with my data on my
own server. MUIS OS Lite: a second brain that's actually mine."

## Fallback lines (if something is slow or fails live)

- Slow model: cut the wait in editing; keep the moment the steps flip to checks.
- Skill not learned on the first try: say nothing, re-run the same errand prompt in a new chat
  (dry-run tip: prompts that need both `recall` and `web_search` reliably trigger reflection).
- Tavily hiccup: the step shows a red cross and the agent says so. That is fine to show: "it never
  invents a source."

## Shot list for the thumbnail / Devpost gallery

`docs/screenshots/02-chat.png` (chat with steps + learned skill), `04-skills.png`, `05-brief.png`,
`07-mobile-chat.png`.
