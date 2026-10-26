"""SQLite persistence: conversations, messages, daily briefs and followed news topics."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    trigger TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # --- conversations -------------------------------------------------------------------
    def create_conversation(self, title: str) -> str:
        cid = uuid.uuid4().hex[:12]
        now = _now()
        title = " ".join(title.split())[:60] or "New chat"
        self._exec("INSERT INTO conversations VALUES (?, ?, ?, ?)", (cid, title, now, now))
        return cid

    def get_conversation(self, cid: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM conversations WHERE id = ?", (cid,))
        if not rows:
            return None
        conv = rows[0]
        conv["messages"] = self.messages(cid)
        return conv

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        )

    def delete_conversation(self, cid: str) -> bool:
        return self._exec("DELETE FROM conversations WHERE id = ?", (cid,)).rowcount > 0

    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> None:
        now = _now()
        self._exec(
            "INSERT INTO messages (conversation_id, role, content, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, role, content, json.dumps(meta or {}, default=str), now),
        )
        self._exec("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))

    def messages(self, cid: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT role, content, meta, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (cid,),
        )
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    def history(self, cid: str, limit: int = 20) -> list[dict[str, str]]:
        """Recent user/assistant turns in OpenAI message format, for the model's context."""
        msgs = [m for m in self.messages(cid) if m["role"] in ("user", "assistant")]
        return [{"role": m["role"], "content": m["content"]} for m in msgs[-limit:]]

    # --- briefs --------------------------------------------------------------------------
    def add_brief(self, content: str, sources: list[dict] | None = None,
                  trigger: str = "manual") -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO briefs (content, sources, trigger, created_at) VALUES (?, ?, ?, ?)",
            (content, json.dumps(sources or []), trigger, _now()),
        )
        return self.get_brief(cur.lastrowid)  # type: ignore[arg-type]

    def get_brief(self, bid: int) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM briefs WHERE id = ?", (bid,))
        return self._brief(rows[0]) if rows else None

    def list_briefs(self, limit: int = 30) -> list[dict[str, Any]]:
        return [self._brief(r) for r in
                self._all("SELECT * FROM briefs ORDER BY id DESC LIMIT ?", (limit,))]

    @staticmethod
    def _brief(row: dict[str, Any]) -> dict[str, Any]:
        row["sources"] = json.loads(row.get("sources") or "[]")
        return row

    # --- topics --------------------------------------------------------------------------
    def add_topic(self, name: str) -> str:
        name = " ".join(name.split())[:80]
        if not name:
            raise ValueError("topic is empty")
        self._exec("INSERT OR IGNORE INTO topics VALUES (?, ?)", (name, _now()))
        return name

    def remove_topic(self, name: str) -> bool:
        return self._exec("DELETE FROM topics WHERE name = ?", (name,)).rowcount > 0

    def topics(self) -> list[str]:
        return [r["name"] for r in self._all("SELECT name FROM topics ORDER BY created_at")]
