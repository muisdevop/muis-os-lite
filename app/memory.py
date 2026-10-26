"""Long-term memory: notes embedded via Token Factory and stored in Qdrant.

Storage modes (QDRANT_URL):
  * http://qdrant:6333  -> a Qdrant server (docker compose)
  * ""                  -> embedded Qdrant persisted on disk under DATA_DIR/qdrant
  * ":memory:"          -> in-process, RAM only (tests)

Embeddings: the provider's embedding model when an API key is set. Without a key, a local
hashing embedder keeps memory usable (keyword-level recall) in a separate collection, so
vectors from different embedders never mix.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

log = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], list[list[float]]]
_WORD = re.compile(r"[a-z0-9']+")
_STOP = set("a an the is are was were be to of in on at for and or my me i you it that this "
            "what who when where how do does did with about from".split())


def _stem(word: str) -> str:
    word = word.strip("'")
    if word.endswith("'s"):
        word = word[:-2]
    for suffix in ("ing", "ies", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def local_embed(texts: list[str], dim: int = 1024) -> list[list[float]]:
    """Offline hashing embedder (stemmed words + bigrams). Used when no embedding key is set."""
    out = []
    for text in texts:
        words = [_stem(w) for w in _WORD.findall(text.lower())]
        words = [w for w in words if w and w not in _STOP]
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        vec = [0.0] * dim
        for f in feats:
            h = int(hashlib.blake2b(f.encode(), digest_size=8).hexdigest(), 16)
            vec[h % dim] += -1.0 if (h >> 20) & 1 else 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec] if any(vec) else [1.0] + [0.0] * (dim - 1))
    return out


class MemoryStore:
    def __init__(
        self,
        embed_fn: EmbedFn,
        url: str = ":memory:",
        collection: str = "muis_memory",
        api_key: str | None = None,
        dim: int = 0,
        client: QdrantClient | None = None,
        path: Path | str | None = None,
        backend_label: str | None = None,
        connect_retries: int = 8,
    ):
        self.embed_fn = embed_fn
        self.url = url
        self.collection = collection
        self.api_key = api_key or None
        self.dim = dim
        self.path = Path(path) if path else None
        self._client = client
        self.backend_label = backend_label
        self.connect_retries = max(1, connect_retries)
        self.fallback_reason: str | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if self.url == ":memory:":
                self._client = QdrantClient(location=":memory:")
            elif not self.url:
                target = self.path or Path("./data/qdrant")
                target.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(target))
            else:
                client = QdrantClient(url=self.url, api_key=self.api_key, timeout=5,
                                      check_compatibility=False)
                for attempt in range(self.connect_retries):
                    try:
                        client.get_collections()
                        break
                    except Exception as exc:
                        if attempt < self.connect_retries - 1:
                            time.sleep(1.5)  # Qdrant may still be booting (compose start)
                            continue
                        # server down: keep working in RAM, and say so in /api/health
                        log.warning("Qdrant at %s unreachable (%s); using in-memory store",
                                    self.url, exc)
                        self.fallback_reason = f"Qdrant unreachable at {self.url}; using in-memory"
                        client = QdrantClient(location=":memory:")
                self._client = client
        return self._client

    @property
    def mode(self) -> str:
        if self.fallback_reason:
            return "in-memory (fallback)"
        if self.url == ":memory:":
            return "in-memory"
        return "embedded (disk)" if not self.url else "server"

    def _exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def _ensure_collection(self, dim: int) -> None:
        if not self._exists():
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dim or dim, distance=Distance.COSINE),
            )

    def add(self, text: str, kind: str = "note", tags: list[str] | None = None) -> str:
        text = (text or "").strip()
        if not text:
            raise ValueError("cannot remember an empty note")
        vector = self.embed_fn([text])[0]
        self._ensure_collection(len(vector))
        point_id = str(uuid.uuid4())
        payload = {
            "text": text,
            "kind": kind or "note",
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return point_id

    def recall(self, query: str, k: int = 5, min_score: float = 0.0) -> list[dict[str, Any]]:
        if not (query or "").strip() or not self._exists():
            return []
        vector = self.embed_fn([query])[0]
        result = self.client.query_points(
            collection_name=self.collection, query=vector, limit=k, with_payload=True
        )
        return [
            {"id": str(p.id), "score": round(p.score, 4), **(p.payload or {})}
            for p in result.points
            if p.score >= min_score
        ]

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        if not self._exists():
            return []
        items: list[dict[str, Any]] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection, limit=256, offset=offset, with_payload=True
            )
            items += [{"id": str(p.id), **(p.payload or {})} for p in points]
            if offset is None or len(items) >= 5000:
                break
        if kind:
            items = [m for m in items if m.get("kind") == kind]
        items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return items[:limit]

    def delete(self, point_id: str) -> bool:
        if not self._exists():
            return False
        found = self.client.retrieve(self.collection, ids=[point_id])
        if not found:
            return False
        self.client.delete(self.collection, points_selector=PointIdsList(points=[point_id]))
        return True

    def count(self) -> int:
        if not self._exists():
            return 0
        return self.client.count(self.collection, exact=True).count
