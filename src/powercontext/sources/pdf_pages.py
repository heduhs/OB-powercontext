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
_PREFIX_LENGTH = 5
_PREFIX_WEIGHT = 0.3
_HEADING_WEIGHT = 0.2
_MAX_FTS_QUERIES = 4
_EXPANSION_WEIGHT = 0.55
_TITLE_TERMS = 80
_STATEMENT_BONUS = 0.45
_STATEMENT_SLOTS = 3
_LENGTH_NORMALISATION = 0.85

# Running headers repeat on every page of a filing and carry no page subject.
_RUNNING_HEADER = "tableofcontents"

# A financial question is usually answered by one of the primary statements, whose
# body is mostly figures; the heading is the only place that names the statement.
_STATEMENT_HINT = re.compile(
    r"consolidated (?:statement|balance|financial)"
    r"|statements? of (?:income|operations|earnings|cash flow|financial position|changes in equity)"
    r"|balance sheets?",
    re.IGNORECASE,
)
# The four primary statements a financial question is answered from.
_STATEMENT_FAMILIES: tuple[re.Pattern[str], ...] = (
    re.compile(r"statement[s]? of (?:income|operations|earnings)|statements? of operations", re.IGNORECASE),
    re.compile(r"balance sheets?|statement of financial position|financial condition", re.IGNORECASE),
    re.compile(r"statements? of cash flows?", re.IGNORECASE),
    re.compile(r"changes in (?:stockholders|shareholders)['’]? equity", re.IGNORECASE),
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

    The lexical channel refuses a page unless it shares several question words, and a
    financial statement shares almost none, so every cached page is scored here instead
    and the citation is resolved back to its page.
    """

    from powercontext.builtin.runtime import SearchMemoryRequest

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    query_terms = _query_terms(question)
    if not query_terms:
        return ()
    try:
        records = tuple((await memory.list()).entries)
    except Exception:  # noqa: BLE001 - fall back to the lexical channel rather than fail
        return await _lexical_channel_citations(memory, question, limit, SearchMemoryRequest)
    if not records:
        return ()
    # One Memory may hold several reports. A question is scored inside each report
    # separately so the corpus size never shifts the ranking, and the report the
    # question asks about keeps the twenty slots.
    labels = tuple(_document_label(record.entry.text) for record in records)
    scores, terms_by_report = _page_scores(query_terms, records, labels)
    fence = _resolve_document(question, labels, terms_by_report, _report_tokens(labels, records))

    ranked = sorted(range(len(records)), key=lambda index: (-scores[index], index))
    citations: list[Any] = []
    seen_pages: set[str] = set()

    def take(index: int) -> bool:
        if fence is not None and labels[index] != fence:
            return False
        # One page may be projected into several entries; the page is the citation unit.
        key = _page_key(records[index])
        if key in seen_pages:
            return False
        seen_pages.add(key)
        citations.append(records[index].citation)
        return True

    # A financial question is answered by the primary statements, whose bodies are
    # mostly figures; reserve a slot for each one instead of trusting term overlap.
    slots = _STATEMENT_SLOTS if _FINANCIAL_TERMS.intersection(query_terms) else 0
    for index in _statement_anchors(records, scores, labels, fence)[:slots]:
        if len(citations) >= limit:
            break
        take(index)
    for index in ranked:
        if len(citations) >= limit:
            break
        take(index)
    return tuple(citations)


def _statement_anchors(
    records: tuple[Any, ...], scores: list[float], labels: tuple[str, ...], fence: str | None
) -> tuple[int, ...]:
    """Best-scoring page of each primary statement type inside the chosen report."""

    anchors: list[int] = []
    for family in _STATEMENT_FAMILIES:
        best: int | None = None
        for index, record in enumerate(records):
            if fence is not None and labels[index] != fence:
                continue
            if not family.search(_heading_of(record.entry.text)):
                continue
            if best is None or scores[index] > scores[best]:
                best = index
        if best is not None:
            anchors.append(best)
    return tuple(anchors)


def _page_scores(
    query_terms: tuple[str, ...], records: tuple[Any, ...], labels: tuple[str, ...]
) -> tuple[list[float], dict[str, set[str]]]:
    """Score every cached page, keeping each report's statistics independent."""

    weights = _term_weights(query_terms)
    scores = [0.0] * len(records)
    terms_by_report: dict[str, set[str]] = {}

    groups: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    for label, indices in groups.items():
        bodies: list[Counter[str]] = []
        headings: list[Counter[str]] = []
        vocabulary: set[str] = set()
        statement: list[bool] = []
        for index in indices:
            text = records[index].entry.text
            body = _document_terms(text)
            bodies.append(body)
            headings.append(_heading_terms(text))
            vocabulary.update(body)
            statement.append(bool(_STATEMENT_HINT.search(_heading_of(text))))
        terms_by_report[label] = vocabulary
        body_scores = _unit(_bm25(weights, tuple(bodies)))
        heading_scores = _unit(_bm25(weights, tuple(headings)))
        prefix_scores = _unit(_bm25(_prefix_weights(weights), tuple(_prefix_counters(bodies))))
        for offset, index in enumerate(indices):
            score = body_scores[offset] + _HEADING_WEIGHT * heading_scores[offset]
            score += _PREFIX_WEIGHT * prefix_scores[offset]
            if statement[offset]:
                score += _STATEMENT_BONUS
            scores[index] = score
    return scores, terms_by_report


def _prefix_weights(weights: dict[str, float]) -> dict[str, float]:
    """Collapse the query to word stems so "geographies" still meets "geographic"."""

    collapsed: dict[str, float] = {}
    for term, weight in weights.items():
        key = term[:_PREFIX_LENGTH] if len(term) >= _PREFIX_LENGTH else term
        collapsed[key] = max(collapsed.get(key, 0.0), weight)
    return collapsed


def _prefix_counters(bodies: list[Counter[str]]) -> list[Counter[str]]:
    counters: list[Counter[str]] = []
    for body in bodies:
        truncated: Counter[str] = Counter()
        for term, count in body.items():
            truncated[term[:_PREFIX_LENGTH] if len(term) >= _PREFIX_LENGTH else term] += count
        counters.append(truncated)
    return counters


def _heading_of(text: str) -> str:
    """The page's own opening line, skipping the document id and running header."""

    lines = _heading_lines(text)
    if not lines:
        return ""
    heading = lines[0]
    if len(heading) < 40 and len(lines) > 1:
        heading = f"{heading} {lines[1]}"
    return heading[:120]


def _heading_lines(text: str) -> list[str]:
    body = _page_body(text)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return lines[_running_header_length(lines) :]


def _running_header_length(lines: list[str]) -> int:
    """How many leading lines spell a "Table of Contents" running header."""

    consumed = ""
    for index, line in enumerate(lines[:4]):
        candidate = consumed + re.sub(r"[^a-z]", "", line.casefold())
        if not candidate or not _RUNNING_HEADER.startswith(candidate):
            break
        consumed = candidate
        if consumed == _RUNNING_HEADER:
            return index + 1
    return 0


def _page_body(text: str) -> str:
    head, separator, body = text.partition("\n")
    return body if separator else head


def _heading_terms(text: str) -> Counter[str]:
    return Counter(_normalize_terms(_heading_of(text)))


_FILING_NOISE = frozenset({"10k", "10q", "8k", "earnings", "annual", "report", "call", "transcript", "dated"})
_PERIOD = re.compile(r"^(?:19|20)\d{2}(?:q[1-4])?$")


def _document_label(text: str) -> str:
    """Reduce a projection's document id to the bare report owner ("PEPSICO")."""

    return "".join(_owner_tokens(text))


def _owner_tokens(text: str) -> list[str]:
    """Split the document id into owner tokens, dropping the period and filing type."""

    head, _separator, _body = text.partition("\n")
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", head) if part]
    while len(parts) > 1 and _is_filing_noise(parts[-1]):
        parts.pop()
    return [part.casefold() for part in parts]


def _is_filing_noise(part: str) -> bool:
    lowered = part.casefold()
    return lowered in _FILING_NOISE or bool(_PERIOD.match(lowered))


def _resolve_document(
    question: str,
    labels: tuple[str, ...],
    terms_by_report: dict[str, set[str]],
    report_tokens: dict[str, list[str]],
) -> str | None:
    """Identify the one report the question asks about, or ``None`` when unsure."""

    unique = sorted(set(labels))
    if len(unique) < 2:
        return None
    question_tokens = [token.casefold() for token in re.findall(r"[A-Za-z0-9]+", question)]
    squashed = "".join(question_tokens)
    owners = _owners_by_term(terms_by_report)
    strengths = {label: _name_strength(report_tokens[label], squashed, question_tokens, owners) for label in unique}
    top = max(strengths.values())
    if top == 0:
        return _covered_report(question_tokens, terms_by_report)
    named = [label for label, strength in strengths.items() if strength == top]
    if len(named) > 1 and top < 2:
        return None
    return max(named, key=len)


def _owners_by_term(terms_by_report: dict[str, set[str]]) -> dict[str, int]:
    owners: dict[str, int] = {}
    for vocabulary in terms_by_report.values():
        for term in vocabulary:
            owners[term] = owners.get(term, 0) + 1
    return owners


def _name_strength(
    tokens: list[str], squashed_question: str, question_tokens: list[str], owners: dict[str, int]
) -> int:
    """How strongly the question calls this report by name: 2 exact, 1 alias, 0 not at all."""

    if not tokens:
        return 0
    key = "".join(tokens)
    # Short keys ("3M") must be whole words; as substrings they hide inside figures.
    if len(key) >= 4 and key in squashed_question:
        return 2
    strength = 0
    for token in set(tokens):
        best = 0
        for candidate in question_tokens:
            if _token_matches(token, candidate, owners):
                best = 2
                break
            if owners.get(candidate, 0) <= 1 and (_is_alias(candidate, token) or _is_alias(candidate, key)):
                best = 1
        if not best:
            return 0
        strength = max(strength, best)
    return strength


def _token_matches(label_token: str, question_token: str, owners: dict[str, int]) -> bool:
    if label_token == question_token:
        return True
    if len(label_token) >= 4 and len(question_token) >= 4:
        return question_token.startswith(label_token) or label_token.startswith(question_token)
    return False


def _is_alias(question_token: str, label_token: str) -> bool:
    """Accept a short alias such as "JnJ" or "AMEX" spelled from the report's own letters."""

    if not 3 <= len(question_token) <= 5 or len(label_token) < 2 * len(question_token):
        return False
    if question_token[0] != label_token[0]:
        return False
    remaining = iter(label_token)
    return all(letter in remaining for letter in question_token)


def _covered_report(question_tokens: list[str], terms_by_report: dict[str, set[str]]) -> str | None:
    """Fall back to the report that covers most of the question's rare vocabulary."""

    wanted = {token for token in question_tokens if len(token) > 1 and token not in _STOPWORDS}
    if not wanted or not terms_by_report:
        return None
    size = len(terms_by_report)
    document_frequency = {
        term: sum(1 for vocabulary in terms_by_report.values() if term in vocabulary) for term in wanted
    }
    best: str | None = None
    best_value = 0.0
    for label, vocabulary in terms_by_report.items():
        value = 0.0
        for term in wanted:
            if term in vocabulary:
                value += math.log(1.0 + (size - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        if value > best_value:
            best, best_value = label, value
    return best


def _report_tokens(labels: tuple[str, ...], records: tuple[Any, ...]) -> dict[str, list[str]]:
    tokens: dict[str, list[str]] = {}
    for label, record in zip(labels, records):
        if label not in tokens:
            tokens[label] = _owner_tokens(record.entry.text)
    return tokens


def _page_key(record: Any) -> str:
    """Identify the physical page a cached entry belongs to."""

    sources = getattr(record.entry, "sources", ())
    if sources:
        return sources[0].source_id
    return record.entry.entry_id


async def _lexical_channel_citations(memory: Any, question: str, limit: int, search_request: Any) -> tuple[Any, ...]:
    """Degraded path: answer from the backend's own lexical channel only."""

    from powercontext.builtin.artifacts.memory import MemoryCitation

    citations: list[Any] = []
    seen: set[str] = set()
    window = max(limit * 4, 40)
    for query in _candidate_queries(question)[:_MAX_FTS_QUERIES]:
        page = await memory.search(search_request(query=query, mode="fts", limit=window))
        for hit in page.hits:
            if hit.entry_id in seen:
                continue
            seen.add(hit.entry_id)
            citations.append(
                MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
            )
            if len(citations) >= limit:
                return tuple(citations)
    return tuple(citations)


def _query_terms(question: str) -> tuple[str, ...]:
    terms = [term for term in _normalize_terms(question) if len(term) > 1 and term not in _STOPWORDS]
    return tuple(dict.fromkeys(terms))


def _normalize_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _WORD.findall(text.casefold()):
        terms.append(raw.replace(",", "") if raw[0].isdigit() else raw)
    return terms


def _document_terms(text: str) -> Counter[str]:
    terms = _normalize_terms(_page_body(text))
    counter: Counter[str] = Counter(terms)
    # Report pages announce their subject in the opening lines ("Consolidated Statement
    # of Income"), which is exactly the phrase a question tends to reuse.
    for term in _normalize_terms(_heading_of(text))[:_TITLE_TERMS]:
        counter[term] += 1
    return counter


def _bm25(
    query_weights: dict[str, float],
    documents: tuple[Counter[str], ...],
    *,
    k1: float = 1.2,
    b: float | None = None,
) -> list[float]:
    b = _LENGTH_NORMALISATION if b is None else b
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
