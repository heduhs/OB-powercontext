"""MiniMax client: embeddings (embo-01) and grounded chat (MiniMax-M1)."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import List

from .config import settings

logger = logging.getLogger(__name__)


def _post(path: str, payload: dict, timeout: int) -> dict:
    url = settings.MINIMAX_BASE_URL.rstrip("/") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def embed(texts: List[str]) -> List[List[float]]:
    """Return MiniMax embo-01 embeddings (1536-dim) for a batch of texts."""
    if not texts:
        return []
    out: List[List[float]] = []
    last_exc = None
    for i in range(0, len(texts), 32):
        batch = texts[i : i + 32]
        for attempt in range(4):
            try:
                body = _post(
                    "/embeddings",
                    {"model": settings.MINIMAX_EMBED_MODEL, "texts": batch, "type": "db"},
                    timeout=60,
                )
                vectors = body.get("vectors") or body.get("embeddings")
                if vectors is None:
                    raise RuntimeError(f"no vectors in response: {body.get('base_resp')}")
                out.extend(vectors)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("embed attempt %d failed: %s", attempt + 1, exc)
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"embedding failed: {last_exc}")
    return out


def chat_answer(question: str, context_chunks: List[dict]) -> dict:
    """Grounded Q&A: answer strictly from the retrieved chunks, citing sources."""
    if not context_chunks:
        return {"answer": "没有检索到相关资料，无法回答。请先导入文献或笔记，或换个关键词。", "citations": []}
    context_text = "\n\n".join(
        f"[{i+1}] 出处《{c.get('title','')}》/ {c.get('section','')}\n{c.get('text','')}"
        for i, c in enumerate(context_chunks)
    )
    system = (
        "你是智研学习助手。只依据给定资料回答用户问题，用中文，简洁准确。"
        "在引用某条资料时用 [编号] 标注。若资料不足以回答，直接说明，不要编造。"
    )
    user = f"## 资料\n{context_text}\n\n## 问题\n{question}\n\n请作答，并在句末用[编号]标注来源。"
    last_exc = None
    for attempt in range(3):
        try:
            body = _post(
                "/text/chatcompletion_v2",
                {
                    "model": settings.MINIMAX_CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 16384,
                },
                timeout=300,
            )
            answer = body["choices"][0]["message"]["content"]
            return {
                "answer": answer,
                "citations": [
                    {"index": i + 1, "title": c.get("title", ""), "section": c.get("section", "")}
                    for i, c in enumerate(context_chunks)
                ],
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("chat attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2.0 * (attempt + 1))
    return {"answer": f"模型服务暂时不可用：{last_exc}", "citations": []}
