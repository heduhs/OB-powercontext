"""Retrieval metrics: recall@k, MRR, nDCG@k, precision@k.

Relevance is binary (a doc is relevant or it is not) as declared in qrels.
"""

from __future__ import annotations

import math


def recall_at_k(ranked, relevant, k):
    if not relevant:
        return None
    hits = len(set(ranked[:k]) & relevant)
    return hits / len(relevant)


def precision_at_k(ranked, relevant, k):
    if k <= 0:
        return None
    return len(set(ranked[:k]) & relevant) / k


def mrr(ranked, relevant, k=None):
    pool = ranked if k is None else ranked[:k]
    for pos, doc_id in enumerate(pool, start=1):
        if doc_id in relevant:
            return 1.0 / pos
    return 0.0


def dcg(ranked, relevant, k):
    score = 0.0
    for pos, doc_id in enumerate(ranked[:k], start=1):
        if doc_id in relevant:
            score += 1.0 / math.log2(pos + 1)
    return score


def ndcg_at_k(ranked, relevant, k):
    if not relevant:
        return None
    ideal = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(pos + 2) for pos in range(ideal))
    return dcg(ranked, relevant, k) / idcg if idcg else None


def hit_rate(ranked, relevant, k):
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def summarize(per_query, ks=(1, 5, 10)):
    """per_query: list of (ranked_doc_ids, relevant_set). Averages skip queries
    without annotations, matching standard qrels practice."""
    rows = {"n_queries": len(per_query)}
    for k in ks:
        rec = [recall_at_k(r, g, k) for r, g in per_query]
        prec = [precision_at_k(r, g, k) for r, g in per_query]
        hit = [hit_rate(r, g, k) for r, g in per_query]
        vals = [v for v in rec if v is not None]
        rows[f"recall@{k}"] = round(sum(vals) / len(vals), 4) if vals else None
        pv = [v for v in prec if v is not None]
        rows[f"precision@{k}"] = round(sum(pv) / len(pv), 4) if pv else None
        hv = [v for v in hit if v is not None]
        rows[f"hit@{k}"] = round(sum(hv) / len(hv), 4) if hv else None
    nd = [ndcg_at_k(r, g, 10) for r, g in per_query]
    vals = [v for v in nd if v is not None]
    rows["nDCG@10"] = round(sum(vals) / len(vals), 4) if vals else None
    mr = [mrr(r, g) for r, g in per_query]
    vals = [v for v in mr if v is not None]
    rows["MRR"] = round(sum(vals) / len(vals), 4) if vals else None
    empty = sum(1 for r, _g in per_query if not r)
    rows["empty_result_rate"] = round(empty / len(per_query), 4) if per_query else None
    return rows
