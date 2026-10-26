/* MUIS OS Lite web UI: plain JS, no build step. */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const state = { conversationId: null, busy: false, health: null, skillSeen: loadSeen(), currentSkill: null };

  // ---------- helpers ----------
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  }
  /** Small, safe markdown renderer (escapes HTML first). */
  function md(src) {
    const lines = esc(src || "").split("\n");
    let html = "", list = null, para = [], inCode = false, code = [];
    const flushPara = () => { if (para.length) { html += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
    const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (line.startsWith("```")) {
        if (inCode) { html += `<pre><code>${code.join("\n")}</code></pre>`; code = []; inCode = false; }
        else { flushPara(); flushList(); inCode = true; }
        continue;
      }
      if (inCode) { code.push(raw); continue; }
      let m;
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flushPara(); flushList(); html += `<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`; continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { flushPara(); if (list !== "ul") { flushList(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flushPara(); if (list !== "ol") { flushList(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; continue; }
      if (list && /^\s{2,}\S/.test(raw)) { html = html.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`); continue; }
      flushList(); para.push(line);
    }
    if (inCode) html += `<pre><code>${code.join("\n")}</code></pre>`;
    flushPara(); flushList();
    return html;
  }
  function ago(iso) {
    if (!iso) return "";
    const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg; t.classList.toggle("err", err); t.classList.remove("hidden");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3500);
  }
  function loadSeen() { try { return JSON.parse(localStorage.getItem("muis.skillsSeen") || "[]"); } catch { return []; } }
  function saveSeen() { localStorage.setItem("muis.skillsSeen", JSON.stringify(state.skillSeen)); }

  async function api(path, opts = {}) {
    const init = { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } };
    if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
    const res = await fetch(path, init);
    if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login required"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    return data;
  }

  // ---------- auth ----------
  function showLogin() { $("#login").classList.remove("hidden"); $("#login-password").focus(); }
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/login", { method: "POST", body: { password: $("#login-password").value } });
      $("#login").classList.add("hidden"); $("#login-error").textContent = "";
      boot();
    } catch (err) { $("#login-error").textContent = err.message; }
  });
  $("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.reload(); });

  // ---------- navigation ----------
  const loaders = { memory: loadMemory, skills: loadSkills, brief: loadBrief, settings: loadSettings, chat: () => {} };
  function show(view) {
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $("#conv-section").classList.toggle("hidden", false);
    $("#sidebar").classList.remove("open");
    history.replaceState(null, "", `#${view}`);
    loaders[view]?.();
  }
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#toggle-convs").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  document.addEventListener("click", (e) => {
    const sb = $("#sidebar");
    if (sb.classList.contains("open") && !sb.contains(e.target) && e.target.id !== "toggle-convs") sb.classList.remove("open");
  });

  // ---------- health ----------
  async function loadHealth() {
    try {
      const h = await api("/api/health");
      state.health = h;
      const ready = h.configured.nebius;
      $("#status-dot").className = "dot " + (ready ? "ok" : "");
      $("#status-text").textContent = ready ? `${h.provider} · connected` : "No LLM key: chat disabled";
      $("#model-pill").textContent = shortModel(h.models.plan);
      $("#brief-schedule").textContent = h.scheduler.enabled ? `auto daily at ${h.scheduler.daily_brief_time} (${h.scheduler.timezone})` : "scheduler off";
      return h;
    } catch (err) {
      $("#status-dot").className = "dot bad"; $("#status-text").textContent = "API unreachable";
    }
  }
  const shortModel = (id) => (id || "").split("/").pop();

  // ---------- conversations ----------
  async function loadConversations() {
    const { conversations } = await api("/api/conversations");
    const list = $("#conv-list");
    list.innerHTML = conversations.length ? "" : '<div class="muted small" style="padding:4px 10px">No chats yet</div>';
    for (const c of conversations) {
      const el = document.createElement("div");
      el.className = "conv" + (c.id === state.conversationId ? " active" : "");
      el.innerHTML = `<span class="title">${esc(c.title)}</span><button class="del" title="Delete">✕</button>`;
      el.addEventListener("click", () => openConversation(c.id));
      el.querySelector(".del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this conversation?")) return;
        await api(`/api/conversations/${c.id}`, { method: "DELETE" });
        if (c.id === state.conversationId) newChat();
        loadConversations();
      });
      list.appendChild(el);
    }
  }
  function newChat() {
    state.conversationId = null;
    $("#messages").innerHTML = "";
    $("#messages").appendChild(emptyTpl.cloneNode(true));
    bindSuggestions();
    $("#chat-title").textContent = "New chat";
    $$(".conv").forEach((c) => c.classList.remove("active"));
    show("chat");
    $("#input").focus();
  }
  async function openConversation(id) {
    const conv = await api(`/api/conversations/${id}`);
    state.conversationId = id;
    $("#chat-title").textContent = conv.title;
    const box = $("#messages"); box.innerHTML = "";
    for (const m of conv.messages) {
      if (m.role === "user") addUser(m.content);
      else if (m.role === "error") addError(m.content);
      else {
        const b = addBot();
        (m.meta.skills_matched || []).forEach((s) => addTag(b, `following skill: ${s}`, "skill"));
        (m.meta.tool_calls || []).forEach((t) => {
          const step = addStep(b, t.tool, safeParse(t.arguments));
          finishStep(step, t.ok !== false, t.summary || "");
        });
        if (m.meta.skill_saved) addLearned(b, m.meta.skill_saved);
        setAnswer(b, m.content, false);
      }
    }
    loadConversations();
    show("chat");
    scrollDown(true);
  }
  $("#new-chat").addEventListener("click", newChat);
  const safeParse = (s) => { try { return typeof s === "string" ? JSON.parse(s) : s; } catch { return s; } };

  // ---------- chat rendering ----------
  const emptyTpl = $("#chat-empty").cloneNode(true);
  function clearEmpty() { $("#chat-empty")?.remove(); }
  function scrollDown(force) {
    const box = $("#messages");
    if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight;
  }
  function addUser(text) {
    clearEmpty();
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
    $("#messages").appendChild(el); scrollDown(true);
  }
  function addError(text) {
    clearEmpty();
    const el = document.createElement("div");
    el.className = "msg error";
    el.innerHTML = `<div class="bubble">⚠️ ${esc(text)}</div>`;
    $("#messages").appendChild(el); scrollDown(true);
  }
  function addBot() {
    clearEmpty();
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = `<div class="avatar">M</div><div class="bubble"><div class="meta-row"></div><div class="steps"></div><div class="answer"></div></div>`;
    $("#messages").appendChild(el);
    return el;
  }
  function addTag(bot, text, cls = "") {
    const t = document.createElement("span");
    t.className = `tag ${cls}`; t.textContent = text;
    $(".meta-row", bot).appendChild(t); return t;
  }
  function addLearned(bot, name) {
    const t = addTag(bot, `✨ learned skill: ${name}`, "learned");
    t.title = "Open in Skills"; t.addEventListener("click", () => { show("skills"); openSkill(name); });
  }
  const TOOL_ICONS = { web_search: "🔎", remember: "📝", recall: "🧠", read_skill: "📖", save_skill: "✨", list_skills: "📚", follow_topic: "📰", gmail_recent: "✉️", calendar_today: "📅" };
  function argPreview(args) {
    if (args == null) return "";
    if (typeof args === "string") return args;
    const vals = Object.values(args).filter((v) => typeof v === "string" || typeof v === "number");
    return vals.length ? vals.join(" · ") : JSON.stringify(args);
  }
  function addStep(bot, name, args) {
    const d = document.createElement("details");
    d.className = "step running";
    d.innerHTML = `<summary><span class="s-ico">${TOOL_ICONS[name] || "🔧"}</span><span class="s-name">${esc(name)}</span><span class="s-arg">${esc(argPreview(args))}</span><span class="s-state"></span></summary><div class="body"></div>`;
    $(".body", d).textContent = typeof args === "string" ? args : JSON.stringify(args, null, 1);
    $(".steps", bot).appendChild(d); scrollDown();
    return d;
  }
  function finishStep(step, ok, summary) {
    step.classList.remove("running"); step.classList.add(ok ? "ok" : "fail");
    const body = $(".body", step);
    body.textContent = `${body.textContent}\n→ ${summary}`.trim();
  }
  function setAnswer(bot, text, streaming) {
    const a = $(".answer", bot);
    a.innerHTML = md(text);
    a.classList.toggle("cursor", !!streaming);
  }

  // ---------- send (SSE over fetch) ----------
  async function send(text) {
    if (state.busy || !text.trim()) return;
    if (state.health && !state.health.configured.nebius) {
      addUser(text);
      addError("Chat needs a model key. Set NEBIUS_API_KEY (or LLM_BASE_URL + LLM_API_KEY) in .env and restart. Memory, Skills and Brief still work.");
      return;
    }
    state.busy = true; $("#send").disabled = true;
    addUser(text);
    const bot = addBot();
    let answer = "", thinkEl = null, thinkText = "";
    const steps = {};
    setAnswer(bot, "", true);
    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: state.conversationId }),
      });
      if (res.status === 401) { showLogin(); throw new Error("login required"); }
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader(); const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          switch (ev.type) {
            case "conversation":
              if (!state.conversationId) { state.conversationId = ev.id; $("#chat-title").textContent = text.slice(0, 60); }
              break;
            case "start":
              if (ev.memories_used) addTag(bot, `🧠 ${ev.memories_used} memories recalled`);
              (ev.skills_matched || []).forEach((s) => addTag(bot, `following skill: ${s}`, "skill"));
              break;
            case "thinking":
              thinkText += ev.text;
              if (!thinkEl) {
                thinkEl = document.createElement("details");
                thinkEl.className = "step think";
                thinkEl.innerHTML = `<summary><span class="s-ico">💭</span><span class="s-name">thinking</span><span class="s-arg"></span></summary><div class="body"></div>`;
                $(".steps", bot).appendChild(thinkEl);
              }
              $(".body", thinkEl).textContent = thinkText;
              $(".s-arg", thinkEl).textContent = thinkText.slice(-90).replace(/\s+/g, " ");
              break;
            case "token":
              answer += ev.text; setAnswer(bot, answer, true); scrollDown();
              break;
            case "interim": {
              const p = document.createElement("p"); p.className = "interim"; p.textContent = ev.text;
              $(".steps", bot).appendChild(p);
              answer = ""; setAnswer(bot, "", true); thinkEl = null; thinkText = "";
              break;
            }
            case "tool_call":
              if (answer) { answer = ""; setAnswer(bot, "", true); }
              thinkEl = null; thinkText = "";
              steps[ev.id] = addStep(bot, ev.name, ev.arguments);
              break;
            case "tool_result":
              if (steps[ev.id]) finishStep(steps[ev.id], ev.ok, ev.summary);
              break;
            case "reflecting":
              addTag(bot, "🪞 reflecting: worth saving as a skill?").id = "reflect-tag";
              break;
            case "skill_learned":
              $("#reflect-tag", bot)?.remove();
              addLearned(bot, ev.name); markNewSkill(ev.name);
              toast(`✨ New skill learned: ${ev.name}`);
              break;
            case "done":
              $("#reflect-tag", bot)?.remove();
              answer = ev.reply || answer;
              setAnswer(bot, answer, false);
              break;
            case "error":
              setAnswer(bot, answer, false);
              addError(ev.message);
              break;
          }
        }
      }
    } catch (err) {
      addError(err.message);
    } finally {
      $(".answer", bot).classList.remove("cursor");
      if (!answer && !$(".answer", bot).textContent) $(".answer", bot).remove();
      state.busy = false; $("#send").disabled = false;
      scrollDown(); loadConversations();
    }
  }
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#input"); const text = input.value; input.value = ""; autosize();
    send(text);
  });
  const autosize = () => { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; };
  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  function bindSuggestions() {
    $$(".suggestions .chip").forEach((c) => c.addEventListener("click", () => send(c.dataset.prompt)));
  }

  // ---------- memory ----------
  function memItem(m) {
    const el = document.createElement("div");
    el.className = "item";
    const score = m.score != null ? `<span class="score">match ${(m.score * 100).toFixed(0)}%</span>` : "";
    el.innerHTML = `<div class="content"><div class="text">${esc(m.text)}</div><div class="sub"><span class="tag">${esc(m.kind || "note")}</span>${score}<span>${ago(m.created_at)}</span></div></div><button class="icon-btn" title="Forget">🗑</button>`;
    el.querySelector("button").addEventListener("click", async () => {
      if (!confirm("Forget this memory?")) return;
      await api(`/api/memory/${m.id}`, { method: "DELETE" });
      el.remove(); toast("Forgotten"); refreshMemCount();
    });
    return el;
  }
  async function refreshMemCount() {
    const { total } = await api("/api/memory?k=1&q=");
    $("#mem-count").textContent = `${total} note${total === 1 ? "" : "s"}`;
  }
  async function loadMemory(q) {
    const list = $("#mem-list");
    try {
      const data = await api(q ? `/api/memory?q=${encodeURIComponent(q)}&k=10` : "/api/memory");
      $("#mem-count").textContent = `${data.total} note${data.total === 1 ? "" : "s"}`;
      list.innerHTML = "";
      if (!data.memories.length) list.innerHTML = `<div class="card muted">${q ? "No matches." : "Nothing remembered yet. Add a note above, or just tell MUIS in chat."}</div>`;
      data.memories.forEach((m) => list.appendChild(memItem(m)));
    } catch (err) { list.innerHTML = `<div class="card error-text">${esc(err.message)}</div>`; }
  }
  $("#mem-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/memory", { method: "POST", body: { text: $("#mem-text").value, kind: $("#mem-kind").value } });
      $("#mem-text").value = ""; toast("Remembered"); loadMemory();
    } catch (err) { toast(err.message, true); }
  });
  $("#mem-search").addEventListener("submit", (e) => { e.preventDefault(); loadMemory($("#mem-q").value.trim()); });
  $("#mem-clear").addEventListener("click", () => { $("#mem-q").value = ""; loadMemory(); });

  // ---------- skills ----------
  function isFresh(s) {
    return s.source === "learned" && !state.skillSeen.includes(s.name);
  }
  function markNewSkill(name) {
    state.skillSeen = state.skillSeen.filter((n) => n !== name); saveSeen();
    $("#skills-badge").classList.remove("hidden");
  }
  async function loadSkills() {
    const { skills } = await api("/api/skills");
    const list = $("#skill-list"); list.innerHTML = "";
    const order = { learned: 0, user: 1, bundled: 2 };
    skills.sort((a, b) => (order[a.source] ?? 3) - (order[b.source] ?? 3) || (b.created || "").localeCompare(a.created || ""));
    for (const s of skills) {
      const el = document.createElement("div");
      el.className = "item" + (isFresh(s) ? " fresh" : "") + (s.name === state.currentSkill ? " active" : "");
      el.dataset.name = s.name;
      const badge = s.source === "learned" ? `<span class="tag learned">${isFresh(s) ? "✨ new" : "learned"}</span>` : `<span class="tag">${esc(s.source)}</span>`;
      el.innerHTML = `<div class="skill-name">${esc(s.name)} ${badge}</div><div class="muted small">${esc(s.description)}</div>`;
      el.addEventListener("click", () => openSkill(s.name));
      list.appendChild(el);
    }
    if (!skills.length) list.innerHTML = '<div class="card muted">No skills yet.</div>';
    $("#skills-badge").classList.toggle("hidden", !skills.some(isFresh));
  }
  async function openSkill(name) {
    const s = await api(`/api/skills/${encodeURIComponent(name)}`);
    state.currentSkill = s.name;
    if (!state.skillSeen.includes(s.name)) { state.skillSeen.push(s.name); saveSeen(); }
    $$("#skill-list .item").forEach((i) => { i.classList.toggle("active", i.dataset.name === s.name); });
    const box = $("#skill-detail");
    box.innerHTML = `
      <div class="skill-name" style="font-size:1.05em">${esc(s.name)} <span class="tag ${s.source === "learned" ? "learned" : ""}">${esc(s.source)}</span></div>
      <div class="muted small" style="margin:4px 0 8px">${esc(s.description)}${s.created ? ` · created ${ago(s.created)}` : ""}</div>
      ${s.triggers.length ? `<div class="chips" style="margin-bottom:8px">${s.triggers.map((t) => `<span class="tag skill">“${esc(t)}”</span>`).join("")}</div>` : ""}
      <div class="answer" id="skill-view">${md(s.body)}</div>
      <textarea id="skill-edit" class="hidden"></textarea>
      <div class="skill-actions">
        <button class="btn" id="skill-edit-btn">Edit markdown</button>
        <button class="btn primary hidden" id="skill-save-btn">Save</button>
        <button class="btn ghost hidden" id="skill-cancel-btn">Cancel</button>
        <button class="btn ghost danger" id="skill-del-btn" style="margin-left:auto">Delete</button>
      </div>`;
    $("#skill-edit").value = s.markdown;
    const toggle = (editing) => {
      $("#skill-view").classList.toggle("hidden", editing); $("#skill-edit").classList.toggle("hidden", !editing);
      $("#skill-edit-btn").classList.toggle("hidden", editing);
      $("#skill-save-btn").classList.toggle("hidden", !editing); $("#skill-cancel-btn").classList.toggle("hidden", !editing);
    };
    $("#skill-edit-btn").onclick = () => toggle(true);
    $("#skill-cancel-btn").onclick = () => toggle(false);
    $("#skill-save-btn").onclick = async () => {
      try {
        await api(`/api/skills/${encodeURIComponent(s.name)}`, { method: "PUT", body: { markdown: $("#skill-edit").value } });
        toast("Skill saved"); await loadSkills(); openSkill(s.name);
      } catch (err) { toast(err.message, true); }
    };
    $("#skill-del-btn").onclick = async () => {
      if (!confirm(`Delete skill ${s.name}?`)) return;
      await api(`/api/skills/${encodeURIComponent(s.name)}`, { method: "DELETE" });
      box.innerHTML = '<p class="muted">Skill deleted.</p>'; state.currentSkill = null; loadSkills();
    };
    loadSkills();
  }

  // ---------- brief ----------
  function renderBrief(b, el) {
    const src = (b.sources || []).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>`).join(" · ");
    el.innerHTML = `<div class="brief-meta"><span>☀️ ${new Date(b.created_at).toLocaleString()}</span><span class="tag">${esc(b.trigger)}</span>${b.generated_by ? `<span class="tag">${esc(b.generated_by)}</span>` : ""}</div>
      <div class="answer">${md(b.content)}</div>
      ${src ? `<div class="muted small" style="margin-top:10px">Sources: ${src}</div>` : ""}
      ${(b.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}`;
  }
  async function loadTopics() {
    const { topics, defaults } = await api("/api/topics");
    const box = $("#topic-list"); box.innerHTML = "";
    const shown = topics.length ? topics : defaults;
    if (!shown.length) box.innerHTML = '<span class="muted small">No topics yet. Add one, or ask in chat: “follow news about …”.</span>';
    shown.forEach((t) => {
      const c = document.createElement("span");
      c.className = "chip"; c.innerHTML = `📰 ${esc(t)}${topics.length ? '<button class="x" title="Unfollow">✕</button>' : ' <span class="muted small">(default)</span>'}`;
      c.querySelector(".x")?.addEventListener("click", async () => { await api(`/api/topics/${encodeURIComponent(t)}`, { method: "DELETE" }); loadTopics(); });
      box.appendChild(c);
    });
  }
  async function loadBrief() {
    loadTopics();
    const { briefs } = await api("/api/briefs");
    const cur = $("#brief-current");
    if (briefs.length) renderBrief(briefs[0], cur);
    const hist = $("#brief-history"); hist.innerHTML = "";
    briefs.slice(1).forEach((b) => {
      const d = document.createElement("details"); d.className = "card";
      d.innerHTML = `<summary style="cursor:pointer">${new Date(b.created_at).toLocaleString()} <span class="tag">${esc(b.trigger)}</span></summary><div></div>`;
      renderBrief(b, d.querySelector("div")); hist.appendChild(d);
    });
    if (briefs.length < 2) hist.innerHTML = '<div class="muted small">Older briefs will show up here.</div>';
  }
  $("#brief-run").addEventListener("click", async () => {
    const btn = $("#brief-run"); btn.disabled = true; btn.textContent = "Composing…";
    try { const b = await api("/api/brief", { method: "POST" }); renderBrief(b, $("#brief-current")); toast("Brief ready"); loadBrief(); }
    catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; btn.textContent = "Generate now"; }
  });
  $("#topic-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/topics", { method: "POST", body: { topic: $("#topic-text").value } }); $("#topic-text").value = ""; loadTopics(); }
    catch (err) { toast(err.message, true); }
  });

  // ---------- settings ----------
  async function loadSettings() {
    const h = (await loadHealth()) || state.health; if (!h) return;
    const card = (ok, title, sub, hint) => `<div class="card provider"><div class="p-head"><span class="dot ${ok ? "ok" : ""}"></span>${title}</div><div class="p-sub">${sub}</div>${!ok && hint ? `<div class="muted small"><code>${hint}</code></div>` : ""}</div>`;
    $("#providers").innerHTML = [
      card(h.configured.nebius, h.provider, h.configured.nebius ? "Nemotron chat + embeddings" : "chat disabled", "NEBIUS_API_KEY"),
      card(h.configured.tavily, "Tavily search", h.configured.tavily ? "live web + brief news" : "web search disabled", "TAVILY_API_KEY"),
      card(h.memory.ok, "Memory (Qdrant)", `${h.memory.count} notes · ${h.memory.backend}<br>${esc(h.memory.embedder)}${h.memory.note ? `<br>${esc(h.memory.note)}` : ""}`),
      card(h.configured.google, "Google (optional)", h.configured.google ? "Gmail + Calendar, read-only" : "not connected", "GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN"),
      card(h.scheduler.enabled, "Daily brief", h.scheduler.enabled ? `every day ${h.scheduler.daily_brief_time} ${h.scheduler.timezone}` : "scheduler off", "ENABLE_SCHEDULER=true"),
      card(h.configured.auth, "Password lock", h.configured.auth ? "on" : "off (open to anyone who can reach it)", "APP_PASSWORD"),
    ].join("");
    $("#models").innerHTML = `
      <div>Planning + tools</div><div><code>${esc(h.models.plan)}</code></div>
      <div>Fast (reflection, brief)</div><div><code>${esc(h.models.fast)}</code></div>
      <div>Embeddings</div><div><code>${esc(h.models.embedding)}</code></div>
      <div>Endpoint</div><div><code>${esc(h.base_url)}</code></div>
      <div>Skills</div><div>${h.skills}</div>
      <div>Version</div><div>${esc(h.version)}</div>`;
    $("#tools").innerHTML = h.tools.map((t) => `<span class="chip">${TOOL_ICONS[t] || "🔧"} ${esc(t)}</span>`).join("");
    $("#logout").classList.toggle("hidden", !h.configured.auth);
  }

  // ---------- boot ----------
  async function boot() {
    const auth = await fetch("/api/auth").then((r) => r.json()).catch(() => ({ required: false, authenticated: true }));
    if (auth.required && !auth.authenticated) { showLogin(); return; }
    bindSuggestions();
    await loadHealth();
    loadConversations().catch(() => {});
    api("/api/skills").then(({ skills }) => $("#skills-badge").classList.toggle("hidden", !skills.some(isFresh))).catch(() => {});
    const view = (location.hash || "#chat").slice(1);
    if (loaders[view]) show(view);
  }
  boot();
})();
