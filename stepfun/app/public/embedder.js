// Shared client helpers: worker facade, chunking, seekdb REST calls.

let nextId = 1;
const pending = new Map();

export class Embedder {
  constructor(url = "/embedder.worker.js") {
    this.worker = new Worker(url, { type: "module" });
    this.worker.onmessage = (ev) => {
      const { id, ok, result, error } = ev.data;
      const p = pending.get(id);
      if (!p) return;
      pending.delete(id);
      ok ? p.resolve(result) : p.reject(new Error(error));
    };
    this.worker.onerror = (e) => {
      for (const p of pending.values()) p.reject(new Error("worker error: " + e.message));
      pending.clear();
    };
    this.session = null;
  }

  call(cmd, payload) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, cmd, payload });
    });
  }

  async init(opts) {
    this.session = await this.call("init", opts);
    return this.session;
  }

  /** Embed a list of texts, split into batches; returns vectors + per-batch timings. */
  async embedBatched(texts, batchSize = 16, onProgress) {
    const vectors = [];
    const batches = [];
    for (let i = 0; i < texts.length; i += batchSize) {
      const part = texts.slice(i, i + batchSize);
      const r = await this.call("embed", { texts: part });
      vectors.push(...r.vectors);
      batches.push({ n: part.length, ms: r.ms, chars: r.chars });
      onProgress?.(vectors.length, texts.length, r.ms);
    }
    const totalMs = batches.reduce((a, b) => a + b.ms, 0);
    const totalChars = batches.reduce((a, b) => a + b.chars, 0);
    return {
      vectors,
      batches,
      timing: {
        total_ms: Math.round(totalMs),
        chunks_per_s: totalMs > 0 ? Math.round((batches.length ? vectors.length : 0) / (totalMs / 1000)) : null,
        chars_per_s: totalMs > 0 ? Math.round(totalChars / (totalMs / 1000)) : null,
        first_batch_ms: batches[0]?.ms ?? null,
        warm_batch_ms: median(batches.slice(1).map((b) => b.ms)),
      },
    };
  }

  embedQueries(texts, opts = {}) {
    const prefix = opts.prefix ?? "";
    return this.call("embed", { texts: texts.map((t) => prefix + t), pooling: opts.pooling });
  }

  async status() {
    this.session = (await this.call("status")) || this.session;
    return this.session;
  }

  dispose() {
    this.worker.terminate();
  }
}

/** Split a document into overlapping chunks, preferring paragraph boundaries. */
export function chunkText(text, size = 320, overlap = 48) {
  const clean = text.replace(/\r\n/g, "\n").trim();
  if (!clean) return [];
  const paras = clean.split(/\n{2,}|\n(?=[#\-*>]|[0-9]+\.)|(?<=[。！？；!?;])\n?/).filter((p) => p.trim().length > 0);
  const chunks = [];
  let buf = "";
  const flush = () => {
    const t = buf.trim();
    if (t) chunks.push(t);
    buf = "";
  };
  const pushLong = (t) => {
    let start = 0;
    while (start < t.length) {
      const cut = Math.min(start + size, t.length);
      const piece = t.slice(start, cut).trim();
      if (piece) chunks.push(piece);
      if (cut >= t.length) break;
      start = Math.max(start + 1, cut - overlap);
    }
  };
  for (const para of paras) {
    const p = para.trim();
    if (p.length > size) {
      flush();
      pushLong(p);
      continue;
    }
    if ((buf + "\n" + p).trim().length > size) flush();
    buf = buf ? buf + "\n" + p : p;
  }
  flush();
  return chunks.length ? chunks : [clean.slice(0, size)];
}

export const median = (arr) => {
  const a = (arr || []).filter((x) => typeof x === "number" && isFinite(x)).sort((x, y) => x - y);
  if (!a.length) return null;
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : Math.round(((a[m - 1] + a[m]) / 2) * 100) / 100;
};

export function p95(arr) {
  const a = (arr || []).filter((x) => isFinite(x)).sort((x, y) => x - y);
  if (!a.length) return null;
  return a[Math.min(a.length - 1, Math.floor(a.length * 0.95))];
}

export async function api(path, body, opts = {}) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(opts.signal ? { signal: opts.signal } : {}),
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error(`${path} 返回非 JSON (${res.status}): ${text.slice(0, 200)}`);
  }
  if (!res.ok || json.error) throw new Error(`${path} 失败 (${res.status}): ${json.error || text.slice(0, 200)}`);
  return json;
}

/**
 * The full-text leg is embedded by seekdb into `match(...) against('<text>')`,
 * so only word characters survive a sanitising pass; quotes/backslash/; are dropped.
 */
export function textLeg(query, maxLen = 160) {
  return String(query || "")
    .replace(/[^\p{Script=Han}A-Za-z0-9\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLen);
}
