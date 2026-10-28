// Runs entirely on the user's device: model download -> ONNX Runtime Web (WebGPU / WASM) -> embeddings.
// No cloud inference API is involved: only model files are fetched, all math happens here.

import { MODELS, pickModel } from "/models.js";

const LIB_SOURCES = [
  "/vendor/transformers.esm.js",
  "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2/+esm",
];
const ORT_LOCAL = {
  mjs: "/vendor/ort/ort-wasm-simd-threaded.asyncify.mjs",
  wasm: "/vendor/ort/ort-wasm-simd-threaded.asyncify.wasm",
};

let tf = null;
let tfSource = null;
let extractor = null;
let session = null;

async function loadLibrary() {
  if (tf) return tf;
  let lastErr;
  for (const src of LIB_SOURCES) {
    try {
      const mod = await import(src);
      if (mod?.pipeline) {
        tf = mod;
        tfSource = src;
        return tf;
      }
    } catch (e) {
      lastErr = e;
    }
  }
  throw new Error("Transformers.js 加载失败：" + (lastErr?.message || "unknown"));
}

async function hasLocalModel(model) {
  try {
    const r = await fetch(`/models/${model}/tokenizer.json`, { method: "HEAD" });
    return r.ok;
  } catch {
    return false;
  }
}

async function hasLocalOrt() {
  try {
    const r = await fetch(ORT_LOCAL.wasm, { method: "HEAD" });
    return r.ok;
  } catch {
    return false;
  }
}

async function webgpuAdapter() {
  if (!navigator.gpu) return null;
  try {
    return (await navigator.gpu.requestAdapter()) || null;
  } catch {
    return null;
  }
}

function wasmThreads(device, override) {
  if (device !== "wasm") return 1;
  if (typeof self.SharedArrayBuffer !== "function") return 1;
  if (override) return Math.max(1, Math.min(Number(override), 16));
  const hw = navigator.hardwareConcurrency || 4;
  return Math.max(1, Math.min(hw, 8));
}

async function plan(requestedDevice, requestedDtype) {
  const adapter = await webgpuAdapter();
  const gpuOk = !!adapter;
  const f16 = gpuOk && adapter.features?.has?.("shader-f16");
  const gpuInfo = adapter?.info ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture } : {};
  const notes = [];
  let device = requestedDevice;

  if (device === "auto") device = gpuOk ? "webgpu" : "wasm";
  if (device === "webgpu" && !gpuOk) {
    notes.push("requestAdapter() 不可用 → 降级到 WASM");
    device = "wasm";
  }
  let dtype = requestedDtype;
  if (dtype === "auto") {
    dtype = device === "webgpu" ? (f16 ? "fp16" : "fp32") : "q8";
    if (device === "webgpu" && !f16) notes.push("WebGPU adapter 无 shader-f16 → 使用 fp32");
  }
  if (device === "wasm" && dtype === "fp16") {
    notes.push("WASM 后端不支持 fp16 → 改用 q8(INT8)");
    dtype = "q8";
  }
  if (device === "webgpu" && dtype === "fp16" && !f16) {
    notes.push("WebGPU 缺少 shader-f16 → 改用 fp32");
    dtype = "fp32";
  }
  return { device, dtype, notes, gpuOk, f16, gpuInfo };
}

async function init(opts) {
  const t0 = performance.now();
  const lib = await loadLibrary();
  const model = opts.model;
  const meta = pickModel(model);
  const local = await hasLocalModel(model);

  lib.env.allowRemoteModels = !local;
  lib.env.allowLocalModels = true;
  lib.env.localModelPath = "/models/";
  if (opts.hfHost && !local) lib.env.remoteHost = opts.hfHost;
  const wasmEnv = lib.env.backends?.onnx?.wasm;
  if (wasmEnv) {
    if ((await hasLocalOrt()) && local) wasmEnv.wasmPaths = ORT_LOCAL;
    wasmEnv.proxy = false;
  }

  const planRes = await plan(opts.device || "auto", opts.dtype || "auto");
  const applyThreads = (device) => {
    if (wasmEnv) wasmEnv.numThreads = wasmThreads(device, opts.threads);
  };
  const build = async ({ device, dtype }) => {
    applyThreads(device);
    const t = performance.now();
    const fn = await lib.pipeline("feature-extraction", model, { device, dtype });
    return { fn, ms: Math.round(performance.now() - t) };
  };

  let active = planRes;
  let built = null;
  try {
    built = await build(active);
  } catch (e) {
    if (active.device === "webgpu") {
      active = {
        ...active,
        device: "wasm",
        dtype: active.dtype === "fp16" ? "fp32" : "q8",
        notes: [...active.notes, `WebGPU 初始化失败(${String(e.message).slice(0, 120)}) → 自动降级 WASM`],
      };
      built = await build(active);
    } else {
      throw e;
    }
  }

  extractor = built.fn;
  const probe = await extractor(["dimension probe"], { pooling: meta.pooling, normalize: true });
  const vectors = readout(probe, 1);
  session = {
    model,
    label: meta.label,
    dimension: vectors[0].length,
    declaredDimension: meta.dimension,
    pooling: meta.pooling,
    queryPrefix: meta.queryPrefix,
    device: active.device,
    dtype: active.dtype,
    requested: { device: opts.device || "auto", dtype: opts.dtype || "auto" },
    notes: active.notes,
    gpuOk: active.gpuOk,
    f16: active.f16,
    gpuInfo: active.gpuInfo,
    threads: wasmThreads(active.device, opts.threads),
    source: local ? "local /models" : "remote " + (lib.env.remoteHost || "huggingface.co"),
    libSource: tfSource,
    loadMs: Math.round(performance.now() - t0),
    buildMs: built.ms,
    calls: 0,
    embeddedChunks: 0,
    embeddedChars: 0,
  };
  return session;
}

function readout(output, count) {
  const raw = typeof output?.tolist === "function" ? output.tolist() : output;
  const list = Array.isArray(raw) ? raw : [raw];
  const out = [];
  for (let i = 0; i < count; i++) {
    let v = list[i % list.length];
    if (Array.isArray(v) && Array.isArray(v[0])) {
      const dim = v[0].length;
      const acc = new Array(dim).fill(0);
      for (const tok of v) for (let d = 0; d < dim; d++) acc[d] += tok[d];
      v = acc.map((x) => x / v.length);
    }
    const norm = Math.hypot(...v) || 1;
    out.push(v.map((x) => Math.round((x / norm) * 1e6) / 1e6));
  }
  return out;
}

async function embed(texts, opts) {
  if (!extractor) throw new Error("模型尚未加载");
  const pooling = opts?.pooling || session.pooling;
  const t0 = performance.now();
  const output = await extractor(texts, { pooling, normalize: true });
  const vectors = readout(output, texts.length);
  const ms = Math.round((performance.now() - t0) * 100) / 100;
  session.calls += 1;
  session.embeddedChunks += texts.length;
  session.embeddedChars += texts.reduce((a, b) => a + b.length, 0);
  return { vectors, ms, chars: texts.reduce((a, b) => a + b.length, 0) };
}

self.onmessage = async (ev) => {
  const { id, cmd, payload } = ev.data;
  try {
    let result;
    if (cmd === "init") result = await init(payload);
    else if (cmd === "embed") result = await embed(payload.texts, payload);
    else if (cmd === "status") result = session;
    else if (cmd === "models") result = MODELS;
    else if (cmd === "dispose") {
      await extractor?.dispose?.();
      extractor = null;
      session = null;
      result = { disposed: true };
    } else throw new Error("unknown command " + cmd);
    self.postMessage({ id, ok: true, result });
  } catch (e) {
    self.postMessage({ id, ok: false, error: String(e?.message || e) });
  }
};
