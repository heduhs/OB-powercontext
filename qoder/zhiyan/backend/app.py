"""ZhiYan (智研) backend — FastAPI app.

Routes
  GET  /api/health          engine / index / backend status
  POST /api/ingest          load the bundled corpus into seekdb
  POST /api/search          hybrid | vector | fulltext retrieval (mode=hybrid)
  GET  /api/explain         seekdb query plans + single-SQL RRF (proof the indexes run)
  POST /api/ask             grounded answer over the retrieved chunks
  POST /api/weak-points     store a weak knowledge point (PowerContext Memory)
  GET  /api/weak-points     list stored weak points
  POST /api/review          record a review grade -> SM-2-lite reschedule
  GET  /api/review/plan     what is due today
  GET  /api/stats           corpus size + memory inventory

The frontend directory is mounted last so /api/* keeps precedence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import answer as answer_mod
from . import db
from .answer import chat_answer
from .config import settings
from .memory import get_memory
from .retrieval import get_store
from .sample_data import all_documents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zhiyan")

app = FastAPI(title="智研 ZhiYan", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "site"


class SearchReq(BaseModel):
    query: str
    top_k: int = 5
    type: Optional[str] = None
    mode: str = "hybrid"


class AskReq(BaseModel):
    question: str
    top_k: int = 5


class WeakPointReq(BaseModel):
    point: str
    source: str = ""
    note: str = ""


class ReviewReq(BaseModel):
    point: str
    quality: int


def _store():
    return get_store()


@app.get("/api/health")
def health():
    conn = None
    try:
        conn = db.connect()
        indexes = db.indexes(conn)
        ready = db.table_exists(conn)
        corpus = db.count(conn) if ready else 0
        engine = db.engine_version(conn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"seekdb 不可用：{exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    return {
        "status": "ok",
        "engine": engine,
        "corpus_count": corpus,
        "table_ready": ready,
        "indexes": [i["type"] for i in indexes],
        "embedding": {"backend": settings.EMBED_BACKEND},
        "answer_engine": settings.MINIMAX_CHAT_MODEL if settings.MINIMAX_API_KEY else "extractive",
        "powercontext": settings.POWERCONTEXT_URL,
    }


@app.post("/api/ingest")
def ingest():
    return _store().ingest(all_documents())


@app.post("/api/search")
def search(req: SearchReq):
    if req.mode not in ("hybrid", "vector", "fulltext"):
        raise HTTPException(status_code=400, detail="mode 只能是 hybrid / vector / fulltext")
    try:
        return _store().search(req.query, top_k=req.top_k, doc_type=req.type, mode=req.mode)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/explain")
def explain(q: str = "混合检索"):
    try:
        return _store().explain(q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/ask")
def ask(req: AskReq):
    hits = _store().search(req.question, top_k=req.top_k)["results"]
    result = chat_answer(req.question, hits)
    return {"question": req.question, **result, "sources": hits}


@app.post("/api/weak-points")
def add_weak_point(req: WeakPointReq):
    return {"saved": True, "weak_point": get_memory().add_weak_point(req.point, req.source, req.note)}


@app.get("/api/weak-points")
def list_weak_points():
    return {"weak_points": get_memory().list_weak_points()}


@app.post("/api/review")
def record_review(req: ReviewReq):
    return {"updated": get_memory().record_review(req.point, req.quality)}


@app.get("/api/review/plan")
def review_plan():
    return {"due": get_memory().review_plan()}


@app.get("/api/stats")
def stats():
    return {"retrieval": _store().stats(), "memory": get_memory().stats()}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="site")


def run():
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    run()
