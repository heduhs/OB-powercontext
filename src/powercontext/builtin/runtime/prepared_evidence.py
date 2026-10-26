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

"""Query-driven selection of literal evidence spans inside recalled Memory entries.

This module owns *only* the ranking and packing of literal spans. It performs no I/O and
never rewrites text: every returned span is a ``[start, end)`` character slice of the entry
text it came from, so a caller can cite the original entry and re-read the same characters.

Byte budgeting is delegated to the caller through ``measure``: the caller renders the spans
it would actually deliver (including trust policy, envelope markers, JSON structure and
citations) and reports the exact UTF-8 size. Selection therefore accounts for the full
output cost rather than the prose alone.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactRef

_TOKEN = re.compile(r"[a-z]+|\d+")
_NUMERIC = re.compile(r"\d+")

# Splitting sentences on terminal punctuation alone breaks on abbreviations such as
# "e.g." or "et al.". Those fragments are merged back by refusing the split.
_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "vs.",
    "etc.",
    "al.",
    "fig.",
    "figs.",
    "eq.",
    "approx.",
    "cf.",
    "ref.",
    "refs.",
    "no.",
    "dr.",
    "prof.",
    "inc.",
    "ltd.",
    "min.",
    "max.",
    "resp.",
    "ca.",
)

_QUOTE_CLOSERS = "\"')]\u201d\u2019"

# A span shorter than this is almost always a section title rather than a finding, so it
# is down-weighted without being excluded: titles can still carry the query's subject.
_SHORT_SPAN_CHARS = 40

# Hard ceiling on delivered fragments. The byte budget is the real limit; this only stops
# a pathological candidate set from producing hundreds of one-word items.
_MAX_FRAGMENTS = 12


@dataclass(frozen=True)
class EvidenceCandidate:
    """One recalled Memory entry, identified exactly and carrying its literal text."""

    scope_id: str
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str
    rank: int


@dataclass(frozen=True)
class EvidenceSpan:
    """A literal ``[start, end)`` slice of one candidate's text."""

    candidate: EvidenceCandidate
    start: int
    end: int
    text: str


def select_evidence_spans(
    query: str,
    candidates: Sequence[EvidenceCandidate],
    *,
    max_bytes: int,
    measure: Callable[[Sequence[EvidenceSpan]], int],
) -> tuple[EvidenceSpan, ...]:
    """Choose literal spans that answer ``query`` within ``max_bytes`` of rendered output.

    Ranking is IDF-weighted query coverage, so a rare term in the query outweighs a common
    one, and spans that merely repeat a frequent word do not crowd out the real evidence.
    Packing is greedy by score; once scored spans are exhausted the remaining budget is
    filled with the leftover text in reading order, because unused bytes are wasted
    evidence rather than prudence.
    """

    if not candidates or max_bytes <= 0:
        return ()

    spans = _candidate_spans(candidates)
    if not spans:
        return ()

    ranked = _rank(query, spans)
    selected: list[EvidenceSpan] = []
    for span in ranked:
        if len(selected) >= _MAX_FRAGMENTS:
            break
        if any(_overlaps(span, chosen) for chosen in selected):
            continue
        if measure((*selected, span)) <= max_bytes:
            selected.append(span)

    if len(selected) < _MAX_FRAGMENTS:
        for span in spans:
            if len(selected) >= _MAX_FRAGMENTS:
                break
            if any(_overlaps(span, chosen) for chosen in selected):
                continue
            if measure((*selected, span)) <= max_bytes:
                selected.append(span)

    return tuple(sorted(selected, key=lambda span: (span.candidate.rank, span.start)))


def _candidate_spans(candidates: Sequence[EvidenceCandidate]) -> tuple[EvidenceSpan, ...]:
    """Split every candidate into literal sentence spans, preserving exact offsets."""

    collected: list[EvidenceSpan] = []
    seen_text: set[str] = set()
    for candidate in candidates:
        for start, end in _sentence_bounds(candidate.text):
            literal = candidate.text[start:end]
            key = literal.strip().lower()
            if not key or key in seen_text:
                continue
            seen_text.add(key)
            collected.append(EvidenceSpan(candidate=candidate, start=start, end=end, text=literal))
    return tuple(collected)


def _sentence_bounds(text: str) -> tuple[tuple[int, int], ...]:
    """Return trimmed half-open character bounds for each sentence in ``text``."""

    bounds: list[tuple[int, int]] = []
    length = len(text)
    start = 0
    index = 0
    while index < length:
        char = text[index]
        if char not in ".!?":
            index += 1
            continue
        following = index + 1
        if (
            char == "."
            and following < length
            and text[following].isdigit()
            and index > 0
            and text[index - 1].isdigit()
        ):
            index = following
            continue
        cursor = following
        while cursor < length and text[cursor] in _QUOTE_CLOSERS:
            cursor += 1
        if cursor < length and not text[cursor].isspace():
            index = following
            continue
        if _ends_with_abbreviation(text, index + 1):
            index = following
            continue
        _append_trimmed(bounds, text, start, cursor)
        start = cursor
        index = cursor
    _append_trimmed(bounds, text, start, length)
    return tuple(bounds)


def _ends_with_abbreviation(text: str, end: int) -> bool:
    tail = text[max(0, end - 12) : end].lower()
    return any(tail.endswith(abbreviation) for abbreviation in _ABBREVIATIONS)


def _append_trimmed(bounds: list[tuple[int, int]], text: str, start: int, end: int) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        bounds.append((start, end))


def _rank(query: str, spans: Sequence[EvidenceSpan]) -> tuple[EvidenceSpan, ...]:
    """Order spans by IDF-weighted query coverage, then by intrinsic match quality."""

    query_terms = _tokens(query)
    if not query_terms:
        return ()

    span_tokens = [_tokens(span.text) for span in spans]
    document_frequency: dict[str, int] = {}
    for tokens in span_tokens:
        for term in set(tokens):
            document_frequency[term] = document_frequency.get(term, 0) + 1

    total = len(spans)
    idf: dict[str, float] = {
        term: math.log(1.0 + total / (1.0 + frequency)) for term, frequency in document_frequency.items()
    }
    absent = math.log(1.0 + total)
    query_weight = sum(idf.get(term, absent) for term in set(query_terms)) or 1.0
    query_set = set(query_terms)
    query_numbers = {term for term in query_set if term.isdigit()}

    scored: list[tuple[float, int, int, EvidenceSpan]] = []
    for span, tokens in zip(spans, span_tokens, strict=True):
        token_set = set(tokens)
        matched = query_set & token_set
        if not matched:
            continue
        coverage = sum(idf.get(term, absent) for term in matched) / query_weight
        score = coverage
        score *= 1.0 + 0.30 * _bigram_ratio(query_terms, tokens)
        if query_numbers & set(_NUMERIC.findall(span.text.lower())):
            score *= 1.15
        if len(span.text) < _SHORT_SPAN_CHARS:
            score *= 0.75
        # A very long sentence that matches the same terms is less precise evidence.
        score /= 1.0 + 0.002 * max(0, len(tokens) - 40)
        scored.append((score, span.candidate.rank, span.start, span))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(item[3] for item in scored)


def _bigram_ratio(query_terms: Sequence[str], tokens: Sequence[str]) -> float:
    """Share of adjacent query pairs that appear adjacent in the span."""

    if len(query_terms) < 2:
        return 0.0
    pairs = list(zip(query_terms, query_terms[1:]))
    present = set(zip(tokens, tokens[1:]))
    hits = sum(1 for pair in pairs if pair in present)
    return hits / len(pairs)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(text.lower()))


def _overlaps(left: EvidenceSpan, right: EvidenceSpan) -> bool:
    if left.candidate.entry_id != right.candidate.entry_id:
        return False
    if left.candidate.entry_version_id != right.candidate.entry_version_id:
        return False
    if left.candidate.scope_id != right.candidate.scope_id:
        return False
    return left.start < right.end and right.start < left.end
