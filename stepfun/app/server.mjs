import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config, describeConfig } from "./lib/config.mjs";
import { SeekdbStore } from "./lib/store.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(here, "public");
const MODELS_DIR = path.join(here, "models");
const VENDOR_DIR = path.join(here, "vendor");
const DATA_DIR = path.join(here, "data");
const EVAL_DIR = path.join(here, "eval");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".onnx": "application/octet-stream",
  ".wasm": "application/wasm",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".map": "application/json; charset=utf-8",
};

const MAX_JSON_BYTES = 64 * 1024 * 1024; // corpora carry float vectors
const store = new SeekdbStore();

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", ...headers });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
};

const fail = (res, status, message, extra = {}) => send(res, status, { error: message, ...extra });

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_JSON_BYTES) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      try {
        resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {});
      } catch (e) {
        reject(new Error("invalid JSON body: " + e.message));
      }
    });
    req.on("error", reject);
  });
}

function safeJoin(root, urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const target = path.resolve(root, "." + (decoded === "/" ? "" : decoded));
  const rel = path.relative(root, target);
  if (rel.startsWith("..") || path.isAbsolute(rel)) return null;
  return target;
}

function mimeFor(target) {
  const ext = path.extname(target).toLowerCase();
  // mirrored ESM files keep jsdelivr's URL shape and often have no usable extension
  if (target.includes(`${path.sep}vendor${path.sep}esm${path.sep}`) || target.includes("/vendor/esm/")) {
    return "text/javascript; charset=utf-8";
  }
  if (MIME[ext]) return MIME[ext];
  return ext ? "application/octet-stream" : "text/javascript; charset=utf-8";
}

async function serveStatic(res, root, urlPath) {
  const target = safeJoin(root, urlPath);
  if (!target) return fail(res, 403, "forbidden");
  let st;
  try {
    st = await fsp.stat(target);
  } catch {
    return fail(res, 404, "not found: " + urlPath);
  }
  if (st.isDirectory()) return fail(res, 404, "not a file");
  res.writeHead(200, {
    "content-type": mimeFor(target),
    "content-length": String(st.size),
    "cache-control": "no-cache",
  });
  fs.createReadStream(target).pipe(res);
}

const validRows = (rows) =>
  Array.isArray(rows) &&
  rows.every(
    (r) =>
      r &&
      typeof r.id === "string" &&
      r.id.length > 0 &&
      r.id.length <= 128 &&
      typeof r.text === "string" &&
      Array.isArray(r.embedding)
  );

async function api(req, res, pathname, query) {
  const explain = query.get("explain") === "1";

  if (pathname === "/api/health" && req.method === "GET") {
    let db = { connected: false };
    try {
      await store.connect();
      db = { connected: true, ...store.info, collections: await store.listCollections() };
    } catch (e) {
      db = { connected: false, error: String(e.message || e) };
    }
    return send(res, 200, {
      app: describeConfig(),
      db,
      models: {
        local: fs.existsSync(MODELS_DIR),
        files: fs.existsSync(MODELS_DIR) ? listModels() : [],
      },
      vendor: fs.existsSync(VENDOR_DIR),
    });
  }

  if (pathname === "/api/collections" && req.method === "GET") {
    await store.connect();
    return send(res, 200, { collections: await store.listCollections() });
  }

  if (pathname === "/api/ingest" && req.method === "POST") {
    const body = await readJson(req);
    const { collection, rows, refresh = true } = body;
    if (!validRows(rows)) return fail(res, 400, "rows must be [{id,text,embedding[],metadata?}]");
    const bad = rows.find((r) => r.embedding.length !== config.dimension);
    if (bad) return fail(res, 400, `embedding for ${bad.id} has ${bad.embedding.length} dims, expected ${config.dimension}`);
    const t0 = performance.now();
    await store.connect();
    const written = rows.length ? await store.add(collection, rows) : { written: 0, batches: [] };
    const tWrite = performance.now() - t0;
    let tRefresh = 0;
    if (refresh) {
      const t1 = performance.now();
      await store.refresh(collection);
      tRefresh = performance.now() - t1;
    }
    const total = await store.count(collection);
    return send(res, 200, {
      collection,
      written: written.written,
      batches: written.batches.length,
      total,
      timing_ms: { write: round(tWrite), refresh: round(tRefresh) },
    });
  }

  if (pathname === "/api/refresh" && req.method === "POST") {
    const { collection } = await readJson(req);
    const t0 = performance.now();
    await store.connect();
    await store.refresh(collection);
    return send(res, 200, { collection, timing_ms: { refresh: round(performance.now() - t0) } });
  }

  if (pathname === "/api/search" && req.method === "POST") {
    const body = await readJson(req);
    const {
      collection,
      mode = "hybrid",
      keywords,
      queryEmbedding,
      topK = 10,
      rrfConstant = 60,
      rrfWindowSize = 60,
    } = body;
    if (!["hybrid", "vector", "text"].includes(mode)) return fail(res, 400, "mode must be hybrid|vector|text");
    if (mode !== "text" && !Array.isArray(queryEmbedding)) return fail(res, 400, "queryEmbedding[] is required");
    if (typeof topK !== "number" || topK < 1 || topK > 50) return fail(res, 400, "topK must be 1..50");

    const t0 = performance.now();
    await store.connect();
    const result = await store.search({
      name: collection,
      mode,
      keywords,
      queryEmbedding: Array.isArray(queryEmbedding) ? queryEmbedding : undefined,
      topK: Math.round(topK),
      rrfConstant,
      rrfWindowSize,
    });
    const took = performance.now() - t0;
    const payload = { collection, mode, took_ms: round(took), ...result };
    if (explain) payload.evidence = await store.recentStatements();
    return send(res, 200, payload);
  }

  if (pathname === "/api/documents" && req.method === "POST") {
    const { collection, ids } = await readJson(req);
    if (!Array.isArray(ids) || ids.length > 200) return fail(res, 400, "ids[] (max 200) required");
    await store.connect();
    return send(res, 200, await store.getByIds(collection, ids));
  }

  if (pathname === "/api/peek" && req.method === "POST") {
    const { collection, limit = 5 } = await readJson(req);
    await store.connect();
    return send(res, 200, await store.peek(collection, Math.min(Number(limit) || 5, 50)));
  }

  if (pathname === "/api/reset" && req.method === "POST") {
    const { collection } = await readJson(req);
    await store.connect();
    await store.dropCollection(collection);
    return send(res, 200, { collection, dropped: true });
  }

  if (pathname === "/api/record" && req.method === "POST") {
    if (!config.allowRecordWrite) return fail(res, 403, "record writing disabled");
    const { name, payload } = await readJson(req);
    if (!/^[a-z0-9][a-z0-9._-]{0,80}\.json$/.test(String(name))) {
      return fail(res, 400, "name must look like bench-2026-10-28.json");
    }
    await fsp.mkdir(config.recordsDir, { recursive: true });
    const file = path.join(config.recordsDir, name);
    await fsp.writeFile(file, JSON.stringify(payload, null, 2), "utf8");
    return send(res, 200, { saved: path.basename(file), bytes: JSON.stringify(payload).length });
  }

  return fail(res, 404, "unknown api " + pathname);
}

function listModels() {
  const out = [];
  const walk = (dir, prefix) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${ent.name}` : ent.name;
      if (ent.isDirectory()) walk(path.join(dir, ent.name), rel);
      else out.push({ file: rel, bytes: fs.statSync(path.join(dir, ent.name)).size });
    }
  };
  walk(MODELS_DIR, "");
  return out;
}

const round = (n) => Math.round(n * 100) / 100;

const server = http.createServer(async (req, res) => {
  res.setHeader("cross-origin-resource-policy", "same-origin");
  res.setHeader("permissions-policy", "unrestricted=*");
  // Cross-origin isolation enables SharedArrayBuffer => multi-threaded WASM inference.
  // Only safe once every asset is served from this origin (npm run setup has been run).
  if (fs.existsSync(MODELS_DIR) && fs.existsSync(VENDOR_DIR)) {
    res.setHeader("cross-origin-opener-policy", "same-origin");
    res.setHeader("cross-origin-embedder-policy", "require-corp");
  }

  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const p = url.pathname;
  try {
    if (p.startsWith("/api/")) return await api(req, res, p, url.searchParams);
    if (p.startsWith("/models/")) return await serveStatic(res, MODELS_DIR, p.slice("/models".length));
    if (p.startsWith("/vendor/")) return await serveStatic(res, VENDOR_DIR, p.slice("/vendor".length));
    if (p.startsWith("/data/")) return await serveStatic(res, DATA_DIR, p.slice("/data".length));
    if (p.startsWith("/eval/")) return await serveStatic(res, EVAL_DIR, p.slice("/eval".length));
    if (req.method !== "GET" && req.method !== "HEAD") return fail(res, 405, "method not allowed");
    return await serveStatic(res, PUBLIC_DIR, p === "/" ? "/index.html" : p);
  } catch (err) {
    return fail(res, 500, String(err && err.stack ? err.stack.split("\n").slice(0, 3).join(" | ") : err));
  }
});

server.listen(config.port, config.host, () => {
  console.log(`[edgekb] http://${config.host}:${config.port}  seekdb=${config.dbMode} db=${config.db.database}`);
});

const shutdown = async () => {
  try {
    await store.close();
  } catch {
    /* already closed */
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
