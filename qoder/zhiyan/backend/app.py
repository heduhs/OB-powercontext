"""ZhiYan (智研) backend — FastAPI app.

Serves the study-assistant API (seekdb hybrid retrieval + MiniMax grounded Q&A +
PowerContext learning memory) and hosts the frontend for local runs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm
from .config import settings
from .memory import get_memory
from .retrieval import get_store
from .sample_data import all_documents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zhiyan")

app = FastAPI(title="智研 ZhiYan", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------- request models ----------
class SearchReq(BaseModel):
    query: str
    top_k: int = 5
    type: Optional[str] = None


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


# ---------- api ----------
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "corpus_count": get_store().count(),
        "minimax": bool(settings.MINIMAX_API_KEY),
        "powercontext": settings.POWERCONTEXT_URL,
    }


@app.post("/api/ingest")
def ingest():
    result = get_store().ingest(all_documents())
    return result


@app.post("/api/search")
def search(req: SearchReq):
    hits = get_store().search(req.query, top_k=req.top_k, doc_type=req.type)
    return {"query": req.query, "count": len(hits), "results": hits}


@app.post("/api/ask")
def ask(req: AskReq):
    hits = get_store().search(req.question, top_k=req.top_k)
    result = llm.chat_answer(req.question, hits)
    return {"question": req.question, **result, "sources": hits}


@app.post("/api/weak-points")
def add_weak_point(req: WeakPointReq):
    state = get_memory().add_weak_point(req.point, req.source, req.note)
    return {"saved": True, "weak_point": state}


@app.get("/api/weak-points")
def list_weak_points():
    return {"weak_points": get_memory().list_weak_points()}


@app.post("/api/review")
def record_review(req: ReviewReq):
    updated = get_memory().record_review(req.point, req.quality)
    return {"updated": updated}


@app.get("/api/review/plan")
def review_plan():
    return {"due": get_memory().review_plan()}


@app.get("/api/stats")
def stats():
    return {
        "corpus": get_store().count(),
        "memory": get_memory().stats(),
    }


# ---------- frontend (local run) ----------
# Mounted at "/" AFTER the API routes so /api/* takes precedence and the
# static assets (index.html, style.css, app.js) resolve at the site root.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def run():
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    run()
