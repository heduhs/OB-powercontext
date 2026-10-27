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
import math
import re
from collections import Counter
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

# Keep thousands separators inside a number so ``13,000`` and ``13000`` agree.
_WORD = re.compile(r"[a-z]+|\d[\d,]*")
_DIGITS_ONLY = re.compile(r"^\d+$")
_PREFIX_LENGTH = 5
_PREFIX_WEIGHT = 0.45
_MAX_FTS_QUERIES = 4
_EXPANSION_WEIGHT = 0.55
_TITLE_TERMS = 80
_STATEMENT_BONUS = 0.45

# A financial question is usually answered by one of the primary statements, whose
# body is mostly figures; the heading is the only place that names the statement.
_STATEMENT_HINT = re.compile(
    r"consolidated (?:statement|balance|financial)"
    r"|statements? of (?:income|operations|earnings|cash flow|financial position|changes in equity)"
    r"|balance sheets?",
    re.IGNORECASE,
)
_FINANCIAL_TERMS = frozenset(
    """
    amount asset assets capex cash cost costs debt depreciation ebitda earnings equity expense expenses
    growth income interest liability liabilities loss margin operating price profit ratio revenue revenues
    sales tax value
    """.split()
)

# Ordinary financial-report vocabulary. A question names a concept the page may only
# express through its reporting synonym ("legal battles" -> litigation/proceedings),
# so each term also votes for the words the same concept is usually filed under. This
# is domain language, never document content, answers or evidence locations.
_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "acquisition": ("acquired", "merger", "business", "combination"),
    "amortization": ("depreciation", "intangible", "ebitda"),
    "battle": ("litigation", "legal", "proceedings", "claims", "disputes"),
    "battles": ("litigation", "legal", "proceedings", "claims", "disputes"),
    "borrowings": ("debt", "notes", "leverage"),
    "capex": ("capital", "expenditure", "property", "plant", "equipment"),
    "capital": ("capex", "expenditure"),
    "cash": ("liquidity", "flows", "equivalents"),
    "customer": ("customers", "concentration", "sales", "revenue", "orders", "backlog"),
    "customers": ("customer", "concentration", "sales", "revenue", "orders", "backlog"),
    "debt": ("borrowings", "leverage", "notes", "obligations"),
    "depreciation": ("amortization", "ebitda", "property"),
    "dividend": ("dividends", "payout", "shareholders", "per"),
    "dividends": ("dividend", "payout", "shareholders", "per"),
    "ebitda": ("operating", "income", "profit", "depreciation", "amortization"),
    "employees": ("headcount", "associates", "personnel"),
    "expenditure": ("capital", "capex", "spending"),
    "geographic": ("region", "regional", "area", "market", "segment"),
    "geographies": ("geographic", "region", "regional", "area", "market", "segment"),
    "geography": ("geographic", "region", "regional", "area", "market", "segment"),
    "guidance": ("outlook", "expectations", "forecast"),
    "headcount": ("employees", "associates"),
    "impairment": ("write", "charges", "restructuring"),
    "income": ("revenue", "earnings", "profit", "operating", "statements"),
    "interest": ("expense", "income", "debt", "rate"),
    "inventory": ("inventories", "stock", "supplies"),
    "lawsuit": ("litigation", "legal", "proceedings", "claims"),
    "legal": ("litigation", "proceedings", "claims", "regulatory", "disputes"),
    "liabilities": ("liability", "balance", "sheet", "payable", "accrued", "obligations"),
    "liability": ("balance", "sheet", "payable", "accrued", "obligations", "debt"),
    "liquidity": ("cash", "current", "quick", "ratio", "assets", "capital"),
    "litigation": ("legal", "proceedings", "claims", "disputes"),
    "margin": ("profit", "income", "revenue", "operating", "gross", "earnings"),
    "operating": ("income", "profit", "ebitda", "expenses", "operations"),
    "profit": ("income", "earnings", "margin", "gross"),
    "ratio": ("current", "quick", "liquidity", "assets", "liabilities", "margin"),
    "region": ("geographic", "area", "market", "segment"),
    "restructuring": ("reorganization", "charges", "productivity", "impairment"),
    "revenue": ("sales", "net", "income", "revenues"),
    "sales": ("revenue", "net", "products", "services"),
    "segment": ("region", "market", "business"),
    "shareholder": ("stockholders", "equity", "dividends"),
    "tax": ("income", "provision", "rate", "effective"),
}
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
    """Return up to ``limit`` exact citations for the most likely evidence pages.

    Every cached entry is scored against the question so a page cannot be dropped just
    because the lexical channel's admission floor rejected it; the lexical channel is
    still consulted and its ranking lifts pages that plain term statistics rank low.
    """

    from powercontext.builtin.runtime import SearchMemoryRequest

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    query_terms = _query_terms(question)
    if not query_terms:
        return ()
    records = tuple((await memory.list()).entries)
    if not records:
        return ()
    scores = _lexical_scores(query_terms, tuple(_document_terms(record.entry.text) for record in records))
    if _FINANCIAL_TERMS.intersection(query_terms):
        scores = [
            score + (_STATEMENT_BONUS if _STATEMENT_HINT.search(record.entry.text[:400]) else 0.0)
            for score, record in zip(scores, records)
        ]
    scores = await _boost_with_lexical_channel(memory, question, records, scores, SearchMemoryRequest)

    ranked = sorted(range(len(records)), key=lambda index: (-scores[index], index))
    matched = [index for index in ranked if scores[index] > 0.0]
    selected = (matched or ranked)[:limit]
    return tuple(records[index].citation for index in selected)


_FTS_WEIGHT = 0.25
_FTS_WINDOW = 200


async def _boost_with_lexical_channel(
    memory: Any,
    question: str,
    records: tuple[Any, ...],
    scores: list[float],
    search_request: Any,
) -> list[float]:
    """Lift entries the lexical channel already surfaces, keeping its order as a prior."""

    order: dict[str, int] = {}
    window = min(max(len(records), 32), _FTS_WINDOW)
    for query in _candidate_queries(question)[:_MAX_FTS_QUERIES]:
        try:
            page = await memory.search(search_request(query=query, mode="fts", limit=window))
        except Exception:  # noqa: BLE001 - the lexical channel is an optional prior here
            continue
        for hit in page.hits:
            order.setdefault(hit.entry_id, len(order))
    if not order:
        return scores
    total = len(order)
    index_by_entry = {record.entry.entry_id: index for index, record in enumerate(records)}
    boosted = list(scores)
    for entry_id, rank in order.items():
        index = index_by_entry.get(entry_id)
        if index is not None:
            boosted[index] += _FTS_WEIGHT * (1.0 - rank / total)
    return boosted


def _query_terms(question: str) -> tuple[str, ...]:
    terms = [term for term in _normalize_terms(question) if len(term) > 1 and term not in _STOPWORDS]
    return tuple(dict.fromkeys(terms))


def _normalize_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _WORD.findall(text.casefold()):
        terms.append(raw.replace(",", "") if raw[0].isdigit() else raw)
    return terms


def _document_terms(text: str) -> Counter[str]:
    head, separator, body = text.partition("\n")
    terms = _normalize_terms(body if separator else head)
    counter: Counter[str] = Counter(terms)
    # Report pages announce their subject in the opening lines ("Consolidated Statement
    # of Income"), which is exactly the phrase a question tends to reuse.
    for term in terms[:_TITLE_TERMS]:
        counter[term] += 1
    return counter


def _prefix_counter(counter: Counter[str]) -> Counter[str]:
    keys: Counter[str] = Counter()
    for term, count in counter.items():
        keys[term[:_PREFIX_LENGTH] if len(term) >= _PREFIX_LENGTH else term] += count
    return keys


def _bm25(
    query_weights: dict[str, float],
    documents: tuple[Counter[str], ...],
    *,
    k1: float = 1.2,
    b: float = 0.85,
) -> list[float]:
    size = len(documents)
    lengths = [sum(counter.values()) for counter in documents]
    average = (sum(lengths) / size) if size else 0.0
    document_frequency: Counter[str] = Counter()
    for counter in documents:
        document_frequency.update(counter.keys())
    scores = [0.0] * size
    for term, weight in query_weights.items():
        frequency = document_frequency.get(term, 0)
        if not frequency:
            continue
        idf = math.log(1.0 + (size - frequency + 0.5) / (frequency + 0.5)) * weight
        for index, counter in enumerate(documents):
            term_frequency = counter.get(term, 0)
            if not term_frequency:
                continue
            scale = (lengths[index] / average) if average else 1.0
            denominator = term_frequency + k1 * (1.0 - b + b * scale)
            scores[index] += idf * (term_frequency * (k1 + 1.0)) / denominator
    return scores


def _unit(scores: list[float]) -> list[float]:
    top = max(scores) if scores else 0.0
    if top <= 0.0:
        return [0.0] * len(scores)
    return [score / top for score in scores]


def _term_weights(query_terms: tuple[str, ...]) -> dict[str, float]:
    """Weight the question's own words above the reporting synonyms of those words."""

    weights = dict.fromkeys(query_terms, 1.0)
    for term in query_terms:
        for synonym in _EXPANSIONS.get(term, ()):
            weights.setdefault(synonym, _EXPANSION_WEIGHT)
    return weights


def _lexical_scores(query_terms: tuple[str, ...], documents: tuple[Counter[str], ...]) -> list[float]:
    weights = _term_weights(query_terms)
    exact = _unit(_bm25(weights, documents))
    prefixed_weights: dict[str, float] = {}
    for term, weight in weights.items():
        key = _prefix_of(term)
        prefixed_weights[key] = max(prefixed_weights.get(key, 0.0), weight)
    prefixed = _unit(_bm25(prefixed_weights, tuple(_prefix_counter(c) for c in documents)))
    return [value + _PREFIX_WEIGHT * extra for value, extra in zip(exact, prefixed)]


def _prefix_of(term: str) -> str:
    return term[:_PREFIX_LENGTH] if len(term) >= _PREFIX_LENGTH else term


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
