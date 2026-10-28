"""Corpus initialization: load the bundled sample corpus into seekdb.

Usage:
    python -m backend.ingest                 # offline lexical embeddings
    ZHIYAN_EMBED_BACKEND=minimax python -m backend.ingest
"""

from __future__ import annotations

from .retrieval import get_store
from .sample_data import all_documents


def main() -> None:
    store = get_store()
    result = store.ingest(all_documents())
    print(f"seekdb 导入 {result['ingested']} 篇，库内现有 {result['stored']} 篇，"
          f"向量维度 {result['dim']}")
    print(f"embedding: {result['embedding']['backend']} / {result['embedding']['model']}")
    print(f"索引: {store.stats()['indexes']}")
    store.close()


if __name__ == "__main__":
    main()
