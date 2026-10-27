"""seekdb-backed hybrid retrieval over the literature/notes corpus."""

from __future__ import annotations

import logging
from typing import List, Optional

import pyseekdb

from . import llm
from .config import settings

logger = logging.getLogger(__name__)


class RetrievalStore:
    """Stores corpus chunks in seekdb and serves hybrid (vector+fulltext) search."""

    def __init__(self) -> None:
        self._client = pyseekdb.RemoteServerClient(
            host=settings.SEEKDB_HOST,
            port=settings.SEEKDB_PORT,
            tenant=settings.SEEKDB_TENANT,
            user=settings.SEEKDB_USER,
            password=settings.SEEKDB_PASSWORD,
            database=settings.SEEKDB_DATABASE,
        )
        self._col = None

    def _ensure_db(self) -> None:
        import pymysql

        conn = pymysql.connect(
            host=settings.SEEKDB_HOST,
            port=settings.SEEKDB_PORT,
            user=f"{settings.SEEKDB_USER}@{settings.SEEKDB_TENANT}",
            password=settings.SEEKDB_PASSWORD,
            autocommit=True,
        )
        try:
            conn.cursor().execute(
                f"CREATE DATABASE IF NOT EXISTS {settings.SEEKDB_DATABASE}"
            )
        finally:
            conn.close()

    def _collection(self):
        if self._col is None:
            self._col = self._client.get_or_create_collection(
                name=settings.COLLECTION,
                embedding_function=_MiniMaxEmbedding(),
            )
        return self._col

    def ingest(self, documents: List[dict]) -> dict:
        """(Re)build the corpus collection from documents."""
        self._ensure_db()
        col = self._client.get_or_create_collection(
            name=settings.COLLECTION, embedding_function=_MiniMaxEmbedding()
        )
        self._col = col
        ids = [d["id"] for d in documents]
        texts = [f"{d['title']} {d['section']} {d['text']}" for d in documents]
        metas = [
            {
                "type": d["type"],
                "title": d["title"],
                "section": d["section"],
                "tags": ",".join(d.get("tags", [])),
                "text": d["text"],
            }
            for d in documents
        ]
        col.upsert(ids=ids, documents=texts, metadatas=metas)
        return {"ingested": len(documents), "collection": settings.COLLECTION}

    def search(
        self, query: str, top_k: int = 5, doc_type: Optional[str] = None
    ) -> List[dict]:
        col = self._collection()
        where = {"type": doc_type} if doc_type else None
        res = col.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            hits.append(
                {
                    "title": meta.get("title", ""),
                    "section": meta.get("section", ""),
                    "type": meta.get("type", ""),
                    "tags": (meta.get("tags") or "").split(","),
                    "text": meta.get("text", doc),
                    "score": round(1.0 - float(dist), 4),
                    "distance": round(float(dist), 4),
                }
            )
        return hits

    def count(self) -> int:
        try:
            return self._collection().count()
        except Exception:  # noqa: BLE001
            return 0


@pyseekdb.register_embedding_function
class _MiniMaxEmbedding:
    """pyseekdb embedding function backed by MiniMax embo-01."""

    def __call__(self, documents):
        if isinstance(documents, str):
            documents = [documents]
        return llm.embed(list(documents))

    @property
    def dimension(self) -> int:
        return settings.EMBED_DIM

    @staticmethod
    def name() -> str:
        return "minimax_embo"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(_config: dict):
        return _MiniMaxEmbedding()


_store: Optional[RetrievalStore] = None


def get_store() -> RetrievalStore:
    global _store
    if _store is None:
        _store = RetrievalStore()
    return _store
