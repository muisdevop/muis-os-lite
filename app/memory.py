"""Long-term memory: notes embedded via Token Factory and stored in Qdrant."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

EmbedFn = Callable[[list[str]], list[list[float]]]


class MemoryStore:
    def __init__(
        self,
        embed_fn: EmbedFn,
        url: str = ":memory:",
        collection: str = "muis_memory",
        api_key: str | None = None,
        dim: int = 0,
        client: QdrantClient | None = None,
    ):
        self.embed_fn = embed_fn
        self.url = url
        self.collection = collection
        self.api_key = api_key or None
        self.dim = dim
        self._client = client

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if self.url == ":memory:":
                self._client = QdrantClient(location=":memory:")
            else:
                self._client = QdrantClient(url=self.url, api_key=self.api_key)
        return self._client

    def _exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def _ensure_collection(self, dim: int) -> None:
        if not self._exists():
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dim or dim, distance=Distance.COSINE),
            )

    def add(self, text: str, kind: str = "note", tags: list[str] | None = None) -> str:
        text = text.strip()
        if not text:
            raise ValueError("cannot remember an empty note")
        vector = self.embed_fn([text])[0]
        self._ensure_collection(len(vector))
        point_id = str(uuid.uuid4())
        payload = {
            "text": text,
            "kind": kind,
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return point_id

    def recall(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        if not query.strip() or not self._exists():
            return []
        vector = self.embed_fn([query])[0]
        result = self.client.query_points(
            collection_name=self.collection, query=vector, limit=k, with_payload=True
        )
        return [{"id": str(p.id), "score": p.score, **(p.payload or {})} for p in result.points]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self._exists():
            return []
        points, _ = self.client.scroll(
            collection_name=self.collection, limit=limit, with_payload=True
        )
        items = [{"id": str(p.id), **(p.payload or {})} for p in points]
        return sorted(items, key=lambda m: m.get("created_at", ""), reverse=True)
