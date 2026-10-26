# MUIS OS Lite: Devpost submission

**Track:** Personal AI
**Tagline:** A private, always-on second brain that remembers your life, learns reusable skills and runs errands, with your data on your own server.
**Repo:** https://github.com/muisdevop/muis-os-lite
**Built with:** NVIDIA Nemotron 3 Super, NVIDIA Nemotron 3 Nano, Nebius Token Factory, Qwen3-Embedding-8B, Tavily, Qdrant, FastAPI, SQLite, APScheduler, Docker, Caddy

---

## Inspiration

I run a small software studio in Pakistan, freelance on the side and support my family. My life is
spread across chats, inboxes, notes and my own head, and I lose things: a client's preference, a
deadline mentioned once, the steps I followed last month to do the same chore. The assistants I tried
either forget me between sessions or remember me on someone else's servers, and none of them get
better at *my* repeated errands.

I wanted the opposite: an assistant that lives on a box I own, remembers what I tell it, and turns
the way I solved something once into a playbook it follows next time. Open models made that
realistic: Nemotron is good enough at tool calling to run the loop, and Nebius Token Factory gives me
those models behind a plain OpenAI-compatible API, so the whole thing stays small enough to run on a
cheap VPS.

## What it does

MUIS OS Lite is a self-hosted personal agent with a built-in web app.

- **Remembers your life.** Tell it a fact once ("my sister Ayesha's birthday is March 3, she loves
  green tea") and it saves it to long-term memory: embedded with Qwen3-Embedding-8B on Token
  Factory and stored in your own Qdrant. Relevant memories are recalled into every conversation.
  The Memory page lets you add, search by meaning, and delete notes.
- **Runs errands with tools.** Chat streams token by token from **Nemotron 3 Super**, which
  plans and calls tools: Tavily web search, remember/recall, read/save/list skills, follow a news
  topic, and (optional) read-only Gmail and Google Calendar. Each tool call appears live in the chat
  as a step you can expand, so you see what it did and with which sources.
- **Learns reusable skills.** After a multi-step task, **Nemotron 3 Nano** reviews the tool trace
  and decides whether it is a repeatable errand. If so, it writes a markdown playbook with
  triggers and steps (for example `plan-a-deadline-week`). Next time you ask something similar, the
  skill is matched and injected into the prompt and the agent follows the same steps. Skills are
  plain files you can read and edit on the Skills page; new ones are highlighted.
- **Briefs you every morning.** A scheduled job builds a daily brief from your memory, your
  recently learned skills, fresh Tavily news on topics you follow, and your calendar and inbox if
  connected. Nano writes it; it is stored and viewable on the Brief page.
- **Stays yours.** `docker compose up` runs the app and Qdrant; one script deploys it to an
  Ubuntu VPS with HTTPS and a password. Chats live in SQLite on your disk, memory in your Qdrant,
  skills in your filesystem. Only inference calls leave the server.

It also degrades gracefully: with no keys at all, the UI, memory (with a local offline embedder),
skills and a digest-style brief still work, and Settings shows exactly which providers are connected.

## How we built it

**Nebius Token Factory** is the inference layer. Because it is OpenAI-compatible, the client is the
standard OpenAI SDK pointed at `https://api.tokenfactory.nebius.com/v1/`, and every model ID is an
environment variable.

**Two Nemotron tiers, each where it fits:**

- **Nemotron 3 Super** (`nvidia/nemotron-3-super-120b-a12b`) runs the agent loop. We call it with
  `stream=True` and the tool schema, and assemble tool calls from the streamed deltas (ids, names
  and argument fragments arrive in pieces), execute them, feed results back as `tool` messages and
  loop until it answers or hits a step limit. Text tokens are forwarded to the browser over
  Server-Sent Events as they arrive.
- **Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) handles the cheap, frequent calls:
  the skill-reflection step after a task (strict JSON out, parsed tolerantly) and composing the
  daily brief from gathered data in a single call.

Nemotron models can reason before answering, so the stream parser separates `<think>` blocks and
`reasoning_content` deltas into a collapsible "thinking" step instead of mixing them into the reply.
Reasoning can be switched off per deployment with an `LLM_EXTRA_BODY` setting.

**Memory** uses Qwen3-Embedding-8B on Token Factory and Qdrant (a Qdrant container in Docker,
embedded on-disk Qdrant otherwise, RAM in tests). Each embedder gets its own collection so vectors
never mix.

**Tavily** powers the `web_search` tool (answer plus sources, which the agent cites) and the
brief's news section using Tavily's news topic search, one query per followed topic.

**Skills** are markdown files with a small front-matter block (name, description, triggers,
source). A lightweight matcher picks relevant skills for each request, and the agent can also list
and read them as tools.

**The app** is FastAPI with a single static HTML/JS/CSS front end (no Node build step):
chat with streaming and live tool steps, Memory, Skills (view/edit), Brief, and Settings/health.
Conversations persist in SQLite, the brief runs on APScheduler, optional password auth protects the
UI and API, and the layout works on a phone.

**Shipping:** 51 offline pytest tests with mocked Nemotron, Tavily, Google and Qdrant cover the agent
loop, streaming, skill learning, memory, the brief, the API, auth and the UI routes. GitHub Actions
runs them, then builds the Docker image, starts `docker compose` with a real Qdrant, exercises the
API end to end and checks memory survives a restart. `scripts/deploy.sh` provisions a VPS with
Docker and Caddy (automatic HTTPS), and `scripts/smoke_live.py` runs a real chat + memory + search
round trip against live keys.

## Challenges we ran into

- **Streaming and tool calls together.** Streaming a reply is easy; streaming while the model may
  instead be emitting tool calls means rebuilding each call from fragments keyed by index, and
  deciding late whether the text you already showed was a final answer or a preamble to a tool call.
- **Reasoning output.** Reasoning can arrive inline as `<think>` tags split across chunk boundaries
  or as a separate field. A small streaming filter that holds back partial tags fixed both.
- **Learning skills without junk.** Early on, the reflector wanted to save a "skill" for every
  single fact. Only reflecting after multi-step tasks that used a real errand tool, telling Nano to
  write generic steps, and refusing duplicates kept the library small and useful.
- **Never failing hard.** Missing keys, a Qdrant container still booting, a provider error mid-stream:
  each needed its own path (retry then RAM fallback, error events in the stream, a digest brief
  when the model is unavailable) so the app always stays usable.

## Accomplishments that we're proud of

- A complete loop: remember, act with tools, learn a skill, follow it next time, and brief you in the
  morning, all visible in a clean web app that works on a phone.
- Real ownership: one `docker compose up` or one deploy command, data on your own disk, readable
  markdown skills you can edit.
- Shipped like a product: 51 tests, CI that boots the real Docker stack, graceful degradation
  without keys, and a one-command HTTPS deploy.

## What we learned

- Open models are now good enough for the agent loop when you give them a tight system prompt, a
  small set of well-described tools and their own past playbooks.
- Splitting work across model sizes matters: Super for planning and tool use, Nano for reflection
  and summaries keeps the experience fast and the cost low.
- An OpenAI-compatible endpoint like Token Factory makes the model layer a configuration detail.

## What's next for MUIS OS Lite

- Deliver the morning brief to Telegram or WhatsApp instead of only the web app.
- More personal tools behind the same pattern: write actions with confirmation (draft an email,
  create a calendar event), bills and reminders.
- Skill refinement: when a followed skill fails or the user corrects it, have Nano update the
  playbook instead of only creating new ones.
- Encrypted memory at rest and per-user accounts for families.
- Voice in and out on the phone.

---

### Try it

```bash
git clone https://github.com/muisdevop/muis-os-lite.git && cd muis-os-lite
cp .env.example .env    # NEBIUS_API_KEY, TAVILY_API_KEY
docker compose up -d --build   # http://localhost:8000
```
