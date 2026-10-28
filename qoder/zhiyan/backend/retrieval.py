"""Hybrid retrieval on seekdb: HNSW vector leg + ik fulltext leg + RRF fusion.

Every leg is executed by seekdb itself:

* vector    `order by cosine_distance(embedding, ?) approx limit N`   -> VECTOR INDEX SCAN
* fulltext  `where match(text_body) against(?) ... order by relevance` -> TEXT RETRIEVAL SCAN
* scalar    `where doc_type = ?` pushed into both legs

Fusion is Reciprocal Rank Fusion (score = w/(k0+rank)); per-leg ranks and the
fused score are returned so a reviewer can see *why* a document ranked where it
did, and so `explain()` can prove both indexes were actually used.
"""

from __future__ import annotations

import os
from typing import List, Optional

from . import db, embeddings
from .config import settings

RRF_K0 = float(os.environ.get("ZHIYAN_RRF_K0", "60"))
# Defaults are the measured optimum on the bundled 48-document corpus with the
# MiniMax embo-01 semantic backend (scripts/verify.py reproduces the sweep):
# weighted fusion at w_vec=0.25 reaches recall@5 0.8472 versus 0.8194 for the best
# single leg, while the textbook RRF w=0.5 default scores only 0.7639 — i.e. a
# badly configured hybrid search is *worse* than keyword search alone.
W_VEC = float(os.environ.get("ZHIYAN_W_VEC", "0.25"))
FUSE_DEFAULT = os.environ.get("ZHIYAN_FUSE", "weighted")
CANDIDATE_DEPTH = int(os.environ.get("ZHIYAN_DEPTH", "10"))


def _vector_leg(cur, query_vec, depth, doc_type):
    where = "where doc_type = %s " if doc_type else ""
    lit = embeddings.as_sql_vector(query_vec)
    sql = (f"select doc_id, cosine_distance(embedding, %s) dist from {db.TABLE} "
           f"{where}order by cosine_distance(embedding, %s) approx limit %s")
    args = (lit, lit, depth) if not doc_type else (lit, doc_type, lit, depth)
    cur.execute(sql, args)
    return {str(r[0]): 1.0 - float(r[1]) for r in cur.fetchall()}


def _fulltext_leg(cur, query, depth, doc_type, boolean_mode=False):
    mode = " in boolean mode" if boolean_mode else ""
    expr = f"match(text_body) against(%s{mode})"
    where = f"and doc_type = %s " if doc_type else ""
    sql = (f"select doc_id, {expr} relevance from {db.TABLE} "
           f"where {expr} {where}order by relevance desc limit %s")
    args = (query, query, depth) if not doc_type else (query, query, doc_type, depth)
    cur.execute(sql, args)
    return {str(r[0]): float(r[1] or 0.0) for r in cur.fetchall()}


def _ranked(score_map):
    return [d for d, _s in sorted(score_map.items(), key=lambda kv: (-kv[1], kv[0]))]


def _rrf(vec_rank, ft_rank, k0=RRF_K0, w_vec=W_VEC):
    fused = {}

    def slot(doc_id):
        return fused.setdefault(doc_id, {"vector_rank": None, "fulltext_rank": None, "score": 0.0})

    for rank, doc_id in enumerate(vec_rank, start=1):
        entry = slot(doc_id)
        entry["vector_rank"] = rank
        entry["score"] += w_vec / (k0 + rank)
    for rank, doc_id in enumerate(ft_rank, start=1):
        entry = slot(doc_id)
        entry["fulltext_rank"] = rank
        entry["score"] += (1.0 - w_vec) / (k0 + rank)
    return sorted(fused.items(), key=lambda kv: (-kv[1]["score"], kv[0]))


def _normalize(score_map):
    if not score_map:
        return {}
    values = list(score_map.values())
    lo, hi = min(values), max(values)
    span = hi - lo
    return {d: (1.0 if span == 0 else (v - lo) / span) for d, v in score_map.items()}


def _weighted(vec_scores, ft_scores, vec_rank, ft_rank, w_vec=W_VEC):
    """Min-max normalized score fusion. Unlike RRF this keeps "how strong was the
    match" information, so a very strong single-leg hit is not buried by two weak
    consensus votes — which is what the bundled corpus measures."""
    nv, nf = _normalize(vec_scores), _normalize(ft_scores)
    vr = {d: i + 1 for i, d in enumerate(vec_rank)}
    fr = {d: i + 1 for i, d in enumerate(ft_rank)}
    fused = {}
    for doc_id in set(nv) | set(nf):
        fused[doc_id] = {
            "vector_rank": vr.get(doc_id),
            "fulltext_rank": fr.get(doc_id),
            "score": w_vec * nv.get(doc_id, 0.0) + (1.0 - w_vec) * nf.get(doc_id, 0.0),
        }
    return sorted(fused.items(), key=lambda kv: (-kv[1]["score"], kv[0]))


class HybridStore:
    def __init__(self, backend: Optional[str] = None) -> None:
        self.backend = backend or settings.EMBED_BACKEND
        self._conn = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def conn(self):
        if self._conn is None or not self._conn.open:
            db.ensure_database()
            self._conn = db.connect()
        return self._conn

    def close(self):
        if self._conn is not None and self._conn.open:
            self._conn.close()
        self._conn = None

    def _embed(self, texts: List[str]):
        return embeddings.embed(texts, self.backend)

    def reset(self) -> dict:
        dim = len(self._embed(["dimension probe"])[0])
        db.ensure_database()
        conn = db.connect()
        try:
            db.reset_table(conn, dim)
        finally:
            self.close()
        return {"dim": dim, "indexes": db.indexes(conn)}

    def ingest(self, documents: List[dict]) -> dict:
        rows = []
        texts = [f"{d.get('title','')} {d.get('section','')} {d['text']}" for d in documents]
        vectors = self._embed(texts)
        for doc, vec in zip(documents, vectors):
            rows.append({
                "doc_id": str(doc["id"]),
                "title": doc.get("title", ""),
                "section": doc.get("section", ""),
                "doc_type": doc.get("type", ""),
                "tags": ",".join(doc.get("tags", [])),
                "text_body": doc["text"],
                "source_url": doc.get("url", ""),
                "embedding": embeddings.as_sql_vector(vec),
            })
        conn = self.conn
        if not db.table_exists(conn) or db.vector_dim(conn) != len(vectors[0]):
            self.reset()
            conn = self.conn
        inserted = db.insert_documents(conn, rows)
        return {"ingested": inserted, "stored": db.count(conn),
                "dim": len(vectors[0]), "embedding": embeddings.describe(self.backend)}

    # -- retrieval ---------------------------------------------------------
    def search(self, query: str, top_k: int = 5, doc_type: Optional[str] = None,
               mode: str = "hybrid", depth: int = CANDIDATE_DEPTH,
               w_vec: float = W_VEC, k0: float = RRF_K0, fuse: str = FUSE_DEFAULT) -> dict:
        if depth < top_k:
            raise ValueError("候选深度必须 >= top_k")
        qvec = self._embed([query])[0]
        with self.conn.cursor() as cur:
            vec_scores = _vector_leg(cur, qvec, depth, doc_type) if mode in ("vector", "hybrid") else {}
            ft_scores = _fulltext_leg(cur, query, depth, doc_type) if mode in ("fulltext", "hybrid") else {}
        vec_rank, ft_rank = _ranked(vec_scores), _ranked(ft_scores)
        # Only the head of each leg gets a fusion vote: tail candidates from a weak
        # leg otherwise outrank real keyword hits and make the fusion worse than
        # either leg alone (measured on the bundled corpus).
        fuse_n = min(depth, max(top_k * 4, 10))
        vec_head, ft_head = vec_rank[:fuse_n], ft_rank[:fuse_n]
        if mode == "vector":
            fused = [(d, {"vector_rank": i + 1, "fulltext_rank": None, "score": s})
                     for i, (d, s) in enumerate(sorted(vec_scores.items(), key=lambda kv: -kv[1]))]
        elif mode == "fulltext":
            fused = [(d, {"vector_rank": None, "fulltext_rank": i + 1, "score": s})
                     for i, (d, s) in enumerate(sorted(ft_scores.items(), key=lambda kv: -kv[1]))]
        else:
            if fuse == "weighted":
                fused = _weighted(vec_scores, ft_scores, vec_rank, ft_rank, w_vec=w_vec)
            else:
                fused = _rrf(vec_head, ft_head, k0=k0, w_vec=w_vec)
        page = fused[:top_k]
        docs = self._fetch_docs([d for d, _ in page])
        results = []
        for doc_id, meta in page:
            rec = docs.get(doc_id, {})
            results.append({
                "doc_id": doc_id,
                "title": rec.get("title", ""),
                "section": rec.get("section", ""),
                "type": rec.get("doc_type", ""),
                "tags": [t for t in (rec.get("tags") or "").split(",") if t],
                "text": rec.get("text_body", ""),
                "source_url": rec.get("source_url", ""),
                "score": round(meta["score"], 6),
                "vector_rank": meta["vector_rank"],
                "fulltext_rank": meta["fulltext_rank"],
                "vector_similarity": round(vec_scores.get(doc_id), 4) if doc_id in vec_scores else None,
                "fulltext_relevance": round(ft_scores.get(doc_id), 4) if doc_id in ft_scores else None,
                "matched_by": [n for n, r in (("vector", meta["vector_rank"]),
                                              ("fulltext", meta["fulltext_rank"])) if r],
            })
        return {"query": query, "mode": mode, "top_k": top_k, "doc_type": doc_type,
                "candidates": {"vector": len(vec_rank), "fulltext": len(ft_rank)},
                "embedding": embeddings.describe(self.backend), "results": results}

    def _fetch_docs(self, doc_ids: List[str]) -> dict:
        if not doc_ids:
            return {}
        marks = ",".join(["%s"] * len(doc_ids))
        with self.conn.cursor() as cur:
            cur.execute(f"select doc_id,title,section,doc_type,tags,text_body,source_url "
                         f"from {db.TABLE} where doc_id in ({marks})", doc_ids)
            cols = ["doc_id", "title", "section", "doc_type", "tags", "text_body", "source_url"]
            return {str(r[0]): dict(zip(cols, r)) for r in cur.fetchall()}

    # -- evidence ----------------------------------------------------------
    def explain(self, query: str) -> dict:
        """Plans + the single-SQL RRF variant, for the verification record."""
        qvec = self._embed([query])[0]
        lit = embeddings.as_sql_vector(qvec)
        out = {}
        with self.conn.cursor() as cur:
            cur.execute(f"explain select doc_id from {db.TABLE} "
                        f"order by cosine_distance(embedding, '{lit}') approx limit 5")
            out["vector_plan"] = "\n".join(str(r[0]) for r in cur.fetchall())
            cur.execute(f"explain select doc_id from {db.TABLE} where match(text_body) "
                        f"against('{query}') limit 5")
            out["fulltext_plan"] = "\n".join(str(r[0]) for r in cur.fetchall())
            cur.execute(
                f"""with v as (select doc_id, row_number() over(order by cosine_distance(embedding, '{lit}')) rn
                              from {db.TABLE} order by cosine_distance(embedding, '{lit}') approx limit 10),
                          f as (select doc_id, row_number() over(order by match(text_body) against('{query}') desc) rn
                                from {db.TABLE} where match(text_body) against('{query}'))
                        select doc_id, sum(1.0/({RRF_K0}+rn)) rrf
                        from (select * from v union all select * from f) t
                        group by doc_id order by rrf desc limit 5""")
            out["single_sql_rrf"] = [{"doc_id": str(r[0]), "rrf": float(r[1])} for r in cur.fetchall()]
        out["indexes"] = db.indexes(self.conn)
        out["engine"] = db.engine_version(self.conn)
        return out

    def stats(self) -> dict:
        return {"table": db.TABLE, "documents": db.count(self.conn),
                "vector_dim": db.vector_dim(self.conn), "indexes": db.indexes(self.conn),
                "engine": db.engine_version(self.conn) if db.table_exists(self.conn) else None,
                "embedding": embeddings.describe(self.backend)}


_store: Optional[HybridStore] = None


def get_store(backend: Optional[str] = None) -> HybridStore:
    global _store
    if _store is None or (backend and backend != _store.backend):
        _store = HybridStore(backend)
    return _store
