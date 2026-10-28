/**
 * Download the browser-side assets so the app runs fully on the local machine:
 *   - open-source ONNX embedding model files (fp32 / fp16 / int8)  -> app/models/<repo>/...
 *   - Transformers.js ESM bundle + ONNX Runtime Web wasm binaries   -> app/vendor/...
 *
 * Usage:
 *   node scripts/fetch-assets.mjs                      # model (q8+fp16) + vendor
 *   node scripts/fetch-assets.mjs --dtypes fp32,fp16,q8 --vendor
 *   node scripts/fetch-assets.mjs --endpoint https://hf-mirror.com   # mirror when huggingface.co is unreachable
 *
 * Every downloaded file is recorded with size + sha256 in ../records/assets-manifest.json,
 * which is also the model-size evidence used by the one-pager.
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..");
const modelsDir = path.join(appRoot, "models");
const vendorDir = path.join(appRoot, "vendor");
const recordsDir = path.resolve(appRoot, "..", "records");

const args = process.argv.slice(2);
const flag = (name, def) => {
  const i = args.indexOf("--" + name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith("--") ? args[i + 1] : (i >= 0 ? true : def);
};

const MODEL = flag("model", "Xenova/bge-small-zh-v1.5");
const ENDPOINT = (flag("endpoint", "https://huggingface.co") + "").replace(/\/$/, "");
const DTYPES = String(flag("dtypes", "q8,fp16")).split(",");
const WANT_VENDOR = flag("vendor", false) || args.includes("--vendor");
const DTYPE_SUFFIX = { fp32: "", fp16: "_fp16", q8: "_quantized", int8: "_int8" };

const sha256 = (buf) => crypto.createHash("sha256").update(buf).digest("hex");

async function download(url, dest, tries = 3) {
  for (let attempt = 1; attempt <= tries; attempt++) {
    try {
      const res = await fetch(url, { redirect: "follow" });
      if (res.status === 404) return { missing: true, status: 404 };
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = Buffer.from(await res.arrayBuffer());
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.writeFileSync(dest, buf);
      return { bytes: buf.length, sha256: sha256(buf) };
    } catch (e) {
      if (attempt === tries) throw new Error(`${url} 下载失败：${e.message}`);
      await new Promise((r) => setTimeout(r, 800 * attempt));
    }
  }
}

const manifest = { generated_at: new Date().toISOString(), model: MODEL, endpoint: ENDPOINT, files: {}, vendor: {} };

async function fetchModel() {
  const base = `${ENDPOINT}/${MODEL}/resolve/main`;
  const flat = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "README.md", "1_Pooling/config.json"];
  for (const f of flat) {
    const dest = path.join(modelsDir, MODEL, f);
    const r = await get(`${base}/${f}`, dest).catch((e) => ({ error: e.message }));
    if (r.missing) continue;
    manifest.files[f] = r;
    console.log(`  ${f}: ${r.error ? "ERR " + r.error : (r.bytes / 1024).toFixed(0) + " KB"}`);
  }
  for (const dtype of DTYPES) {
    const suffix = DTYPE_SUFFIX[dtype];
    if (suffix === undefined) {
      console.warn(`  忽略未知精度 ${dtype}`);
      continue;
    }
    const f = `onnx/model${suffix}.onnx`;
    const dest = path.join(modelsDir, MODEL, f);
    const r = await get(`${base}/${f}`, dest).catch((e) => ({ error: e.message }));
    manifest.files[f] = { ...r, dtype };
    console.log(`  ${f}: ${r.error ? "ERR " + r.error : (r.bytes / 1048576).toFixed(1) + " MB"}`);
  }
}

/* ---- mirror the ESM dependency chain so the browser needs no CDN ---- */

const CDN = "https://cdn.jsdelivr.net";
const seen = new Map(); // cdn url -> local relative path

function localNameFor(url) {
  const clean = url.replace(/^https?:\/\/cdn.jsdelivr.net\//, "").replace(/\//g, "_").replace(/[^A-Za-z0-9._@-]/g, "_");
  return clean.length > 120 ? clean.slice(-120) : clean;
}

async function mirrorEsm(cdnUrl) {
  const key = cdnUrl.replace(/^\/npm\//, CDN + "/npm/");
  if (seen.has(key)) return seen.get(key);
  const rel = `esm/${localNameFor(key)}`;
  seen.set(key, rel);
  const res = await fetch(key, { redirect: "follow" });
  if (!res.ok) throw new Error(`${key} -> HTTP ${res.status}`);
  let code = await res.text();
  const specs = [...code.matchAll(/(?:from\s*|import\s*\(\s*)["']([^"']+)["']/g)].map((m) => m[1]);
  for (const spec of specs) {
    if (!spec.startsWith("/npm/") && !spec.startsWith("https://cdn.jsdelivr.net/")) continue;
    const abs = spec.startsWith("/npm/") ? CDN + spec : spec;
    const childRel = await mirrorEsm(abs);
    code = code.split(spec).join("/vendor/" + childRel);
  }
  const dest = path.join(vendorDir, rel);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, code, "utf8");
  manifest.vendor[rel] = { bytes: Buffer.byteLength(code), source: key };
  return rel;
}

async function fetchVendor() {
  const entry = `${CDN}/npm/@huggingface/transformers@4.3.2/+esm`;
  const rel = await mirrorEsm(entry);
  const abs = path.join(vendorDir, rel);
  const wrapper = `export * from "/vendor/${rel}";\n`;
  fs.writeFileSync(path.join(vendorDir, "transformers.esm.js"), wrapper, "utf8");
  manifest.vendor["transformers.esm.js"] = { bytes: Buffer.byteLength(wrapper), entry };
  console.log(`  vendor: ${rel} (${(fs.statSync(abs).size / 1024).toFixed(0)} KB) + 依赖 ${seen.size - 1} 个`);

  const ortVersion = [...seen.keys()].map((u) => /onnxruntime-web@([^/]+)\//.exec(u)?.[1]).filter(Boolean)[0] || "1.22.0";
  manifest.vendor["onnxruntime-web"] = { version: ortVersion };
  for (const f of ["ort-wasm-simd-threaded.asyncify.mjs", "ort-wasm-simd-threaded.asyncify.wasm", "ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"]) {
    const r = await download(`${CDN}/npm/onnxruntime-web@${ortVersion}/dist/${f}`, path.join(vendorDir, "ort", f)).catch((e) => ({ error: e.message }));
    if (r.missing) continue;
    manifest.vendor["ort/" + f] = r;
    console.log(`  ort/${f}: ${r.error ? "ERR" : (r.bytes / 1048576).toFixed(1) + " MB"}`);
  }
}

async function get(url, dest) {
  if (!args.includes("--force") && fs.existsSync(dest) && fs.statSync(dest).size > 0) {
    return { bytes: fs.statSync(dest).size, sha256: sha256(fs.readFileSync(dest)), cached: true };
  }
  return download(url, dest);
}

console.log(`模型 ${MODEL} @ ${ENDPOINT} · 精度 ${DTYPES.join(",")}`);
if (!args.includes("--skip-models")) await fetchModel();
if (WANT_VENDOR) {
  console.log("vendor 镜像：");
  await fetchVendor();
}
fs.mkdirSync(recordsDir, { recursive: true });
fs.writeFileSync(path.join(recordsDir, "assets-manifest.json"), JSON.stringify(manifest, null, 2), "utf8");
const total = Object.values(manifest.files).reduce((a, f) => a + (f.bytes || 0), 0);
console.log(`完成：本地资产 ${(total / 1048576).toFixed(1)} MB · 清单 records/assets-manifest.json`);
