"""PowerContext-backed learning memory: weak knowledge points + review progress.

Idiomatic PowerContext usage:
  - Memory entries (kind="weak-point") hold the CURRENT state of each weak point
    (review count, interval, next review date) — versioned, reusable across
    sessions and restarts.
  - Source records hold the immutable EVIDENCE log (when a weak point was
    flagged, each review event with its quality rating).

Spaced repetition uses an SM-2-lite schedule so the assistant "remembers what
you keep forgetting" and surfaces it at the right time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, timedelta
from typing import List, Optional

from powercontext.client import PowerContextClient
from powercontext.http._generated.models import (
    CreateScopeRequest,
    CreateSourceRequest,
    ListAtomicMemoryRequest,
    RememberMemoryRequest,
)

from .config import settings

WEAK_KIND = "weak-point"


def _slug(point: str) -> str:
    norm = re.sub(r"\s+", " ", point.strip().lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _today() -> str:
    return date.today().isoformat()


class LearningMemory:
    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url or settings.POWERCONTEXT_URL
        self._scope_id: Optional[str] = None

    def _run(self, coro):
        return asyncio.run(coro)

    def _client(self) -> PowerContextClient:
        return PowerContextClient(base_url=self._base_url, allow_insecure_http=True)

    # ---- scope ----
    def ensure_scope(self) -> str:
        if self._scope_id:
            return self._scope_id

        async def _do() -> str:
            async with self._client() as client:
                page = await client.list_scopes()
                for scope in page.items:
                    if scope.title == settings.POWERCONTEXT_SCOPE_TITLE:
                        return scope.scope_id
                created = await client.create_scope(
                    CreateScopeRequest(
                        title=settings.POWERCONTEXT_SCOPE_TITLE,
                        summary="智研学习助手：薄弱知识点与复习进度记忆",
                        idempotency_key="zhiyan-scope-v1",
                    )
                )
                return created.scope_id

        self._scope_id = self._run(_do())
        return self._scope_id

    @property
    def scope_id(self) -> str:
        if not self._scope_id:
            self.ensure_scope()
        return self._scope_id

    # ---- weak points ----
    def add_weak_point(self, point: str, source: str = "", note: str = "") -> dict:
        self.ensure_scope()
        wid = _slug(point)
        state = {
            "id": wid,
            "point": point,
            "source": source,
            "note": note,
            "version": 1,
            "review_count": 0,
            "interval_days": 0,
            "ease": 2.5,
            "next_review": _today(),
            "created_at": _today(),
            "last_quality": None,
        }

        async def _do() -> dict:
            async with self._client() as client:
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=self._scope_id,
                        kind=WEAK_KIND,
                        text=json.dumps(state, ensure_ascii=False),
                        reason="标记薄弱知识点",
                    )
                )
                await client.create_source(
                    self._scope_id,
                    CreateSourceRequest(
                        content={"event": "weak_point_flagged", **state}
                    ),
                )
                return state

        return self._run(_do())

    def _latest_weak_points(self) -> List[dict]:
        self.ensure_scope()

        async def _do() -> List[dict]:
            async with self._client() as client:
                page = await client.list_atomic_memories(
                    ListAtomicMemoryRequest(
                        scope_id=self._scope_id, kind=WEAK_KIND, limit=100
                    )
                )
                return [r.text for r in page.items]

        texts = self._run(_do())
        by_id: dict = {}
        for t in texts:
            try:
                obj = json.loads(t)
            except (json.JSONDecodeError, TypeError):
                continue
            wid = obj.get("id")
            if not wid:
                continue
            if str(obj.get("state", "")) == "forgotten":
                by_id.pop(wid, None)
                continue
            prev = by_id.get(wid)
            if prev is None or obj.get("version", 0) >= prev.get("version", 0):
                by_id[wid] = obj
        return list(by_id.values())

    def list_weak_points(self) -> List[dict]:
        return sorted(
            self._latest_weak_points(),
            key=lambda x: (x.get("next_review", ""), -x.get("review_count", 0)),
        )

    def record_review(self, point: str, quality: int) -> dict:
        """Record a review with SM-2-lite scheduling. quality in 0..5."""
        self.ensure_scope()
        quality = max(0, min(5, int(quality)))
        wid = _slug(point)
        current = next(
            (w for w in self._latest_weak_points() if w.get("id") == wid), None
        )
        if current is None:
            current = {
                "id": wid, "point": point, "source": "", "note": "",
                "version": 0, "review_count": 0, "interval_days": 0,
                "ease": 2.5, "next_review": _today(), "created_at": _today(),
            }

        review_count = current.get("review_count", 0) + 1
        ease = current.get("ease", 2.5)
        interval = current.get("interval_days", 0)
        if quality >= 3:
            if review_count == 1:
                interval = 1
            elif review_count == 2:
                interval = 6
            else:
                interval = max(1, round(interval * ease))
            ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            review_count = 0
            interval = 1
            ease = max(1.3, ease - 0.2)
        next_review = (date.today() + timedelta(days=interval)).isoformat()

        updated = {
            **current,
            "version": current.get("version", 0) + 1,
            "review_count": review_count,
            "interval_days": interval,
            "ease": round(ease, 3),
            "next_review": next_review,
            "last_quality": quality,
            "last_review": _today(),
        }

        async def _do() -> dict:
            async with self._client() as client:
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=self._scope_id,
                        kind=WEAK_KIND,
                        text=json.dumps(updated, ensure_ascii=False),
                        reason=f"复习评分 {quality}",
                    )
                )
                await client.create_source(
                    self._scope_id,
                    CreateSourceRequest(
                        content={
                            "event": "review_recorded",
                            "id": wid, "point": point,
                            "quality": quality, "next_review": next_review,
                        }
                    ),
                )
                return updated

        return self._run(_do())

    def review_plan(self) -> List[dict]:
        """Weak points due for review today or overdue, most overdue first."""
        today = _today()
        due = [w for w in self._latest_weak_points() if w.get("next_review", today) <= today]
        return sorted(due, key=lambda x: x.get("next_review", ""))

    def stats(self) -> dict:
        points = self._latest_weak_points()
        today = _today()
        return {
            "weak_points": len(points),
            "due_today": len([w for w in points if w.get("next_review", today) <= today]),
            "mastered": len([w for w in points if w.get("review_count", 0) >= 3]),
            "scope_id": self.scope_id,
        }


_mem: Optional[LearningMemory] = None


def get_memory() -> LearningMemory:
    global _mem
    if _mem is None:
        _mem = LearningMemory()
    return _mem
