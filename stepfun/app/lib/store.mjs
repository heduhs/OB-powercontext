import {
  SeekdbClient,
  SeekdbAdminClient,
  Schema,
  VectorIndexConfig,
  FulltextIndexConfig,
} from "seekdb";
import { config } from "./config.mjs";

const COLLECTION_PREFIX = /^kb_[a-z0-9_]{1,48}$/;

/** seekdb interpolates the text leg into `match(...) against('<text>')`, so keep word characters only. */
export function sanitizeTextLeg(text, maxLen = 160) {
  return String(text || "")
    .replace(/[^\p{Script=Han}A-Za-z0-9\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLen);
}

function assertCollectionName(name) {
  if (typeof name !== "string" || !COLLECTION_PREFIX.test(name)) {
    throw new Error(
      `invalid collection name "${name}" (expected kb_[a-z0-9_]{1,48})`
    );
  }
  return name;
}

function clientArgs() {
  if (config.dbMode === "embedded") {
    return { path: config.db.path, database: config.db.database, queryTimeout: config.queryTimeout };
  }
  return {
    host: config.db.host,
    port: config.db.port,
    user: config.db.user,
    password: config.db.password,
    database: config.db.database,
    queryTimeout: config.queryTimeout,
  };
}

export class SeekdbStore {
  constructor(opts = {}) {
    this.opts = opts;
    this.client = null;
    this.cache = new Map();
    this.info = null;
  }

  async connect() {
    if (this.client) return this;
    const admin = new SeekdbAdminClient(clientArgs());
    try {
      await admin.createDatabase(config.db.database);
    } catch (err) {
      // seekdb reports "already exists" as an error; tolerate it.
      if (!/exist/i.test(String(err && err.message))) throw err;
    } finally {
      await admin.close();
    }

    const client = new SeekdbClient(clientArgs());
    const rows = await client.execute("select version() as version");
    this.info = {
      mode: config.dbMode,
      database: config.db.database,
      serverVersion: rows?.[0]?.version ?? null,
      dimension: config.dimension,
      distance: config.distance,
      analyzer: config.analyzer,
    };
    this.client = client;
    return this;
  }

  async close() {
    if (!this.client) return;
    await this.client.close();
    this.client = null;
    this.cache.clear();
  }

  buildSchema() {
    return new Schema({
      vectorIndex: new VectorIndexConfig({
        hnsw: { dimension: config.dimension, distance: config.distance },
        // Vectors are produced in the browser; the SDK must not embed anything.
        embeddingFunction: null,
      }),
      fulltextIndex: new FulltextIndexConfig(config.analyzer, { ik_mode: config.ikMode }),
    });
  }

  async collection(name) {
    assertCollectionName(name);
    if (this.cache.has(name)) return this.cache.get(name);
    await this.connect();
    const col = await this.client.getOrCreateCollection({
      name,
      schema: this.buildSchema(),
      embeddingFunction: null,
    });
    this.cache.set(name, col);
    return col;
  }

  /** @param {{id:string,text:string,embedding:number[],metadata:object}[]} rows */
  async add(name, rows) {
    const col = await this.collection(name);
    const out = [];
    for (let i = 0; i < rows.length; i += config.maxBatchRows) {
      const part = rows.slice(i, i + config.maxBatchRows);
      await col.add({
        ids: part.map((r) => r.id),
        documents: part.map((r) => r.text),
        embeddings: part.map((r) => r.embedding),
        metadatas: part.map((r) => r.metadata || {}),
      });
      out.push(part.length);
    }
    return { written: rows.length, batches: out };
  }

  async refresh(name) {
    const col = await this.collection(name);
    await col.refresh_index();
  }

  async count(name) {
    const col = await this.collection(name);
    return col.count();
  }

  async peek(name, limit = 5) {
    const col = await this.collection(name);
    return col.peek(limit);
  }

  async listCollections() {
    await this.connect();
    const cols = await this.client.listCollections({ withEmbeddingFunction: false });
    const out = [];
    for (const c of cols) {
      let rows = null;
      try {
        rows = await c.count();
      } catch {
        rows = null;
      }
      out.push({ name: c.name, dimension: c.dimension, distance: c.distance, rows });
    }
    return out;
  }

  async dropCollection(name) {
    assertCollectionName(name);
    await this.connect();
    this.cache.delete(name);
    await this.client.deleteCollection(name);
  }

  async getByIds(name, ids) {
    if (!ids.length) return { ids: [], documents: [], metadatas: [] };
    const col = await this.collection(name);
    return col.get({ ids, include: ["documents", "metadatas"] });
  }

  /**
   * Hybrid retrieval. Both legs and the RRF fusion run inside seekdb:
   * the SDK serialises a search body, DBMS_HYBRID_SEARCH.GET_SQL() builds the
   * fused SQL server-side and the SDK only executes what the database returned.
   */
  async search({ name, mode = "hybrid", keywords, queryEmbedding, topK = 10, rrfConstant = 60, rrfWindowSize = 60 }) {
    const col = await this.collection(name);
    const include = ["documents", "metadatas", "distances"];
    if (mode !== "text" && (!Array.isArray(queryEmbedding) || queryEmbedding.length !== config.dimension)) {
      throw new Error(
        `query embedding must carry ${config.dimension} dims (got ${queryEmbedding?.length})`
      );
    }
    const textLeg = sanitizeTextLeg(keywords);

    if (mode === "vector") {
      const r = await col.query({ queryEmbeddings: [queryEmbedding], nResults: topK, include });
      return normalize(r);
    }
    if (mode === "text") {
      const r = await col.hybridSearch({
        query: { whereDocument: { $contains: textLeg } },
        nResults: topK,
        include,
      });
      return normalize(r);
    }
    const r = await col.hybridSearch({
      query: textLeg ? { whereDocument: { $contains: textLeg }, nResults: topK } : undefined,
      knn: { queryEmbeddings: [queryEmbedding], nResults: Math.max(topK, rrfWindowSize) },
      rank: { rrf: { rankConstant: rrfConstant, rankWindowSize: rrfWindowSize } },
      nResults: topK,
      include,
    });
    return normalize(r);
  }

  /**
   * Statements the database itself recorded, used as evidence that the two
   * retrieval legs and the RRF fusion are executed by seekdb (not the client).
   */
  async recentStatements(limit = 8) {
    try {
      const rows = await this.client.execute(
        `select query_sql from oceanbase.gv$ob_sql_audit
          where is_inner_sql = 0 order by request_time desc limit ?`,
        [limit]
      );
      return (rows || [])
        .map((r) => String(Object.values(r)[0] || "").replace(/\s+/g, " ").replace(/\[-?[\d., eE+-]{500,}\]/g, "[<query vector>]"))
        .filter((s) => s && !/ob_sql_audit/i.test(s) && !/^show tables/i.test(s) && !/sdk_collections/i.test(s))
        .map((s) => (s.length > 600 ? s.slice(0, 600) + " …" : s))
        .slice(0, 3);
    } catch {
      return [];
    }
  }
}

function normalize(r) {
  const first = (arr) => (arr && arr[0] ? arr[0] : []);
  return {
    ids: first(r.ids),
    documents: first(r.documents),
    metadatas: first(r.metadatas),
    distances: first(r.distances),
  };
}
