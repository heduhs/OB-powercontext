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

"""Capture every visible event of one Pi session branch as its own Source.

A Pi session is a tree: each record names its parent, so one leaf determines exactly one
history. Only that path is captured, and only the events that carry visible text — thinking
blocks, images and control records keep their place in the tree but produce no evidence.

The captured record is the whole original JSON object, never a rendering of it, so a citation
leads back to exactly what the session contained. ``PiSessionConnector`` keeps its progress in
the Connector checkpoint and therefore never imports the same event twice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_CONNECTOR_NAME = "pi-session-history"
PI_CONNECTOR_VERSION = "1"
PI_SESSION_VERSION = 3

_CHECKPOINT_KEY = "events"

NonEmptyText = Annotated[str, Field(min_length=1)]


class PiSessionInput(BaseModel):
    """One Pi JSONL session file and the branch to follow."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, with the record it came from."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """The captured Source for one visible Pi event."""

    source_id: NonEmptyText
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        if self.source_id != self.name:
            raise ValueError("source_id must equal the Source name")  # noqa: TRY003
        return self


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events on the branch ending at ``leaf_id``, root first.

    Nothing is returned unless the entire archive parses: encoding, JSON, duplicate ids,
    dangling parents and a missing leaf all fail before any event is produced.
    """

    records = _read_records(value.content)
    if not records:
        raise ValueError("Pi session archive must start with a session record")  # noqa: TRY003
    session_id = _require_session_header(records[0])

    parents: dict[str, str | None] = {}
    order: list[str] = []
    by_id: dict[str, dict[str, JsonValue]] = {}
    for record in records[1:]:
        event_id = _require_event_id(record)
        if event_id in parents:
            raise ValueError(f"duplicate Pi event id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"Pi event {event_id!r} has a non-string parentId")  # noqa: TRY003
        if parent_id is not None and parent_id not in parents:
            raise ValueError(f"Pi event {event_id!r} points at unknown parent {parent_id!r}")  # noqa: TRY003
        parents[event_id] = parent_id
        by_id[event_id] = record
        order.append(event_id)

    leaf = value.leaf_id if value.leaf_id is not None else (order[-1] if order else None)
    if leaf is None:
        raise ValueError("Pi session archive has no branch to follow")  # noqa: TRY003
    if leaf not in parents:
        raise ValueError(f"Pi session leaf {leaf!r} does not exist")  # noqa: TRY003

    chain: list[str] = []
    cursor: str | None = leaf
    while cursor is not None:
        chain.append(cursor)
        cursor = parents[cursor]
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        record = by_id[event_id]
        rendered = _render_visible(record)
        if rendered is None:
            continue
        role, body = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parents[event_id],
                event_type=str(record.get("type") or ""),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the delimiter-safe identity of one captured event."""

    if not session_id or not session_id.strip():
        raise ValueError("session_id must be non-empty")  # noqa: TRY003
    if not event_id or not event_id.strip():
        raise ValueError("event_id must be non-empty")  # noqa: TRY003
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"event Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _read_records(content: bytes) -> list[dict[str, JsonValue]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error

    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Pi session line is not valid JSON: {error}") from error
        if not isinstance(record, dict):
            raise ValueError("every Pi session line must be a JSON object")  # noqa: TRY003
        records.append(record)
    return records


def _require_session_header(record: dict[str, JsonValue]) -> str:
    if record.get("type") != "session":
        raise ValueError("Pi session archive must start with a session record")  # noqa: TRY003
    if record.get("version") != PI_SESSION_VERSION:
        raise ValueError(f"Pi session version must be {PI_SESSION_VERSION}")  # noqa: TRY003
    session_id = record.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session record must carry a non-empty id")  # noqa: TRY003
    return session_id


def _require_event_id(record: dict[str, JsonValue]) -> str:
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("every Pi event record must carry a non-empty id")  # noqa: TRY003
    return event_id


def _render_visible(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Return ``(role, body)`` for a record with visible text, otherwise ``None``."""

    event_type = record.get("type")
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
        return (str(event_type), body) if body.strip() else None
    if event_type == "custom_message":
        body = _render_content(record.get("content"))
        return ("custom", body) if body.strip() else None
    if event_type != "message":
        return None

    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None
    if role == "toolResult":
        name = message.get("toolName")
        call_id = message.get("toolCallId")
        tool = name if isinstance(name, str) and name else "unknown"
        call = call_id if isinstance(call_id, str) else ""
        text = _render_content(message.get("content"))
        return ("toolResult", f"[tool_result {tool} {call}] {text}")
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        text = command if isinstance(command, str) else ""
        tail = output if isinstance(output, str) else ""
        return ("bashExecution", f"$ {text}\n{tail}")
    body = _render_content(message.get("content"))
    return (role, body) if body.strip() else None


def _render_content(content: JsonValue) -> str:
    """Render visible content: strings verbatim, text and tool-call blocks by line."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            tool = name if isinstance(name, str) and name else "unknown"
            call = call_id if isinstance(call_id, str) else ""
            parts.append(f"[tool_call {tool} {call}] {_compact_json(arguments)}")
    return "\n".join(parts)


def _compact_json(value: JsonValue) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PiEventAdapter:
    """Resolve a parsed event into its Source, and read the parser input back."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one event's full text while keeping its place in the session graph."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "session_id": source.session_id,
            "event_id": source.event_id,
            "parent_id": source.parent_id,
            "event_type": source.event_type,
            "role": source.role,
        }
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata=metadata,
        )


class PiSessionConnector:
    """Re-read one session file and submit only the branch events not yet captured."""

    name = PI_CONNECTOR_NAME
    version = PI_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        captured = _captured_digests(session.checkpoint)

        current: dict[str, str] = {}
        for event in events:
            digest = _record_digest(event.record)
            stored = captured.get(event.event_id)
            if stored is not None and stored != digest:
                raise ValueError(
                    f"Pi event {event.event_id!r} was already captured with different content",  # noqa: TRY003
                )
            current[event.event_id] = digest

        for event in events:
            if event.event_id in captured:
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)

        merged = {**captured, **current}
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={_CHECKPOINT_KEY: merged},
        )


def _captured_digests(checkpoint: JsonValue | None) -> dict[str, str]:
    if not isinstance(checkpoint, dict):
        return {}
    events = checkpoint.get(_CHECKPOINT_KEY)
    if not isinstance(events, dict):
        return {}
    return {key: value for key, value in events.items() if isinstance(key, str) and isinstance(value, str)}


def _record_digest(record: dict[str, JsonValue]) -> str:
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


PI_EVENT_SOURCE_ADAPTER = PiEventAdapter()
PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version="1",
    projections=(PiEventTextEvidenceProjection(),),
)
