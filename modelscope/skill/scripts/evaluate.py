#!/usr/bin/env python3
"""Score vector / fulltext / hybrid retrieval strategies on an annotated query set.

Usage:
  python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table kb_docs
  python scripts/evaluate.py --qrels q.jsonl --strategies vector,fulltext,hybrid_rrf --k 10
  python scripts/evaluate.py --qrels q.jsonl --filter category=db      # scalar filter
  python scripts/evaluate.py --qrels q.jsonl --out reports/run1        # report.md/.json

Query set line format:
  {"query_id":"q1","query":"...","relevant":["d1","d4"]}

Every leg is answered by seekdb itself; the fusion (RRF or weighted) is the only
thing done client-side, so you can lift it into one SQL statement later.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import embed as emb  # noqa: E402
import metrics  # noqa: E402

ALL_STRATEGIES = ["vector", "fulltext", "hybrid_rrf", "hybrid_weighted"]
RRF_K0 = 60.0


def read_qrels(path: str):
    rows = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno} 不是合法 JSON：{exc}")
        if "query" not in row:
            raise SystemExit(f"{path}:{lineno} 缺少 query 字段")
        rows.append({
            "query_id": str(row.get("query_id") or f"q{lineno}"),
            "query": row["query"],
            "relevant": {str(x) for x in row.get("relevant", [])},
            "tags": [str(t) for t in row.get("tags", [])],
        })
    if not rows:
        raise SystemExit(f"{path} 是空的")
    return rows


def table_columns(conn, table):
    with db.cursor(conn) as cur:
        cur.execute(f"show columns from {table}")
        return {str(r[0]).lower() for r in cur.fetchall()}


def vector_leg(cur, table, query_vec, depth, filt):
    where = f"where {filt[0]} = %s " if filt else ""
    literal = emb.as_sql_vector(query_vec)
    sql = (f"select doc_id, cosine_distance(embedding, %s) score from {table} "
           f"{where}order by cosine_distance(embedding, %s) approx limit %s")
    args = (literal, literal, depth) if not filt else (literal, filt[1], literal, depth)
    cur.execute(sql, args)
    out = []
    for doc_id, score in cur.fetchall():
        out.append((str(doc_id), 1.0 - float(score)))  # cosine distance -> similarity
    return out


def fulltext_leg(cur, table, query, depth, filt, boolean_mode):
    mode = " in boolean mode" if boolean_mode else ""
    match_expr = f"match(body) against(%s{mode})"
    where = f"and {filt[0]} = %s " if filt else ""
    sql = (f"select doc_id, {match_expr} relevance from {table} "
           f"where {match_expr} {where}order by relevance desc limit %s")
    args = (query, query, depth) if not filt else (query, query, filt[1], depth)
    cur.execute(sql, args)
    return [(str(doc_id), float(score or 0.0)) for doc_id, score in cur.fetchall()]


def rrf(vec_list, ft_list, k0=RRF_K0, w_vec=0.5):
    fused = {}
    for rank, (doc_id, _s) in enumerate(vec_list, start=1):
        fused[doc_id] = fused.get(doc_id, 0.0) + w_vec / (k0 + rank)
    for rank, (doc_id, _s) in enumerate(ft_list, start=1):
        fused[doc_id] = fused.get(doc_id, 0.0) + (1.0 - w_vec) / (k0 + rank)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))


def weighted(vec_list, ft_list, w_vec=0.5):
    def normalize(pairs):
        if not pairs:
            return {}
        values = [s for _d, s in pairs]
        lo, hi = min(values), max(values)
        span = hi - lo
        return {d: (1.0 if span == 0 else (s - lo) / span) for d, s in pairs}

    nv, nf = normalize(vec_list), normalize(ft_list)
    fused = {d: w_vec * nv.get(d, 0.0) + (1.0 - w_vec) * nf.get(d, 0.0)
             for d in set(nv) | set(nf)}
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))


def run(conn, args, qrels):
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in strategies if s not in ALL_STRATEGIES]
    if unknown:
        raise SystemExit(f"未知策略 {unknown}，可选 {ALL_STRATEGIES}")
    needs_legs = any(s in strategies for s in ("hybrid_rrf", "hybrid_weighted"))
    if needs_legs and ("vector" not in strategies or "fulltext" not in strategies):
        strategies = list(dict.fromkeys(["vector", "fulltext"] + strategies))

    db.require_table(conn, args.table)
    indexes = db.index_summary(conn, args.table)
    index_types = {i["type"] for i in indexes}
    if "FULLTEXT" not in index_types:
        raise SystemExit(f"{args.table} 上没有 FULLTEXT 索引，全文腿会直接报错。"
                         "用 build_index.py 重建：fulltext index ftidx (body) with parser ik")
    if "VECTOR" not in index_types:
        raise SystemExit(f"{args.table} 上没有 VECTOR 索引，向量腿会退化成全表精算。"
                         "用 build_index.py 重建：vector index vidx (embedding) "
                         "with (distance=cosine, type=hnsw)")
    filt = None
    if args.filter:
        column, _, value = args.filter.partition("=")
        column = column.strip().lower()
        if column not in table_columns(conn, args.table):
            raise SystemExit(f"过滤列 {column!r} 不在 {args.table} 的列里")
        filt = (column, value.strip())

    if args.only_tag:
        qrels = [r for r in qrels if args.only_tag in r["tags"]]
        if not qrels:
            raise SystemExit(f"没有 tag={args.only_tag} 的查询")

    filter_ceiling = None
    if filt:
        with db.cursor(conn) as cur:
            cur.execute(f"select doc_id from {args.table} where {filt[0]} = %s", (filt[1],))
            inside = {str(r[0]) for r in cur.fetchall()}
        total = sum(len(r["relevant"]) for r in qrels)
        reachable = sum(len(r["relevant"] & inside) for r in qrels)
        filter_ceiling = round(reachable / total, 4) if total else None

    collected = {name: [] for name in strategies}
    latencies = {name: [] for name in strategies}
    detail = []
    with db.cursor(conn) as cur:
        for row in qrels:
            t0 = time.time()
            query_vec = emb.embed([row["query"]], backend=args.backend, dim=args.dim,
                                  model=args.model, cur=cur)[0]
            legs = {}
            if "vector" in strategies:
                legs["vector"] = vector_leg(cur, args.table, query_vec, args.depth, filt)
            if "fulltext" in strategies:
                legs["fulltext"] = fulltext_leg(cur, args.table, row["query"], args.depth,
                                                filt, args.boolean_mode)
            if "hybrid_rrf" in strategies:
                legs["hybrid_rrf"] = rrf(legs["vector"], legs["fulltext"],
                                        k0=args.rrf_k0, w_vec=args.w_vec)
            if "hybrid_weighted" in strategies:
                legs["hybrid_weighted"] = weighted(legs["vector"], legs["fulltext"],
                                                  w_vec=args.w_vec)
            per_query = {"query_id": row["query_id"], "query": row["query"],
                         "n_relevant": len(row["relevant"])}
            for name, pairs in legs.items():
                ranked = [d for d, _s in pairs]
                collected[name].append((ranked, row["relevant"]))
                latencies[name].append((time.time() - t0) * 1000.0 / max(1, len(legs)))
                top = ranked[: args.k]
                per_query[name] = {
                    "top": top,
                    "found@k": len(set(top) & row["relevant"]),
                    "missed": sorted(row["relevant"] - set(top)),
                    "first_rank": next((i for i, d in enumerate(ranked[:args.depth], 1)
                                        if d in row["relevant"]), None),
                }
            detail.append(per_query)

    summary = {}
    for name in strategies:
        if not collected[name]:
            continue
        summary[name] = metrics.summarize(collected[name], ks=tuple(sorted({1, 5, args.k})))
        summary[name]["avg_latency_ms"] = round(sum(latencies[name]) / len(latencies[name]), 1)
    return summary, detail, {"filter_ceiling": filter_ceiling}


def to_markdown(summary, args):
    keys = ["recall@1", f"recall@{args.k}", f"precision@{args.k}", "MRR", "nDCG@10",
            "empty_result_rate", "avg_latency_ms"]
    lines = ["| strategy | " + " | ".join(keys) + " |",
             "|---|" + "---|" * len(keys)]
    for name, row in summary.items():
        cells = []
        for key in keys:
            value = row.get(key)
            cells.append("-" if value is None else
                         (f"{value:.4f}" if isinstance(value, float) and key != "avg_latency_ms"
                          else f"{value}"))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def diagnose(summary, detail, args, meta=None):
    notes = []
    ceiling = (meta or {}).get("filter_ceiling")
    if ceiling is not None and ceiling < 1.0:
        notes.append(f"标量过滤 {args.filter} 使标注文档只有 {ceiling:.0%} 落在过滤范围内，"
                     f"这就是本轮 recall 的上限——指标低不代表检索差，先让过滤范围与标注集一致。")
    ft = summary.get("fulltext")
    if ft and ft.get("empty_result_rate") == 1.0:
        notes.append("全文腿全部返回空结果：中文基本可以确定没走 ik 分词器，"
                     "建索引请写 `fulltext index ftx (body) with parser ik`。")
    vec = summary.get("vector")
    if vec and vec.get(f"recall@{args.k}") == 0 and ft and ft.get(f"recall@{args.k}"):
        notes.append("向量腿 recall 为 0 而全文腿正常：先确认 embedding 列维度与建表一致、"
                     "查询向量与文档向量来自同一个后端，再看 `explain` 里向量索引是否被使用。")
    best = max(summary, key=lambda n: summary[n].get(f"recall@{args.k}") or 0)
    if best == "vector":
        notes.append("纯向量已经最好：混合检索的关键词腿目前没有增益，检查分词与停用词，"
                     "或把候选池 --depth 加大后再比。")
    elif best.startswith("hybrid"):
        gain = ((summary[best].get(f"recall@{args.k}") or 0) -
                max((summary.get("vector", {}).get(f"recall@{args.k}") or 0),
                    (summary.get("fulltext", {}).get(f"recall@{args.k}") or 0)))
        notes.append(f"混合检索最优，相对最好单腿 +{gain:.4f} recall@{args.k}。")
    zeros = [d for d in detail if d.get(best, {}).get("found@k") == 0 and d["n_relevant"]]
    if zeros:
        notes.append(f"{len(zeros)}/{len(detail)} 条查询在最好策略下前 {args.k} 仍然全错，"
                     f"这些是补语料/改分块的第一优先级：{[z['query'] for z in zeros][:3]}")
    if args.backend == "local":
        notes.append("当前 backend=local（离线自检后端），指标只用于验证链路，"
                     "不要当作检索质量结论。")
    return notes


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="seekdb 检索质量评测")
    ap.add_argument("--qrels", required=True)
    ap.add_argument("--table", default=db.DEFAULT_TABLE)
    ap.add_argument("--backend", default="local", choices=["local", "api", "seekdb"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--dim", type=int, default=emb.DIM_DEFAULT)
    ap.add_argument("--strategies", default=",".join(ALL_STRATEGIES))
    ap.add_argument("--k", type=int, default=10, help="指标截断位置")
    ap.add_argument("--depth", type=int, default=50, help="每条召回腿取回的候选数（>= k）")
    ap.add_argument("--rrf-k0", dest="rrf_k0", type=float, default=RRF_K0)
    ap.add_argument("--w-vec", dest="w_vec", type=float, default=0.5,
                    help="向量腿权重，全文腿用 1-它")
    ap.add_argument("--boolean-mode", action="store_true", help="全文腿用 boolean mode")
    ap.add_argument("--filter", default=None, help="标量过滤，如 category=hybrid")
    ap.add_argument("--only-tag", dest="only_tag", default=None, help="只用某个 tag 的查询")
    ap.add_argument("--out", default="report", help="输出 report.md / report.json 的前缀")
    args = ap.parse_args()
    args.table = db.check_identifier(args.table, "表名")

    if args.depth < args.k:
        raise SystemExit("--depth 必须 >= --k，否则混合检索的候选池被截断")

    qrels = read_qrels(args.qrels)
    out_prefix = Path(args.out)
    if out_prefix.parent and not out_prefix.parent.exists():
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    start = time.time()
    summary, detail, meta = run(conn, args, qrels)
    elapsed = round(time.time() - start, 2)
    notes = diagnose(summary, detail, args, meta) if summary else []

    table_md = to_markdown(summary, args)
    print(table_md)
    print(f"\nqueries={len(detail)} table={args.table} depth={args.depth} k={args.k} "
          f"backend={args.backend} filter={args.filter or '-'} seconds={elapsed}")
    if summary:
        best = max(summary, key=lambda n: summary[n].get(f"recall@{args.k}") or 0)
        print(f"best on recall@{args.k}: {best}")
        print("\n## 诊断")
        for note in notes:
            print(f"- {note}")
        misses = [d for d in detail
                  if d.get("n_relevant") and d.get(best, {}).get("first_rank") is None]
        if misses:
            print("\n## 完全漏召的查询")
            for row in misses:
                print(f"- {row['query_id']} {row['query']} 应命中 {row['n_relevant']} 篇")

    Path(f"{args.out}.json").write_text(json.dumps(
        {"args": vars(args), "meta": meta, "summary": summary, "detail": detail,
         "diagnosis": notes, "seconds": elapsed}, ensure_ascii=False, indent=2), encoding="utf-8")
    body = table_md + f"\n\nqueries={len(detail)} depth={args.depth} table={args.table} " \
        f"seconds={elapsed}\n"
    if notes:
        body += "\n## 诊断\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    Path(f"{args.out}.md").write_text(body, encoding="utf-8")
    print(f"\n写出 {args.out}.md / {args.out}.json")


if __name__ == "__main__":
    main()
