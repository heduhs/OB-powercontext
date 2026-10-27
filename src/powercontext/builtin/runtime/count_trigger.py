# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Start Source processing when enough new journal identities have accumulated.

Time-based scheduling answers "when did we last run".  Busy scopes also want "how
much has arrived since we last succeeded", so this module counts persisted journal
identities between the family's last successful cursor and the journal head, and
runs exactly the existing processing call once that gap reaches the configured
threshold.

Counting is per scope and per family.  A repeated capture of an existing identity
does not insert a journal row, so it cannot inflate the count.  Memory keeps using
its own durable cursor (a failed window leaves it untouched, so the work is retried
after a restart); Topic Memory records the head it asked for only after its wave has
settled, so an interrupted wave is retried too.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.tables import SOURCES_TABLE

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = ["MEMORY_FAMILY", "TOPIC_FAMILY", "SourceCountTrigger"]

logger = logging.getLogger(__name__)

MEMORY_FAMILY = "memory"
TOPIC_FAMILY = "topic-memory"

_MAX_ROUNDS = 1000
_TOPIC_SETTLE_SECONDS = 30.0
_POLL_SECONDS = 0.05
_ACTIVE_SWEEP: ContextVar[str | None] = ContextVar("powercontext_source_count_sweep", default=None)
_CHECKPOINT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pc_source_count_checkpoints ("
    "scope_id VARCHAR(128) NOT NULL,"
    "family VARCHAR(32) NOT NULL,"
    "position INTEGER NOT NULL,"
    "PRIMARY KEY (scope_id, family)"
    ")"
)


class SourceCountTrigger:
    """Start one processing call per threshold crossing, per scope and family."""

    def __init__(
        self,
        runtime: BuiltinRuntime,
        *,
        memory_threshold: int | None = None,
        topic_threshold: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._thresholds: dict[str, int] = {}
        if memory_threshold is not None:
            self._thresholds[MEMORY_FAMILY] = memory_threshold
        if topic_threshold is not None:
            self._thresholds[TOPIC_FAMILY] = topic_threshold
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._dirty: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._schema_ready = False
        self._closed = False

    @property
    def configured(self) -> bool:
        return bool(self._thresholds)

    # -- entry points ----------------------------------------------------

    def notify(self, scope_id: str) -> None:
        """Schedule one sweep for ``scope_id``; safe to call on every access.

        A notification that arrives while a sweep is still running is not lost:
        the active sweep loops once more after it finishes, so the newest
        journal head is always re-examined.
        """
        if not self._thresholds or self._closed:
            return
        if _ACTIVE_SWEEP.get() == scope_id:
            # The sweep itself reads through the public application, which
            # notifies on entry.  Those notifications are not new work.
            return
        self._dirty.add(scope_id)
        existing = self._tasks.get(scope_id)
        if existing is not None and not existing.done():
            return
        try:
            task = asyncio.ensure_future(self._guarded_sweep(scope_id))
        except RuntimeError:  # no running loop: nothing to schedule
            return
        self._tasks[scope_id] = task

    async def sweep(self, scope_id: str) -> None:
        """Run one threshold check for every configured family of this scope."""
        lock = self._locks.setdefault(scope_id, asyncio.Lock())
        async with lock:
            token = _ACTIVE_SWEEP.set(scope_id)
            try:
                for family in tuple(self._thresholds):
                    if self._closed:
                        return
                    await self._drain(scope_id, family)
            finally:
                _ACTIVE_SWEEP.reset(token)

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        self._tasks.clear()
        self._dirty.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best effort
                pass

    # -- internals -------------------------------------------------------

    async def _guarded_sweep(self, scope_id: str) -> None:
        try:
            while True:
                self._dirty.discard(scope_id)
                await self.sweep(scope_id)
                if self._closed or scope_id not in self._dirty:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - background work must not crash the caller
            log_safely(
                logger,
                logging.WARNING,
                "Source count admission failed",
                exc_info=error,
                extra={
                    "event": "processing.count_trigger.failed",
                    "outcome": "failure",
                    "unit": "processing",
                },
            )

    async def _drain(self, scope_id: str, family: str) -> None:
        threshold = self._thresholds[family]
        for _ in range(_MAX_ROUNDS):
            pending = await self._pending(scope_id, family)
            if pending < threshold:
                return
            if family == MEMORY_FAMILY:
                result = await self._runtime.memory.for_scope(scope_id).flush()
                if not result.processed:
                    return
                continue
            head = await self._journal_head(scope_id)
            result = await self._runtime.topic_memory.for_scope(scope_id).flush()
            if result.status != "accepted":
                return
            if await self._await_topic_settled(scope_id):
                await self._save_checkpoint(scope_id, family, head)
            return

    async def _pending(self, scope_id: str, family: str) -> int:
        head = await self._journal_head(scope_id)
        if family == MEMORY_FAMILY:
            cursor = await self._runtime.memory.for_scope(scope_id).cursor()
            return max(0, head - int(cursor.sequence))
        checkpoint = await self._load_checkpoint(scope_id, family)
        return max(0, head - checkpoint)

    async def _journal_head(self, scope_id: str) -> int:
        async with self._runtime.database.transaction() as connection:
            value = await connection.scalar(
                select(func.coalesce(func.max(SOURCES_TABLE.c.journal_position), 0)).where(
                    SOURCES_TABLE.c.scope_id == scope_id
                )
            )
        return int(value or 0)

    async def _topic_settled(self, scope_id: str) -> bool:
        async with self._runtime.database.transaction() as connection:
            pending = await ArtifactProcessingPendingRepository().load(
                connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            )
        if pending is None:
            return True
        return pending.handled_flush_generation >= pending.flush_generation

    async def _await_topic_settled(self, scope_id: str) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TOPIC_SETTLE_SECONDS
        while loop.time() < deadline:
            if self._closed:
                return False
            if await self._topic_settled(scope_id):
                return True
            await asyncio.sleep(_POLL_SECONDS)
        return False

    async def _load_checkpoint(self, scope_id: str, family: str) -> int:
        await self._ensure_schema()
        async with self._runtime.database.transaction() as connection:
            value = await connection.scalar(
                text(
                    "SELECT position FROM pc_source_count_checkpoints WHERE scope_id = :scope AND family = :family"
                ),
                {"scope": scope_id, "family": family},
            )
        return int(value or 0)

    async def _save_checkpoint(self, scope_id: str, family: str, position: int) -> None:
        await self._ensure_schema()
        async with self._runtime.database.transaction() as connection:
            await connection.execute(
                text("DELETE FROM pc_source_count_checkpoints WHERE scope_id = :scope AND family = :family"),
                {"scope": scope_id, "family": family},
            )
            await connection.execute(
                text(
                    "INSERT INTO pc_source_count_checkpoints (scope_id, family, position) "
                    "VALUES (:scope, :family, :position)"
                ),
                {"scope": scope_id, "family": family, "position": position},
            )

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._runtime.database.transaction() as connection:
            await connection.execute(text(_CHECKPOINT_SCHEMA))
        self._schema_ready = True
