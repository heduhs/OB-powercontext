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

"""Chunked context packs and prefix-reusing wire deltas.

A context pack is a deterministic chunking of the exact bytes produced by
``prepare``.  A delta names how many leading chunks of a previously delivered
pack the receiver may keep and carries only the remaining suffix, so a client
holding the previous pack can rebuild the current one without a second full
transfer.

Both wire objects are canonical JSON: UTF-8, sorted keys, ``","``/``":"``
separators and no extra whitespace.  Chunks travel as standard Base64 strings.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = [
    "CONTEXT_PACK_CHUNK_BYTES",
    "CONTEXT_PACK_SCHEMA",
    "CONTEXT_PACK_DELTA_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
CONTEXT_PACK_CHUNK_BYTES = 512
_DEFAULT_CHUNK_BYTES = CONTEXT_PACK_CHUNK_BYTES


class ContextPackError(ValueError):
    """Raised when a pack or delta violates the wire or chunking contract."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: Iterable[bytes]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(values: Any, *, field: str) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError(f"{field} must be a list")
    decoded: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError(f"{field} entries must be base64 strings")
        try:
            raw = base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ContextPackError(f"{field} entries must be base64 strings") from error
        decoded.append(raw)
    return tuple(decoded)


def _digest_items(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": CONTEXT_PACK_SCHEMA, "items": _encode_items(items)}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _utf8_cut(raw: bytes, limit: int) -> int:
    """Largest offset ``<= limit`` that does not split a code point."""
    if limit >= len(raw):
        return len(raw)
    cut = limit
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    if cut == 0:
        raise ContextPackError("chunk-bytes-too-small")
    return cut


def _split_lines(raw: bytes) -> list[bytes]:
    return raw.splitlines(keepends=True) or [raw]


def _chunk_content(raw: bytes, limit: int) -> tuple[bytes, ...]:
    """Pack bytes into ``limit``-bounded UTF-8 chunks, preferring line breaks."""
    if not raw:
        return ()
    chunks: list[bytes] = []
    buffer = b""
    for line in _split_lines(raw):
        if buffer and len(buffer) + len(line) <= limit:
            buffer += line
            continue
        if buffer:
            chunks.append(buffer)
            buffer = b""
        if len(line) <= limit:
            buffer = line
            continue
        remaining = line
        while len(remaining) > limit:
            cut = _utf8_cut(remaining, limit)
            chunks.append(remaining[:cut])
            remaining = remaining[cut:]
        buffer = remaining
    if buffer:
        chunks.append(buffer)
    return tuple(chunks)


@dataclass(frozen=True)
class ContextPack:
    """A complete, self-describing chunking of one prepared context."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(not isinstance(item, bytes) for item in self.items):
            raise ContextPackError("items must be a tuple of bytes")
        if not all(isinstance(chunk, str) for chunk in (self.digest,)):
            raise ContextPackError("digest must be a string")
        if type(self.byte_count) is not int:
            raise ContextPackError("byte_count must be an integer")

    @classmethod
    def build(cls, items: Iterable[bytes]) -> "ContextPack":
        try:
            materialized = tuple(items)
        except TypeError as error:
            raise ContextPackError("items must be iterable") from error
        if any(not isinstance(item, bytes) for item in materialized):
            raise ContextPackError("items must be bytes")
        digest = _digest_items(materialized)
        byte_count = 0
        for _ in range(8):
            payload = {
                "schema": CONTEXT_PACK_SCHEMA,
                "items": _encode_items(materialized),
                "digest": digest,
                "byte_count": byte_count,
            }
            size = len(_canonical(payload))
            if size == byte_count:
                return cls(items=materialized, digest=digest, byte_count=size)
            byte_count = size
        raise ContextPackError("byte_count did not converge")

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = _DEFAULT_CHUNK_BYTES) -> "ContextPack":
        if not isinstance(content, str):
            raise ContextPackError("content must be str")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        return cls.build(_chunk_content(content.encode("utf-8"), chunk_bytes))

    @property
    def text(self) -> str:
        return b"".join(self.items).decode("utf-8")

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": CONTEXT_PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview | str) -> "ContextPack":
        payload, _raw = _load_canonical(data, _PACK_FIELDS, "pack")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported schema")
        items = _decode_items(payload["items"], field="items")
        digest = payload["digest"]
        if not isinstance(digest, str) or digest != _digest_items(items):
            raise ContextPackError("digest mismatch")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int:
            raise ContextPackError("byte_count must be an integer")
        pack = cls(items=items, digest=digest, byte_count=byte_count)
        if byte_count != len(pack.to_bytes()):
            raise ContextPackError("byte_count mismatch")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """The suffix of a target pack, plus how much of the base pack to keep."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.base_digest, str) or not isinstance(self.target_digest, str):
            raise ContextPackError("digests must be strings")
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        if not isinstance(self.appended, tuple) or any(not isinstance(item, bytes) for item in self.appended):
            raise ContextPackError("appended must be a tuple of bytes")

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: "ContextPack", target: "ContextPack") -> "ContextPackDelta":
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("base and target must be ContextPack")
        prefix = 0
        for left, right in zip(base.items, target.items):
            if left != right:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": CONTEXT_PACK_DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview | str) -> "ContextPackDelta":
        payload, _raw = _load_canonical(data, _DELTA_FIELDS, "delta")
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported schema")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("digests must be strings")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=_decode_items(payload["appended"], field="appended"),
        )


def _load_canonical(data: Any, fields: frozenset[str], kind: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:
        raise ContextPackError(f"{kind} payload must be bytes or str")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(f"{kind} payload is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError(f"{kind} payload must be an object")
    if set(payload) != fields:
        raise ContextPackError(f"{kind} payload has unexpected fields")
    return payload, raw


def apply_context_pack_delta(base: "ContextPack", delta: "ContextPackDelta") -> "ContextPack":
    """Rebuild the target pack from ``base`` and ``delta``."""
    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("base and delta have unexpected types")
    if base.digest != delta.base_digest:
        raise ContextPackError("base digest mismatch")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix_items exceeds base chunk count")
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("target digest mismatch")
    return rebuilt
