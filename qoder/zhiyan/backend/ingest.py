"""Corpus initialization: load the bundled sample corpus into seekdb.

Usage:  python -m backend.ingest
"""

from __future__ import annotations

from .retrieval import get_store
from .sample_data import all_documents


def main() -> None:
    store = get_store()
    result = store.ingest(all_documents())
    print(f"ingested {result['ingested']} documents into seekdb collection '{result['collection']}'")
    print(f"corpus count now: {store.count()}")


if __name__ == "__main__":
    main()
