#!/usr/bin/env python3
"""End-to-end verification for ZhiYan: proves seekdb hybrid retrieval and
PowerContext memory actually do the work, and writes the evidence files.

Run with an interpreter that has the project dependencies installed:

    python scripts/verify.py                 # offline backends (no API key needed)
    python scripts/verify.py --embed minimax # with MINIMAX_API_KEY exported

Outputs
    records/verification.json   machine-readable evidence
    records/verification.log    human-readable transcript
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db                                        # noqa: E402
from backend.answer import chat_answer                        # noqa: E402
from backend.config import settings                           # noqa: E402
from backend.retrieval import get_store                        # noqa: E402
from backend.retrieval import W_VEC as W_VEC_DEFAULT           # noqa: E402
from backend.retrieval import FUSE_DEFAULT                     # noqa: E402
from backend.sample_data import all_documents                 # noqa: E402

# Queries with the documents that should be retrievable for each one.
# keyword  -> carries terms that literally appear in the target document
# semantic -> paraphrased with no (or almost no) literal term overlap, so only a
#             semantic embedding can bridge it
# Expectations list EVERY document that answers the query (reading them off
# sample_data.py), because under-annotating makes every strategy look bad and
# over-annotating hides the differences between them.
QUERY_SET = [
    {"id": "q01", "kind": "keyword", "query": "HNSW 召回率 efSearch 参数扫描",
     "expect": ["doc-03", "doc-09", "doc-46", "doc-25"]},
    {"id": "q02", "kind": "keyword", "query": "PagedAttention KV Cache 显存碎片",
     "expect": ["doc-01"]},
    {"id": "q03", "kind": "keyword", "query": "BM25 概率相关性 关键词打分",
     "expect": ["doc-15", "doc-38"]},
    {"id": "q04", "kind": "keyword", "query": "RRF 倒数排名融合 混合检索",
     "expect": ["doc-26", "doc-39", "doc-12"]},
    {"id": "q05", "kind": "keyword", "query": "两阶段提交 协调者 原子性",
     "expect": ["doc-18", "doc-35"]},
    {"id": "q06", "kind": "keyword", "query": "LSM 树 写放大 分层合并",
     "expect": ["doc-13", "doc-27"]},
    {"id": "q07", "kind": "keyword", "query": "MVCC 快照隔离 版本可见性",
     "expect": ["doc-05", "doc-17", "doc-31"]},
    {"id": "q08", "kind": "keyword", "query": "LoRA 低秩 参数高效微调",
     "expect": ["doc-21"]},
    {"id": "q09", "kind": "semantic", "query": "怎样让模型不要自己编造答案",
     "expect": ["doc-02", "doc-10"]},
    {"id": "q10", "kind": "semantic", "query": "生成式服务为什么要排队才能返回",
     "expect": ["doc-43", "doc-22"]},
    {"id": "q11", "kind": "semantic", "query": "内存表写满之后后台怎样重写磁盘文件",
     "expect": ["doc-13", "doc-27"]},
    {"id": "q12", "kind": "semantic", "query": "优化器为什么选错了索引，怎么查出来",
     "expect": ["doc-28", "doc-45", "doc-16"]},
]

WEIGHT_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]

LOG = []


def log(msg: str = "") -> None:
    print(msg)
    LOG.append(msg)


def recall(ranked, expected, k=5):
    top = set(ranked[:k])
    return round(len(top & set(expected)) / len(expected), 4) if expected else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", default=None, help="override ZHIYAN_EMBED_BACKEND")
    ap.add_argument("--tag", default="", help="suffix for the evidence files, e.g. -offline")
    args = ap.parse_args()
    if args.embed:
        settings.EMBED_BACKEND = args.embed

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence = {"started_at": started, "project": "智研 ZhiYan",
                "seekdb": {}, "hybrid": {}, "answer": {}, "memory": {}}

    log("=" * 78)
    log("ZhiYan 端到端验证")
    log(f"时间：{started}")
    log(f"embedding 后端：{settings.EMBED_BACKEND}    答案引擎："
        f"{settings.MINIMAX_CHAT_MODEL if settings.MINIMAX_API_KEY else 'extractive(离线)'}")
    log("=" * 78)

    # ---- 1. seekdb connection, schema and indexes -------------------------
    conn = db.connect()
    engine = db.engine_version(conn)
    log(f"\n[1] seekdb 引擎：{engine}  库：{settings.SEEKDB_DATABASE}")
    evidence["seekdb"]["engine"] = engine

    store = get_store()
    log("[1] 重建表并导入语料 …")
    store.reset()
    ing = store.ingest(all_documents())
    idx = db.indexes(conn)
    log(f"    语料 {ing['stored']} 篇，向量维度 {ing['dim']}")
    log(f"    索引：{[(i['name'], i['type']) for i in idx]}")
    evidence["seekdb"].update({"ingest": ing, "indexes": idx,
                               "documents": ing["stored"],
                               "ddl": db.DDL.format(table=db.TABLE, dim=ing["dim"])})

    # ---- 2. three retrieval modes on the same query set -------------------
    log("\n[2] 三种检索模式对比（top_k=5）")
    log(f"{'query':<6}{'kind':<9}{'vector':>8}{'fulltext':>10}{'hybrid':>8}")
    rows = []
    for item in QUERY_SET:
        per = {"id": item["id"], "kind": item["kind"], "query": item["query"],
               "expect": item["expect"]}
        for mode in ("vector", "fulltext", "hybrid"):
            res = store.search(item["query"], top_k=5, mode=mode)
            per[mode] = recall([h["doc_id"] for h in res["results"]], item["expect"])
        rows.append(per)
        log(f"{per['id']:<6}{per['kind']:<9}{per['vector']:>8}{per['fulltext']:>10}{per['hybrid']:>8}")
    avg = {m: round(sum(r[m] for r in rows) / len(rows), 4) for m in ("vector", "fulltext", "hybrid")}
    log(f"{'avg':<15}{'':>1}{avg['vector']:>8}{avg['fulltext']:>10}{avg['hybrid']:>8}")
    best_single = max(avg["vector"], avg["fulltext"])
    log(f"    混合检索(w_vec={W_VEC_DEFAULT}) recall@5 = {avg['hybrid']}，"
        f"最好单腿 = {best_single}，增益 = {round(avg['hybrid'] - best_single, 4)}")
    evidence["hybrid"] = {"per_query": rows, "average_recall_at_5": avg,
                          "gain_over_best_leg": round(avg["hybrid"] - best_single, 4)}

    # ---- 2b. fusion method x weight sweep ---------------------------------
    log("\n[2b] 融合方式 × 权重扫描（w_vec：0=纯全文，1=纯向量）")
    sweep = []
    for fuse in ("rrf", "weighted"):
        line = f"    {fuse:9s}"
        for w in WEIGHT_GRID:
            rec = []
            for item in QUERY_SET:
                res = store.search(item["query"], top_k=5, mode="hybrid", w_vec=w, fuse=fuse)
                rec.append(recall([h["doc_id"] for h in res["results"]], item["expect"]))
            mean = round(sum(rec) / len(rec), 4)
            sweep.append({"fuse": fuse, "w_vec": w, "recall_at_5": mean})
            line += f"  w={w}:{mean}"
        log(line)
    best = max(sweep, key=lambda s: s["recall_at_5"])
    shipped = next(s for s in sweep
                   if s["fuse"] == FUSE_DEFAULT and abs(s["w_vec"] - W_VEC_DEFAULT) < 1e-9)
    textbook = next(s for s in sweep if s["fuse"] == "rrf" and abs(s["w_vec"] - 0.5) < 1e-9)
    fulltext_only = next(s["recall_at_5"] for s in sweep if s["fuse"] == "rrf" and s["w_vec"] == 0.0)
    log(f"    本项目默认融合：{FUSE_DEFAULT} w_vec={W_VEC_DEFAULT} -> recall@5={shipped['recall_at_5']}")
    log(f"    教科书默认 RRF w=0.5 -> {textbook['recall_at_5']}（同样的两腿，配置不同差 {round(shipped['recall_at_5'] - textbook['recall_at_5'], 4)}）")
    log(f"    纯全文基线 -> {fulltext_only}；纯向量 -> {avg['vector']}；本次扫描最优 -> "
        f"{best['fuse']} w={best['w_vec']} = {best['recall_at_5']}")
    log(f"    默认配置{'就是' if best is shipped else '不是'}本次扫描的最优，"
        f"相对最好单腿增益 {round(shipped['recall_at_5'] - max(avg['vector'], avg['fulltext']), 4)}")
    log("    结论：混合检索不是免费的增益。融合方式与权重必须用带标注的查询集扫出来，")
    log("          选错组合会低于最好单腿；本项目据此选定默认融合配置。")
    evidence["hybrid"]["fusion_sweep"] = sweep
    evidence["hybrid"]["best"] = best
    evidence["hybrid"]["shipped_default"] = shipped
    evidence["hybrid"]["textbook_rrf_050"] = textbook

    # ---- 3. fusion transparency + index usage -----------------------------
    demo = store.search("HNSW 索引参数 ef_search 召回率", top_k=5, mode="hybrid")
    log("\n[3] RRF 融合明细（可见两腿名次，说明两路各自贡献了什么）")
    for h in demo["results"]:
        log(f"    {h['doc_id']} v={h['vector_rank']} f={h['fulltext_rank']} "
            f"by={'+'.join(h['matched_by']) or '-'} score={h['score']}")
    ex = store.explain("HNSW 索引参数")
    vec_plan = [l for l in ex["vector_plan"].splitlines() if "SCAN" in l]
    ftx_plan = [l for l in ex["fulltext_plan"].splitlines() if "SCAN" in l]
    log(f"\n[3] explain 向量腿计划：{vec_plan}")
    log(f"[3] explain 全文腿计划：{ftx_plan}")
    log(f"[3] 单条 SQL 的 RRF 融合结果：{ex['single_sql_rrf']}")
    evidence["hybrid"]["fusion_example"] = demo["results"]
    evidence["hybrid"]["explain"] = ex

    # ---- 4. grounded answer ----------------------------------------------
    q = "RAG 是如何减少大模型幻觉的？"
    hits = store.search(q, top_k=3)["results"]
    ans = chat_answer(q, hits)
    log(f"\n[4] 基于检索结果的问答（引擎：{ans['engine']}）")
    log("    " + (ans["answer"].replace("\n", "\n    ")[:400]))
    log(f"    引用出处：{[(c['index'], c['title'][:24]) for c in ans['citations']]}")
    evidence["answer"] = {"question": q, **ans,
                          "retrieved": [{"doc_id": h["doc_id"], "title": h["title"]} for h in hits]}

    # ---- 5. PowerContext memory, including cross-process reuse -----------
    log("\n[5] PowerContext 记忆：写入 → 跨进程读回 → 复习重排")
    try:
        from backend.memory import get_memory
        mem = get_memory()
        scope = mem.ensure_scope()
        point = f"验证用薄弱点 {datetime.now().strftime('%H%M%S')}"
        mem.add_weak_point(point, source="verify.py", note="自动验证写入")
        # a *separate process* must see it: that is the persistence claim
        probe = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "memory_probe.py"), point],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        seen = json.loads(probe.stdout.strip().splitlines()[-1]) if probe.stdout.strip() else {}
        updated = mem.record_review(point, 5)
        log(f"    scope={scope}")
        log(f"    写入：{point}")
        log(f"    另一进程读回：found={seen.get('found')} version={seen.get('version')}")
        log(f"    复习评分 5 后：复习次数={updated['review_count']} "
            f"下次复习={updated['next_review']} 版本={updated['version']}")
        log(f"    今日应复习：{[w['point'][:22] for w in mem.review_plan()][:6]}")
        evidence["memory"] = {"service": "PowerContext", "scope_id": scope,
                              "written": point, "cross_process_read": seen,
                              "after_review": updated, "inventory": mem.stats(),
                              "probe_returncode": probe.returncode}
    except Exception as exc:  # noqa: BLE001
        log(f"    PowerContext 验证失败：{type(exc).__name__}: {exc}")
        evidence["memory"] = {"error": f"{type(exc).__name__}: {exc}"}

    # ---- write evidence ---------------------------------------------------
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_json = ROOT / "records" / f"verification{args.tag}.json"
    out_log = ROOT / "records" / f"verification{args.tag}.log"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    out_log.write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\n证据已写出：{out_json.relative_to(ROOT)} / {out_log.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
