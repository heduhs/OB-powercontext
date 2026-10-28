"""Grounded answering with two engines.

  extractive (default, no key)  picks the sentences from the retrieved chunks that
                                actually carry the query's terms, and cites them.
  minimax    (MINIMAX_API_KEY)  MiniMax-M1 answers strictly from the retrieved
                                chunks with [n] citations.

Both engines answer **only** from what seekdb returned, so the retrieval layer is
always the thing under test, and every answer carries its sources.
"""

from __future__ import annotations

import re
from typing import List

from .config import settings


def _tokens(text: str) -> set:
    low = (text or "").lower()
    words = set(re.findall(r"[a-z0-9]{2,}", low))
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", low)
    grams = set(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return words | grams


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？；\n])", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def extractive_answer(question: str, hits: List[dict], max_sentences: int = 4) -> dict:
    if not hits:
        return {"engine": "extractive",
                "answer": "没有检索到相关资料：请先执行导入（/api/ingest），或换一个查询词。",
                "citations": []}
    qterms = _tokens(question)
    scored = []
    for idx, hit in enumerate(hits, start=1):
        for sentence in _sentences(hit.get("text", "")):
            overlap = len(qterms & _tokens(sentence))
            if overlap:
                scored.append((overlap, len(sentence), idx, hit, sentence))
    if not scored:  # nothing shares a term with the query: fall back to top source
        scored = [(0, 0, i, h, _sentences(h.get("text", ""))[:1][0] if _sentences(h.get("text", "")) else "")
                  for i, h in enumerate(hits, start=1)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen, seen, lines = [], set(), []
    for _o, _l, idx, hit, sentence in scored:
        if len(lines) >= max_sentences:
            break
        if sentence in seen:
            continue
        seen.add(sentence)
        lines.append(f"{sentence} [{idx}]")
        chosen.append(idx)
    citations = [{"index": i, "title": h.get("title", ""), "section": h.get("section", ""),
                  "doc_id": h.get("doc_id", ""), "used": (i in chosen)}
                 for i, h in enumerate(hits, start=1)]
    return {"engine": "extractive",
            "answer": "\n".join(lines) if lines else "检索到的片段与查询词面重叠很低，建议改用混合检索或调整查询。",
            "citations": citations}


def chat_answer(question: str, hits: List[dict]) -> dict:
    """Answer with MiniMax when a key is configured, otherwise extractively."""
    if not settings.MINIMAX_API_KEY:
        result = extractive_answer(question, hits)
        result["note"] = "未配置 MINIMAX_API_KEY，使用离线抽取式回答（配置后自动切换 MiniMax-M1）"
        return result
    from . import llm
    try:
        result = llm.chat_answer(question, hits)
        result["engine"] = settings.MINIMAX_CHAT_MODEL
        return result
    except Exception as exc:  # noqa: BLE001 — a model outage must not kill the API
        result = extractive_answer(question, hits)
        result["note"] = f"MiniMax 调用失败，已回退到抽取式回答：{exc}"
        return result
