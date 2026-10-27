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

"""Evidence dependency graphs with source-driven availability.

Callers register captured Sources, Memory entries and Topic Memory revisions,
declare which parents support each derived node, and then publish lifecycle
events.  Availability propagates through the DAG with ``all``/``any`` policies,
Memory entries drop out of native search (and come back on restore), and Topic
Memory revisions stop being surfaced by native Topic recall.

Every registration and event is persisted, so a reopened Runtime keeps both the
graph and the resulting availability.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = [
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "ScopedSourceLifecycle",
    "SourceLifecycleRegistry",
]

_NODE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pc_source_lifecycle_nodes ("
    "scope_id VARCHAR(128) NOT NULL,"
    "node_id VARCHAR(128) NOT NULL,"
    "family VARCHAR(16) NOT NULL,"
    "parents TEXT NOT NULL,"
    "policy VARCHAR(8) NOT NULL,"
    "revision INTEGER NOT NULL,"
    "reference TEXT NOT NULL,"
    "available INTEGER NOT NULL,"
    "synced INTEGER NOT NULL,"
    "PRIMARY KEY (scope_id, node_id)"
    ")"
)
_EVENT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pc_source_lifecycle_events ("
    "scope_id VARCHAR(128) NOT NULL,"
    "node_id VARCHAR(128) NOT NULL,"
    "sequence INTEGER NOT NULL,"
    "available INTEGER NOT NULL,"
    "reason TEXT NOT NULL,"
    "PRIMARY KEY (scope_id, node_id, sequence)"
    ")"
)


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle registration or event violates the contract."""


class SourceLifecycleEvent(BaseModel):
    """One availability transition for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: StrictStr
    reason: StrictStr
    revision: int = Field(strict=True, ge=1)
    sequence: int = Field(strict=True, ge=1)
    available: bool = Field(strict=True)

    @field_validator("node_id", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@dataclass
class _Node:
    node_id: str
    family: Literal["source", "memory", "topic"]
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int = 0
    reference: str = "{}"
    available: bool = True
    synced: bool = True


class SourceLifecycleRegistry:
    """Runtime-wide factory for per-scope lifecycle services."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime
        self._services: dict[str, ScopedSourceLifecycle] = {}

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycle:
        scope = validate_scope_id(scope_id)
        service = self._services.get(scope)
        if service is None:
            service = ScopedSourceLifecycle(self._runtime, scope)
            self._services[scope] = service
        return service

    async def excluded_topics(self, scope_id: str) -> frozenset[str]:
        return await self.for_scope(scope_id).excluded_topics()


class ScopedSourceLifecycle:
    """Registrations, events and availability for exactly one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id
        self._lock = asyncio.Lock()
        self._nodes: dict[str, _Node] = {}
        self._loaded = False
        self._schema_ready = False
        self._excluded: frozenset[str] = frozenset()

    # -- registrations ---------------------------------------------------

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int = 1) -> None:
        node_key = _node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type, source_ref.source_id
            )
        except Exception as error:  # noqa: BLE001 - an unreadable Source fails the contract
            raise SourceLifecycleError(f"source is not readable: {source_ref.source_id}") from error
        async with self._lock:
            await self._ensure_loaded()
            node = _Node(
                node_id=node_key,
                family="source",
                revision=revision,
                reference=source_ref.model_dump_json(),
            )
            if await self._register_locked(node):
                await self._persist_locked()
            await self._sync_topics()

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] = "all",
    ) -> None:
        node_key = _node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(_memory_request(citation))
        except Exception as error:  # noqa: BLE001 - an unreadable citation fails the contract
            raise SourceLifecycleError(f"memory entry is not readable: {citation.entry_id}") from error
        async with self._lock:
            await self._ensure_loaded()
            resolved = self._validate_parents(node_key, parents, policy)
            node = _Node(
                node_id=node_key,
                family="memory",
                parents=resolved,
                policy=policy,
                reference=citation.model_dump_json(),
                available=self._evaluate(resolved, policy),
                synced=True,
            )
            created = await self._register_locked(node)
            if created and not node.available:
                await self._apply_memory_state(node, available=False, reason="registered without support")
            if created:
                await self._persist_locked()
            await self._sync_topics()

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] = "all",
    ) -> None:
        node_key = _node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("artifact_ref must be an ArtifactRef")
        if artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("artifact_ref must be a Topic Memory revision")
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except Exception as error:  # noqa: BLE001 - an unreadable revision fails the contract
            raise SourceLifecycleError(f"topic revision is not readable: {artifact_ref.artifact_id}") from error
        async with self._lock:
            await self._ensure_loaded()
            resolved = self._validate_parents(node_key, parents, policy)
            node = _Node(
                node_id=node_key,
                family="topic",
                parents=resolved,
                policy=policy,
                reference=artifact_ref.model_dump_json(),
                available=self._evaluate(resolved, policy),
            )
            if await self._register_locked(node):
                await self._persist_locked()
            await self._sync_topics()

    # -- events ----------------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")
        async with self._lock:
            await self._ensure_loaded()
            node = self._nodes.get(event.node_id)
            if node is None or node.family != "source":
                raise SourceLifecycleError(f"unknown source: {event.node_id}")
            if node.revision != event.revision:
                raise SourceLifecycleError("revision mismatch")
            last, last_available = await self._last_event(event.node_id)
            if event.sequence < last:
                raise SourceLifecycleError("sequence must increase")
            if event.sequence == last:
                if last_available != event.available:
                    raise SourceLifecycleError("sequence reused with different content")
            else:
                await self._record_event(event)
            node.available = event.available
            active = self._propagate()
            await self._sync_native(event.reason)
            await self._persist_locked()
            return sorted(active)

    async def resolve(self, node_id: str, /, *, purpose: Literal["generation", "audit"]) -> Any:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError(f"unknown purpose: {purpose}")
        async with self._lock:
            await self._ensure_loaded()
            node = self._nodes.get(node_id)
            if node is None:
                raise SourceLifecycleError(f"unknown node: {node_id}")
            if purpose == "generation" and not node.available:
                raise SourceLifecycleError(f"node is not available: {node_id}")
            return _decode_reference(node)

    async def excluded_topics(self) -> frozenset[str]:
        async with self._lock:
            await self._ensure_loaded()
            return self._excluded

    # -- internals -------------------------------------------------------

    def _validate_parents(
        self,
        node_id: str,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> tuple[str, ...]:
        if policy not in ("all", "any"):
            raise SourceLifecycleError("policy must be all or any")
        if not parents:
            raise SourceLifecycleError("derived nodes require at least one parent")
        resolved: list[str] = []
        for parent in parents:
            if not isinstance(parent, str) or not parent.strip():
                raise SourceLifecycleError("parent must be a node id")
            if parent == node_id:
                raise SourceLifecycleError("a node cannot depend on itself")
            if parent in resolved:
                raise SourceLifecycleError("duplicate parent")
            if parent not in self._nodes:
                raise SourceLifecycleError(f"unknown parent: {parent}")
            resolved.append(parent)
        return tuple(resolved)

    async def _register_locked(self, node: _Node) -> bool:
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            if (
                existing.family != node.family
                or existing.parents != node.parents
                or existing.policy != node.policy
                or existing.reference != node.reference
                or existing.revision != node.revision
            ):
                raise SourceLifecycleError(f"node already registered with a different binding: {node.node_id}")
            return False
        self._nodes[node.node_id] = node
        self._propagate()
        return True

    def _evaluate(self, parents: tuple[str, ...], policy: str) -> bool:
        support = [self._nodes[parent].available for parent in parents]
        return all(support) if policy == "all" else any(support)

    def _propagate(self) -> set[str]:
        active: set[str] = set()
        for node in _topological(self._nodes):
            if node.family == "source":
                if node.available:
                    active.add(node.node_id)
                continue
            node.available = self._evaluate(node.parents, node.policy)
            if node.available:
                active.add(node.node_id)
        return active

    async def _sync_native(self, reason: str) -> None:
        for node in tuple(self._nodes.values()):
            if node.family != "memory" or node.synced == node.available:
                continue
            await self._apply_memory_state(node, available=node.available, reason=reason)
        await self._sync_topics()

    async def _sync_topics(self) -> None:
        self._excluded = frozenset(
            _decode_reference(node).artifact_id
            for node in self._nodes.values()
            if node.family == "topic" and not node.available
        )

    async def _apply_memory_state(self, node: _Node, *, available: bool, reason: str) -> None:
        from powercontext.builtin.runtime.application import _head_or_none

        citation = MemoryCitation.model_validate_json(node.reference)
        page = await self._runtime.memory.for_scope(self.scope_id).list(include_inactive=True)
        record = next((item for item in page.entries if item.entry.entry_id == citation.entry_id), None)
        if record is None:
            raise SourceLifecycleError(f"memory entry is not in the current head: {citation.entry_id}")
        wanted = "active" if available else "inactive"
        if record.state != wanted:
            async with self._runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                memory = await _head_or_none(service, context.artifacts.memory_artifact_id)
                if memory is None:
                    raise SourceLifecycleError("memory head is missing")
                if available:
                    await service.reactivate(memory, entries=(record.entry,), reason=reason)
                else:
                    await service.forget(memory, entries=(record.entry,), reason=reason)
        node.synced = available

    # -- persistence -----------------------------------------------------

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._runtime.database.transaction() as connection:
            if not self._schema_ready:
                await connection.execute(text(_NODE_SCHEMA))
                await connection.execute(text(_EVENT_SCHEMA))
                self._schema_ready = True
            rows = (
                await connection.execute(
                    text(
                        "SELECT node_id, family, parents, policy, revision, reference, available, synced "
                        "FROM pc_source_lifecycle_nodes WHERE scope_id = :scope"
                    ),
                    {"scope": self.scope_id},
                )
            ).mappings()
            loaded: dict[str, _Node] = {}
            for row in rows:
                loaded[row["node_id"]] = _Node(
                    node_id=row["node_id"],
                    family=row["family"],
                    parents=tuple(json.loads(row["parents"])),
                    policy=row["policy"],
                    revision=int(row["revision"]),
                    reference=row["reference"],
                    available=bool(row["available"]),
                    synced=bool(row["synced"]),
                )
        self._nodes = loaded
        self._loaded = True
        self._propagate()
        await self._sync_topics()

    async def _persist_locked(self) -> None:
        async with self._runtime.database.transaction() as connection:
            await connection.execute(
                text("DELETE FROM pc_source_lifecycle_nodes WHERE scope_id = :scope"),
                {"scope": self.scope_id},
            )
            for node in self._nodes.values():
                await connection.execute(
                    text(
                        "INSERT INTO pc_source_lifecycle_nodes "
                        "(scope_id, node_id, family, parents, policy, revision, reference, available, synced) "
                        "VALUES (:scope, :node_id, :family, :parents, :policy, :revision, :reference, :available, :synced)"
                    ),
                    {
                        "scope": self.scope_id,
                        "node_id": node.node_id,
                        "family": node.family,
                        "parents": json.dumps(list(node.parents)),
                        "policy": node.policy,
                        "revision": node.revision,
                        "reference": node.reference,
                        "available": int(node.available),
                        "synced": int(node.synced),
                    },
                )

    async def _last_event(self, node_id: str) -> tuple[int, bool]:
        async with self._runtime.database.transaction() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT sequence, available FROM pc_source_lifecycle_events "
                        "WHERE scope_id = :scope AND node_id = :node ORDER BY sequence DESC LIMIT 1"
                    ),
                    {"scope": self.scope_id, "node": node_id},
                )
            ).first()
        if row is None:
            return 0, True
        return int(row[0]), bool(row[1])

    async def _record_event(self, event: SourceLifecycleEvent) -> None:
        async with self._runtime.database.transaction() as connection:
            await connection.execute(
                text(
                    "INSERT INTO pc_source_lifecycle_events "
                    "(scope_id, node_id, sequence, available, reason) "
                    "VALUES (:scope, :node, :sequence, :available, :reason)"
                ),
                {
                    "scope": self.scope_id,
                    "node": event.node_id,
                    "sequence": event.sequence,
                    "available": int(event.available),
                    "reason": event.reason,
                },
            )


def _topological(nodes: dict[str, _Node]) -> list[_Node]:
    ordered: list[_Node] = []
    visit = {node_id: 0 for node_id in nodes}

    def walk(node: _Node) -> None:
        state = visit[node.node_id]
        if state == 2:
            return
        if state == 1:
            raise SourceLifecycleError("dependency cycle")
        visit[node.node_id] = 1
        for parent in node.parents:
            parent_node = nodes.get(parent)
            if parent_node is None:
                raise SourceLifecycleError(f"unknown parent: {parent}")
            walk(parent_node)
        visit[node.node_id] = 2
        ordered.append(node)

    for node in nodes.values():
        walk(node)
    return ordered


def _node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node id must be a non-blank string")
    return value


def _memory_request(citation: MemoryCitation) -> Any:
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest

    return GetMemoryEntryRequest(citation=citation)


def _decode_reference(node: _Node) -> Any:
    if node.family == "source":
        return SourceRef.model_validate_json(node.reference)
    if node.family == "memory":
        return MemoryCitation.model_validate_json(node.reference)
    return ArtifactRef.model_validate_json(node.reference)
