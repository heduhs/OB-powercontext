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

"""One captured Source per physical page of a financial-report PDF.

Every physical page becomes its own Source — including pages whose text cannot be
extracted — so a citation always resolves to the exact page a reader would open.
Page identity is ``(document_id, document_sha256, page_number)``: re-issuing the
same document keeps the same identity, and a revised file produces a fresh one
while old citations keep pointing at the revision they captured.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Annotated, Any

from pydantic import BaseModel, Field, JsonValue, field_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_DEFINITION_VERSION = "1"

NonEmptyText = Annotated[str, Field(min_length=1)]

_WORD = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset(
    """
    a about above after again against all also an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers herself him himself his how i if in into is it its itself
    just me more most my myself no nor not of off on once only or other our ours ourselves out over
    own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who
    whom why will with would you your yours yourself yourselves company report fiscal year annual
    """.split()
)


class PDFDocumentInput(BaseModel):
    """One complete original PDF supplied as offline bytes."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)


class PDFPageInput(BaseModel):
    """One parsed physical page, tied to its document and content digest."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """The captured Source for one physical PDF page."""

    source_id: NonEmptyText
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one PDF, or fail without returning a prefix."""

    if not isinstance(value, PDFDocumentInput):
        raise ValueError("value must be a PDFDocumentInput")  # noqa: TRY003
    document_id = _require_document_id(value.document_id)
    raw = bytes(value.content)
    if not raw:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    digest = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")  # noqa: TRY003
        pages: list[PDFPageInput] = []
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=digest,
                    page_number=index + 1,
                    text=text,
                )
            )
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - every parser failure is an invalid document
        raise ValueError(f"unreadable PDF: {error}") from error
    if not pages:
        raise ValueError("PDF contains no pages")  # noqa: TRY003
    return tuple(pages)


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable, collision-free identity of one page Source."""

    document = _require_document_id(document_id)
    identity = f"pdf:{document}:{document_sha256}:{page_number}"
    if len(identity) <= MAX_SOURCE_ID_LENGTH:
        return identity
    shortened = hashlib.sha256(document.encode("utf-8")).hexdigest()
    return f"pdf:{shortened}:{document_sha256}:{page_number}"


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[Any, ...]:
    """Return up to ``limit`` exact citations for distinct evidence pages."""

    from powercontext.builtin.artifacts.memory import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    citations: list[Any] = []
    seen: set[str] = set()
    window = max(limit * 4, 40)
    for query in _candidate_queries(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=window))
        for hit in page.hits:
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            fresh = [reference for reference in entry.sources if reference.source_id not in seen]
            if not fresh:
                continue
            for reference in fresh:
                seen.add(reference.source_id)
            citations.append(citation)
            if len(citations) >= limit:
                return tuple(citations)
    return tuple(citations)


def _candidate_queries(question: str) -> tuple[str, ...]:
    """Broaden recall without inventing evidence: the question, then its content words."""
    queries: list[str] = [question.strip()]
    terms = [term for term in _WORD.findall(question.lower()) if term not in _STOPWORDS]
    deduped = tuple(dict.fromkeys(terms))
    if len(deduped) > 1:
        queries.append(" ".join(deduped))
    numbered = tuple(term for term in deduped if term.isdigit())
    if numbered:
        queries.append(" ".join(numbered))
    return tuple(dict.fromkeys(query for query in queries if query))


def _require_document_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("document_id must be non-empty and must not contain surrounding whitespace")  # noqa: TRY003
    return value


class PDFPageSourceAdapter:
    """Resolve a parsed page into its Source, and read it back unchanged."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as ``DOCUMENT_ID\\nPAGE_TEXT`` plus locating metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "document_id": source.document_id,
            "document_sha256": source.document_sha256,
            "page_number": source.page_number,
        }
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata=metadata,
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_DEFINITION_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)
