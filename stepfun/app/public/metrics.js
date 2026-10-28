// Retrieval metrics over a ranked list of chunk hits, deduplicated to documents.

export function chunksToDocs(ids, metadatas) {
  const seen = new Map();
  const out = [];
  ids.forEach((id, i) => {
    const docId = (metadatas[i] && metadatas[i].doc_id) || String(id).split("#")[0];
    if (seen.has(docId)) return;
    seen.set(docId, i);
    out.push({ docId, rank: i, chunk: id });
  });
  return out;
}

export function recallAtK(rankedDocs, gold, k) {
  if (!gold.length) return null;
  const top = new Set(rankedDocs.slice(0, k).map((d) => d.docId));
  const hit = gold.filter((g) => top.has(g)).length;
  return hit / gold.length;
}

const log2 = (x) => Math.log2(x + 1);

export function ndcgAtK(rankedDocs, gold, k) {
  if (!gold.length) return null;
  const set = new Set(gold);
  let dcg = 0;
  rankedDocs.slice(0, k).forEach((d, i) => {
    if (set.has(d.docId)) dcg += 1 / log2(i + 2);
  });
  const idealN = Math.min(gold.length, k);
  let idcg = 0;
  for (let i = 0; i < idealN; i++) idcg += 1 / log2(i + 2);
  return idcg > 0 ? dcg / idcg : null;
}

export function mrr(rankedDocs, gold) {
  const set = new Set(gold);
  const i = rankedDocs.findIndex((d) => set.has(d.docId));
  return i < 0 ? 0 : 1 / (i + 1);
}

export function summarise(perQuery, ks = [1, 3, 5, 10]) {
  const rows = perQuery.filter((q) => q.gold && q.gold.length);
  const out = { queries: rows.length, corpus_chunks: null };
  for (const k of ks) out[`recall@${k}`] = round(avg(rows.map((q) => recallAtK(q.ranked, q.gold, k))), 4);
  out["ndcg@10"] = round(avg(rows.map((q) => ndcgAtK(q.ranked, q.gold, 10))), 4);
  out["mrr"] = round(avg(rows.map((q) => mrr(q.ranked, q.gold))), 4);
  out["hit@1"] = round(avg(rows.map((q) => (q.ranked[0] && q.gold.includes(q.ranked[0].docId) ? 1 : 0))), 4);
  return out;
}

const avg = (a) => (a.filter((x) => x !== null && isFinite(x)).length ? a.reduce((s, x) => s + (x || 0), 0) / a.filter((x) => x !== null && isFinite(x)).length : null);
const round = (n, p = 2) => (n === null || !isFinite(n) ? null : Math.round(n * 10 ** p) / 10 ** p);
export { avg, round };
