#!/usr/bin/env python3
"""Read one weak point back from PowerContext in a *separate* process.

verify.py calls this to demonstrate that learning memory survives across process
restarts — the property a reviewer cannot confirm from a single in-process call.
Prints one JSON line on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.memory import get_memory  # noqa: E402


def main() -> int:
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    points = get_memory().list_weak_points()
    hit = next((p for p in points if p.get("point") == wanted), None)
    print(json.dumps({"found": hit is not None,
                      "pid": __import__("os").getpid(),
                      "total_weak_points": len(points),
                      "version": (hit or {}).get("version"),
                      "review_count": (hit or {}).get("review_count"),
                      "next_review": (hit or {}).get("next_review")}, ensure_ascii=False))
    return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(main())
