"""Task state persistence over PowerContext.

Task progress, decisions and verification results are saved as PowerContext
Memory entries (explicit writes, no model required) plus Source records as
evidence. State survives module restarts: the scope id is cached locally and
entries are listed back through the PowerContext HTTP API.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List, Optional

from powercontext.client import PowerContextClient
from powercontext.http._generated.models import (
    CreateScopeRequest,
    CreateSourceRequest,
    ListAtomicMemoryRequest,
    RememberMemoryRequest,
)

from .config import settings

SCOPE_TITLE = "SDD 上下文管理任务状态"
MEMORY_KIND = "sdd-task-state"
SCOPE_CACHE = Path(__file__).resolve().parent.parent / ".state" / "scope_id.json"


class StateStore:
    """Thin sync facade over the async PowerContext client."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url or settings.powercontext_url
        self._scope_id: Optional[str] = None

    def _run(self, coro):
        return asyncio.run(coro)

    def _client_ctx(self):
        client = PowerContextClient(base_url=self._base_url, allow_insecure_http=True)
        return client

    def ensure_scope(self) -> str:
        """Resolve the scope id: local cache -> lookup by title -> create.

        The resolved id is cached under minimax/.state so a restarted module
        keeps working on the same PowerContext scope.
        """
        if self._scope_id:
            return self._scope_id
        if SCOPE_CACHE.exists():
            cached = json.loads(SCOPE_CACHE.read_text(encoding="utf-8")).get("scope_id")
            if cached:
                self._scope_id = cached
                return cached

        async def _do() -> str:
            async with self._client_ctx() as client:
                page = await client.list_scopes()
                for scope in page.items:
                    if scope.title == SCOPE_TITLE:
                        return scope.scope_id
                created = await client.create_scope(
                    CreateScopeRequest(
                        title=SCOPE_TITLE,
                        summary="SDD上下文管理系统的任务状态、决定与验证结果",
                        idempotency_key="sddctx-scope-v1",
                    )
                )
                return created.scope_id

        self._scope_id = self._run(_do())
        SCOPE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SCOPE_CACHE.write_text(
            json.dumps({"scope_id": self._scope_id}, ensure_ascii=False), encoding="utf-8"
        )
        return self._scope_id

    @property
    def scope_id(self) -> str:
        if not self._scope_id:
            self.ensure_scope()
        return self._scope_id

    def save_progress(self, task_id: str, entry: dict) -> dict:
        """Save one progress record (decision / todo state / verification)."""
        self.ensure_scope()

        async def _do() -> dict:
            async with self._client_ctx() as client:
                payload = json.dumps({"task_id": task_id, **entry}, ensure_ascii=False)
                resp = await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=self._scope_id,
                        kind=MEMORY_KIND,
                        text=payload,
                        reason=entry.get("reason"),
                    )
                )
                return json.loads(resp.model_dump_json())

        return self._run(_do())

    def save_evidence(self, task_id: str, content: dict) -> dict:
        """Attach an immutable Source record as evidence (logs, diffs)."""
        self.ensure_scope()

        async def _do() -> dict:
            async with self._client_ctx() as client:
                resp = await client.create_source(
                    self._scope_id,
                    CreateSourceRequest(content={"task_id": task_id, **content}),
                )
                return json.loads(resp.model_dump_json())

        return self._run(_do())

    def load_state(self, task_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        """Read saved state back deterministically (newest last).

        Works after a module restart as long as the PowerContext server data
        directory persists; used to resume tasks and to inject prior progress
        into newly built contexts.
        """
        self.ensure_scope()

        async def _do() -> List[dict]:
            async with self._client_ctx() as client:
                page = await client.list_atomic_memories(
                    ListAtomicMemoryRequest(
                        scope_id=self._scope_id, kind=MEMORY_KIND, limit=limit
                    )
                )
                out = []
                for record in page.items:
                    try:
                        parsed = json.loads(record.text)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {"raw": record.text}
                    if task_id is None or parsed.get("task_id") == task_id:
                        parsed["_state"] = str(getattr(record, "state", ""))
                        parsed["_ref"] = getattr(record.artifact, "artifact_id", "")
                        out.append(parsed)
                return out

        return self._run(_do())
