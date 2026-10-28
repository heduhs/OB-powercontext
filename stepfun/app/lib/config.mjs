import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const APP_ROOT = path.resolve(here, "..");

const sdkVersion = (() => {
  try {
    return JSON.parse(fs.readFileSync(path.join(APP_ROOT, "node_modules", "seekdb", "package.json"), "utf8")).version;
  } catch {
    return "unknown";
  }
})();

const env = process.env;
const bool = (v, d) => (v === undefined ? d : /^(1|true|yes|on)$/i.test(String(v)));

export const config = {
  port: Number(env.PORT || 8630),
  host: env.HOST || "127.0.0.1",

  // seekdb: "server" (MySQL protocol) or "embedded" (native bindings, local dir)
  dbMode: env.SEEKDB_MODE || "server",
  db: {
    path: env.SEEKDB_PATH || path.join(APP_ROOT, ".data", "seekdb.db"),
    host: env.SEEKDB_HOST || "127.0.0.1",
    port: Number(env.SEEKDB_PORT || 2881),
    user: env.SEEKDB_USER || "root",
    password: env.SEEKDB_PASSWORD || "",
    database: env.SEEKDB_DATABASE || "stepfun_kb",
  },

  // collection schema defaults (bge-small-zh-v1.5 -> 512 dims)
  dimension: Number(env.KB_DIM || 512),
  distance: env.KB_DISTANCE || "cosine",
  analyzer: env.KB_ANALYZER || "ik",
  ikMode: env.KB_IK_MODE || "smart",

  // ingest
  maxBatchRows: Number(env.KB_MAX_BATCH_ROWS || 500),
  queryTimeout: Number(env.SEEKDB_QUERY_TIMEOUT || 60000),

  // evidence dump for the benchmark page
  recordsDir: env.KB_RECORDS_DIR || path.resolve(APP_ROOT, "..", "records"),
  allowRecordWrite: bool(env.KB_ALLOW_RECORD_WRITE, true),
};

export function describeConfig() {
  return {
    dbMode: config.dbMode,
    database: config.db.database,
    dimension: config.dimension,
    distance: config.distance,
    analyzer: config.analyzer,
    ikMode: config.ikMode,
    maxBatchRows: config.maxBatchRows,
    server: `${config.host}:${config.port}`,
    seekdbSdkVersion: sdkVersion,
    node: process.version,
  };
}
