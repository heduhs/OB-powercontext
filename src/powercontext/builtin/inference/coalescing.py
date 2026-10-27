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

"""Cross-call request coalescing and a bounded vector cache for embeddings.

Several callers in one event loop often submit the same texts at the same time
(memory writes for different scopes, a write racing a search).  The wrapper
below shares one underlying request per distinct text, remembers successful
vectors in an LRU cache, and keeps every caller's original order and usage
accounting intact — including when one caller is cancelled.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Iterable, Mapping
from typing import Any

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]

_Ticket = tuple[EmbeddingVector, int | None]


class CoalescingEmbeddingModel:
    """Share in-flight embedding work across calls and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not _positive_int(max_batch_size):
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Ticket]] = {}
        self._pending: dict[str, None] = {}
        self._window: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()
        self._usage: dict[int, InferenceUsage] = {}
        self._next_batch = 0
        self._lock = asyncio.Lock()

    @property
    def profile(self) -> Any:
        return self._model.profile

    @property
    def model(self) -> EmbeddingModel:
        return self._model

    async def embed(self, texts: Iterable[str]) -> EmbeddingResult:
        items = tuple(texts)
        if not items:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = tuple(dict.fromkeys(items))
        values: dict[str, EmbeddingVector] = {}
        tickets: dict[str, asyncio.Future[_Ticket]] = {}
        async with self._lock:
            for text in unique:
                cached = self._cache_lookup(text)
                if cached is not None:
                    values[text] = cached
                    continue
                ticket = self._inflight.get(text)
                if ticket is None:
                    ticket = asyncio.get_running_loop().create_future()
                    self._inflight[text] = ticket
                    self._pending[text] = None
                tickets[text] = ticket
            self._open_window()
        batch_ids: set[int] = set()
        for text, ticket in tickets.items():
            vector, batch_id = await asyncio.shield(ticket)
            values[text] = vector
            if batch_id is not None:
                batch_ids.add(batch_id)
        return EmbeddingResult(
            vectors=tuple(values[text] for text in items),
            usage=self._aggregate_usage(batch_ids),
        )

    # -- cache -----------------------------------------------------------

    def _cache_lookup(self, text: str) -> EmbeddingVector | None:
        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    # -- window and batches ----------------------------------------------

    def _open_window(self) -> None:
        if self._window is None and self._pending:
            self._window = asyncio.ensure_future(self._flush_window())

    async def _flush_window(self) -> None:
        try:
            if self._coalesce_seconds > 0:
                await asyncio.sleep(self._coalesce_seconds)
            async with self._lock:
                pending = tuple(self._pending)
                self._pending.clear()
                self._window = None
        except asyncio.CancelledError:
            async with self._lock:
                self._window = None
            raise
        for start in range(0, len(pending), self._max_batch_size):
            batch = pending[start : start + self._max_batch_size]
            worker = asyncio.ensure_future(self._run_batch(batch))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)

    async def _run_batch(self, batch: tuple[str, ...]) -> None:
        try:
            result = await self._model.embed(batch)
        except asyncio.CancelledError:
            self._fail(batch, None)
            raise
        except BaseException as error:  # noqa: BLE001 - forwarded to every waiter
            self._fail(batch, error)
            return
        vectors = tuple(tuple(vector) for vector in result.vectors)
        if len(vectors) != len(batch):
            self._fail(batch, ValueError("embedding vector count does not match batch size"))
            return
        self._next_batch += 1
        batch_id = self._next_batch
        self._usage[batch_id] = result.usage
        async with self._lock:
            for text, vector in zip(batch, vectors, strict=True):
                self._cache_store(text, vector)
                ticket = self._inflight.pop(text, None)
                if ticket is not None and not ticket.done():
                    ticket.set_result((vector, batch_id))

    def _fail(self, batch: tuple[str, ...], error: BaseException | None) -> None:
        for text in batch:
            ticket = self._inflight.pop(text, None)
            if ticket is None or ticket.done():
                continue
            if error is None:
                ticket.cancel()
            else:
                ticket.set_exception(error)
                ticket.exception()

    # -- usage -----------------------------------------------------------

    def _aggregate_usage(self, batch_ids: set[int]) -> InferenceUsage:
        if not batch_ids:
            return InferenceUsage(requests=0)
        usages = [self._usage[batch_id] for batch_id in sorted(batch_ids) if batch_id in self._usage]
        if not usages:
            return InferenceUsage(requests=0)
        return InferenceUsage(
            requests=len(usages),
            input_tokens=_total(getattr(usage, "input_tokens", None) for usage in usages),
            output_tokens=_total(getattr(usage, "output_tokens", None) for usage in usages),
        )


def _total(values: Iterable[int | None]) -> int | None:
    collected = list(values)
    if not collected or any(value is None for value in collected):
        return None
    return sum(value for value in collected if value is not None)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
