#!/usr/bin/env python3
"""Load a JSONL corpus into seekdb with a vector index and a Chinese fulltext index.

Usage:
  python scripts/build_index.py --corpus assets/sample/corpus.jsonl
  python scripts/build_index.py --corpus my.jsonl --table kb_docs --backend api

Corpus line format (only "body" and "doc_id" are required):
  {"doc_id":"d1","title":"...","body":"...","category":"db","url":"https://..."}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import embed as emb  # noqa: E402


def read_corpus(path: str):
    docs = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno} 不是合法 JSON：{exc}")
        if "body" not in row:
            raise SystemExit(f"{path}:{lineno} 缺少 body 字段")
        docs.append({
            "doc_id": str(row.get("doc_id") or f"auto-{lineno}"),
            "title": row.get("title", ""),
            "body": row["body"],
            "category": row.get("category", ""),
            "url": row.get("url", ""),
        })
    ids = [d["doc_id"] for d in docs]
    if len(ids) != len(set(ids)):
        dup = {i for i in ids if ids.count(i) > 1}
        raise SystemExit(f"corpus 中 doc_id 重复：{sorted(dup)}")
    return docs


def main():
    ap = argparse.ArgumentParser(description="建库并写入 seekdb")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--table", default=db.DEFAULT_TABLE)
    ap.add_argument("--backend", default="local", choices=["local", "api", "seekdb"])
    ap.add_argument("--model", default=None, help="api: EMBED_MODEL 覆盖；seekdb: AI MODEL 别名")
    ap.add_argument("--dim", type=int, default=emb.DIM_DEFAULT, help="local 后端向量维度")
    ap.add_argument("--parser", default="ik", help="全文分词器，中文必须 ik（none 表示默认空格分词）")
    ap.add_argument("--no-drop", action="store_true", help="保留已有表（追加写入）")
    args = ap.parse_args()
    args.table = db.check_identifier(args.table, "表名")
    if args.parser and not re.fullmatch(r"[A-Za-z0-9_]+", args.parser):
        raise SystemExit(f"--parser {args.parser!r} 非法，只允许字母数字下划线（如 ik）")

    docs = read_corpus(args.corpus)
    text_for_vec = [f"{d['title']}。{d['body']}" if d["title"] else d["body"] for d in docs]

    conn = db.connect()
    with db.cursor(conn) as cur:
        vectors = emb.embed(text_for_vec, backend=args.backend, dim=args.dim,
                            model=args.model, cur=cur)
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise SystemExit(f"embedding 维度不一致（{dim} 起）——同一批必须来自同一个模型")

    if not args.no_drop:
        db.drop_table(conn, args.table)
    with db.cursor(conn) as cur:
        cur.execute(db.table_ddl(args.table, dim, args.parser))

    inserted = 0
    start = time.time()
    with db.cursor(conn) as cur:
        for doc, vec in zip(docs, vectors):
            cur.execute(
                f"insert into {args.table}(doc_id,title,body,category,url,embedding) "
                "values(%s,%s,%s,%s,%s,%s)",
                (doc["doc_id"], doc["title"], doc["body"][:4000], doc["category"],
                 doc["url"], emb.as_sql_vector(vec)),
            )
            inserted += 1

    with db.cursor(conn) as cur:
        cur.execute(f"select count(*) from {args.table}")
        stored = cur.fetchone()[0]
        cur.execute(f"show index from {args.table}")
        indexes = [(r[2], r[10] or r[3]) for r in cur.fetchall()]

    print(json.dumps({
        "table": args.table,
        "docs_in_corpus": len(docs),
        "rows_inserted": inserted,
        "rows_stored": stored,
        "vector_dim": dim,
        "backend": args.backend,
        "fulltext_parser": args.parser,
        "indexes": [{"name": n, "column": c} for n, c in indexes],
        "seconds": round(time.time() - start, 2),
    }, ensure_ascii=False, indent=2))
    if stored != len(docs):
        raise SystemExit(f"写入行数 {stored} != 语料 {len(docs)}，先排查再评测")


if __name__ == "__main__":
    main()
