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

"""Capture one offline CSV row per Source, addressed by document and primary key.

A row is identified by what it *is* — its document and its primary-key value — never by its
position, so re-sorting or editing a non-key column keeps the same identity. Values are kept
as the exact strings that appeared in the file: no trimming, no numeric or boolean
coercion, and column order is preserved everywhere.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_DEFINITION_VERSION = "1"

NonEmptyText = Annotated[str, Field(min_length=1)]


class CSVDocumentInput(BaseModel):
    """One complete CSV document supplied as offline bytes."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One parsed data record, tied to its document and primary key."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """The captured Source for one CSV row, materialized from its parsed record."""

    source_id: NonEmptyText
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> CSVRowSource:
        if self.source_id != self.name:
            raise ValueError("source_id must equal the Source name")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same width")  # noqa: TRY003
        return self

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _require_document_id(value)
        return value


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one document into rows, or fail without returning any of them.

    Validation is all-or-nothing: the whole file is parsed and checked before the tuple is
    produced, so a caller never sees a prefix of a document that turned out to be invalid.
    """

    document_id = _require_document_id(value.document_id)
    primary_key = _require_primary_key(value.primary_key)
    records = _read_records(value.content)

    if not records:
        raise ValueError("CSV document must contain a header record")  # noqa: TRY003
    columns = tuple(records[0])
    _require_header(columns)
    if primary_key not in columns:
        raise ValueError(f"primary key {primary_key!r} is not a column of this document")  # noqa: TRY003
    key_index = columns.index(primary_key)
    width = len(columns)

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError(
                f"record width {len(record)} does not match the header width {width}",  # noqa: TRY003
            )
        key_value = record[key_index]
        if not key_value.strip():
            raise ValueError("primary key values must not be empty or blank")  # noqa: TRY003
        if key_value in seen:
            raise ValueError(f"duplicate primary key value {key_value!r} in this document")  # noqa: TRY003
        seen.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe identity of one row Source."""

    document = _require_document_id(document_id)
    identity = f"csv:{quote(document, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"row Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters",  # noqa: TRY003
        )
    return identity


def _read_records(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed CSV content: {error}") from error


def _require_header(columns: tuple[str, ...]) -> None:
    if not columns:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    seen: set[str] = set()
    for column in columns:
        if not column.strip():
            raise ValueError("CSV header fields must not be empty or blank")  # noqa: TRY003
        if column in seen:
            raise ValueError(f"duplicate CSV header field {column!r}")  # noqa: TRY003
        seen.add(column)


def _require_document_id(value: str) -> str:
    if not value or not value.strip() or value != value.strip():
        raise ValueError("document_id must be non-empty and must not contain surrounding whitespace")  # noqa: TRY003
    return value


def _require_primary_key(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("primary_key must be non-empty")  # noqa: TRY003
    return value


class CSVRowSourceAdapter:
    """Resolve a parsed record into its Source, and read it back unchanged."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one row as the JSON object the retrieval pipeline indexes."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_object(source.columns, source.values)
        metadata: dict[str, JsonValue] = {
            "document_id": source.document_id,
            "primary_key": source.primary_key,
            "primary_key_value": source.primary_key_value,
            "columns": list(source.columns),
            "row": row,
        }
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata=metadata,
        )


def _row_object(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, JsonValue]:
    if len(columns) != len(values):
        raise ValueError("columns and values must have the same width")  # noqa: TRY003
    return {column: value for column, value in zip(columns, values, strict=True)}


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_DEFINITION_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)
