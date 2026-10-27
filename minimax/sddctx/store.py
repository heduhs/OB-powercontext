"""Ingestion and retrieval over seekdb.

Engineering material (spec entries, code symbols, test cases) is parsed into
chunks and stored in a single seekdb collection. Retrieval is hybrid: the
query combines vector similarity (embeddings) with full-text matching, both
executed inside seekdb.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pyseekdb

from .config import settings
from .embeddings import SddEmbeddingFunction, resolve_provider

COLLECTION = "sddctx_materials"


@dataclass
class Chunk:
    chunk_id: str
    kind: str  # spec | code | test
    source: str  # repo-relative path
    symbol: str  # spec entry id / function or class name / test name
    text: str


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(settings.project_root)).replace("\\", "/")
    except ValueError:
        return path.name


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


# ---------------------------------------------------------------- parsing

_SPEC_ENTRY = re.compile(r"^-\s+([A-Z]{2,4}-\d+)\s*[:：]\s*(.+)$")


def parse_spec(path: Path) -> List[Chunk]:
    """Split a spec markdown file into rule-level entries (CS-1, QS-2, ...).

    Each entry keeps its section heading as context. The file preamble and
    invariants sections are stored as their own chunks.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    chunks: List[Chunk] = []
    section = ""
    buffer: List[str] = []
    title = path.stem

    def flush_para() -> None:
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    chunk_id=_stable_id(_rel(path), "para", text[:80]),
                    kind="spec",
                    source=_rel(path),
                    symbol=f"{title}#{section or 'preamble'}",
                    text=text,
                )
            )
        buffer.clear()

    for line in lines:
        heading = re.match(r"^#+\s+(.*)$", line)
        entry = _SPEC_ENTRY.match(line.strip())
        if heading:
            flush_para()
            section = heading.group(1).strip()
            continue
        if entry:
            flush_para()
            entry_id, body = entry.group(1), entry.group(2)
            chunks.append(
                Chunk(
                    chunk_id=_stable_id(_rel(path), "entry", entry_id),
                    kind="spec",
                    source=_rel(path),
                    symbol=entry_id,
                    text=f"[{entry_id}] ({section}) {body}",
                )
            )
            continue
        buffer.append(line)
    flush_para()
    return chunks


def parse_python(path: Path, kind: str) -> List[Chunk]:
    """Split a python file into top-level symbol chunks (functions/classes)."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    chunks: List[Chunk] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            text = "\n".join(lines[start:end])
            is_test = kind == "test" and node.name.startswith("test")
            if isinstance(node, ast.ClassDef):
                # keep test classes as one chunk, expand methods for code classes
                if kind == "test":
                    chunks.append(
                        Chunk(
                            chunk_id=_stable_id(_rel(path), node.name),
                            kind="test",
                            source=_rel(path),
                            symbol=node.name,
                            text=text,
                        )
                    )
                    continue
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        s2 = sub.lineno - 1
                        e2 = getattr(sub, "end_lineno", sub.lineno)
                        sub_text = "\n".join(lines[s2:e2])
                        chunks.append(
                            Chunk(
                                chunk_id=_stable_id(_rel(path), f"{node.name}.{sub.name}"),
                                kind=kind,
                                source=_rel(path),
                                symbol=f"{node.name}.{sub.name}",
                                text=sub_text,
                            )
                        )
                continue
            chunks.append(
                Chunk(
                    chunk_id=_stable_id(_rel(path), node.name),
                    kind="test" if is_test else kind,
                    source=_rel(path),
                    symbol=node.name,
                    text=text,
                )
            )
    return chunks


def collect_chunks(project_root: Optional[Path] = None) -> List[Chunk]:
    root = project_root or settings.project_root
    chunks: List[Chunk] = []
    for spec in sorted((root / "specs").glob("*.md")):
        chunks.extend(parse_spec(spec))
    pkg_dir = root / "ordersvc"
    for py in sorted(pkg_dir.glob("**/*.py")):
        chunks.extend(parse_python(py, "code"))
    for py in sorted((root / "tests").glob("test_*.py")):
        chunks.extend(parse_python(py, "test"))
    return chunks


# ---------------------------------------------------------------- storage


class ContextStore:
    def __init__(self, provider: Optional[str] = None) -> None:
        self.provider = provider or resolve_provider()
        self._ef = SddEmbeddingFunction(provider=self.provider)
        self._client = pyseekdb.RemoteServerClient(
            host=settings.seekdb_host,
            port=settings.seekdb_port,
            tenant=settings.seekdb_tenant,
            user=settings.seekdb_user,
            password=settings.seekdb_password,
            database=settings.seekdb_database,
        )

    def _collection(self, recreate: bool = False):
        if recreate and self._client.has_collection(COLLECTION):
            self._client.delete_collection(COLLECTION)
        return self._client.get_or_create_collection(
            name=COLLECTION, embedding_function=self._ef
        )

    def ingest(self, project_root: Optional[Path] = None) -> dict:
        """(Re)import all materials. The collection is rebuilt from scratch so
        the stored embedding dimension always matches the active provider, and
        an updated spec replaces its previous content on re-import."""
        chunks = collect_chunks(project_root)
        col = self._collection(recreate=True)
        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [
            {"kind": c.kind, "source": c.source, "symbol": c.symbol} for c in chunks
        ]
        col.upsert(ids=ids, documents=docs, metadatas=metas)
        by_kind: dict = {}
        for c in chunks:
            by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        return {"ingested": len(chunks), "by_kind": by_kind, "provider": self.provider}

    def retrieve(
        self,
        task: str,
        top_k: int = 6,
        kinds: Optional[List[str]] = None,
    ) -> List[dict]:
        """Hybrid retrieval for a task description. Returns ranked chunks with
        source provenance."""
        col = self._collection()
        where = None
        if kinds:
            if len(kinds) == 1:
                where = {"kind": kinds[0]}
            else:
                where = {"kind": {"$in": kinds}}
        result = col.query(
            query_texts=[task],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for doc, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append(
                {
                    "text": doc,
                    "kind": meta.get("kind"),
                    "source": meta.get("source"),
                    "symbol": meta.get("symbol"),
                    "distance": dist,
                }
            )
        return hits

    def stats(self) -> dict:
        col = self._collection()
        return {"count": col.count(), "provider": self.provider}
