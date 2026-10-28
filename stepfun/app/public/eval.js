import { Embedder, chunkText, api, textLeg, median, p95 } from "/embedder.js";
import { chunksToDocs, ndcgAtK, mrr, recallAtK, summarise, round } from "/metrics.js";
import { MODELS, DTYPE_FILE } from "/models.js";

const CONFIGS = [
  { key: "fp16_webgpu", label: "FP16 + WebGPU", dtype: "fp16", device: "webgpu", on: true },
  { key: "q8_webgpu", label: "INT8(q8) + WebGPU", dtype: "q8", device: "webgpu", on: true },
  { key: "fp32_wasm", label: "量化前基线 · fp32 + WASM", dtype: "fp32", device: "wasm", on: true },
  { key: "q8_wasm", label: "INT8(q8) + WASM", dtype: "q8", device: "wasm", on: true },
  { key: "fp32_webgpu", label: "fp32 + WebGPU", dtype: "fp32", device: "webgpu", on: false },
];

const withTimeout = (promise, ms, label) =>
  Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} 超时 ${ms / 1000}s`)), ms)),
  ]);

const $ = (id) => document.getElementById(id);
const logEl = $("log");
const log = (m) => {
  logEl.textContent += "\n" + new Date().toISOString().slice(11, 19) + "  " + m;
  logEl.scrollTop = logEl.scrollHeight;
  console.log(m);
};

const state = { corpus: [], chunks: [], health: null, runs: [], ablation: null, env: null, stopped: false, failures: [] };

$("cfg-box").innerHTML = CONFIGS.map(
  (c) => `<label class="inline"><input type="checkbox" data-key="${c.key}" ${c.on ? "checked" : ""} /> ${c.label}</label>`
).join("");

async function boot() {
  state.health = await api("/api/health");
  const gpu = await adapterInfo();
  state.env = {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemoryGB: navigator.deviceMemory || null,
    isSecureContext: self.isSecureContext,
    crossOriginIsolated: self.crossOriginIsolated,
    sharedArrayBuffer: typeof SharedArrayBuffer === "function",
    webgpu: gpu,
    seekdb: state.health.db,
    app: state.health.app,
    chunking: { size: 320, overlap: 48 },
    transformers_js: "4.3.2 (/vendor 或 jsDelivr)",
  };
  $("env").textContent = gpu ? `WebGPU 可用${gpu.f16 ? " · shader-f16" : ""} · ${navigator.hardwareConcurrency} 核` : "WebGPU 不可用 · 仅 WASM";
  $("env").className = "badge " + (gpu ? "ok" : "warn");
  renderSizes();
}

async function adapterInfo() {
  if (!navigator.gpu) return null;
  try {
    const a = await navigator.gpu.requestAdapter();
    if (!a) return null;
    const info = a.info || {};
    return { f16: a.features?.has?.("shader-f16"), vendor: info.vendor, architecture: info.architecture, description: info.description };
  } catch {
    return null;
  }
}

function filesFor(dtype) {
  const model = Object.keys(MODELS)[0];
  const files = (state.health.models.files || []).filter((f) => f.file.startsWith(model + "/"));
  if (!files.length) return null;
  const core = files.filter((f) => f.file === `${model}/${DTYPE_FILE[dtype]}`);
  const shared = files.filter((f) => !f.file.includes("/onnx/"));
  return {
    model,
    core: core.map((f) => ({ file: f.file, bytes: f.bytes })),
    coreBytes: core.reduce((a, f) => a + f.bytes, 0),
    sharedBytes: shared.reduce((a, f) => a + f.bytes, 0),
    totalBytes: core.reduce((a, f) => a + f.bytes, 0) + shared.reduce((a, f) => a + f.bytes, 0),
  };
}

function renderSizes() {
  const rows = ["fp32", "fp16", "q8"].map((dtype) => {
    const f = filesFor(dtype);
    const base = filesFor("fp32");
    const ratio = f && base && f.coreBytes ? (base.coreBytes / f.coreBytes).toFixed(2) : "—";
    return `<tr><td>${dtype === "q8" ? "q8 / INT8 动态量化" : dtype}</td>
      <td>${f ? (f.coreBytes / 1048576).toFixed(1) + " MB" : "未下载"}</td>
      <td>${f ? (f.sharedBytes / 1024).toFixed(0) + " KB" : "—"}</td>
      <td>${f ? (f.totalBytes / 1048576).toFixed(1) + " MB" : "—"}</td>
      <td>${ratio === "—" ? "—" : ratio + "×"}</td></tr>`;
  });
  $("sizes").innerHTML = `<table><thead><tr><th>精度方案</th><th>ONNX 权重</th><th>tokenizer/config</th><th>合计（同口径）</th><th>压缩比(vs fp32 权重)</th></tr></thead><tbody>${rows.join("")}</tbody></table>
    <p class="muted">口径：运行时真正下载的 onnx 图文件 + 分词器与配置文件；压缩比 = fp32 权重 / 该精度权重。文件清单来自 <code>/api/health</code>（含 sha256 的完整清单见 records/assets-manifest.json）。</p>`;
}

async function loadCorpus() {
  const idx = await api("/data/corpus/index.json");
  const docs = [];
  for (const d of idx.documents) {
    const text = await (await fetch("/data/corpus/" + d.file)).text();
    docs.push({ ...d, text: text.trim() });
  }
  state.corpus = docs;
  state.chunks = [];
  for (const d of docs) {
    for (const [i, text] of chunkText(d.text, 320, 48).entries()) {
      state.chunks.push({ id: `${d.id}#${i}`, text, metadata: { doc_id: d.id, title: d.title, chunk_index: i, source: d.source, lang: d.lang, session: "eval" } });
    }
  }
  log(`语料：${docs.length} 篇 → ${state.chunks.length} 条切片`);
}

/** RRF 参数扫描：向量已在主循环算好，这里只打数据库，比较融合超参。 */
async function sweepRrf({ collection, queries, qvecs, topK }) {
  const out = [];
  for (const rc of [10, 30, 60, 120]) {
    for (const rw of [10, 20, 60]) {
      const rows = [];
      for (const q of queries) {
        if (!qvecs[q.id]) continue;
        const r = await api("/api/search", {
          collection,
          mode: "hybrid",
          keywords: textLeg(q.text),
          queryEmbedding: qvecs[q.id],
          topK,
          rrfConstant: rc,
          rrfWindowSize: rw,
        });
        rows.push({ gold: q.gold, ranked: chunksToDocs(r.ids, r.metadatas) });
      }
      const s = summarise(rows);
      out.push({ rank_constant: rc, rank_window: rw, "recall@1": s["recall@1"], "recall@5": s["recall@5"], "ndcg@10": s["ndcg@10"], mrr: s.mrr });
    }
  }
  return out;
}

async function runConfig(cfg) {
  const t0 = performance.now();
  const emb = new Embedder();
  const model = Object.keys(MODELS)[0];
  let session;
  try {
    session = await withTimeout(emb.init({ model, dtype: cfg.dtype, device: cfg.device }), 180000, "模型加载");
  } catch (e) {
    log(`  跳过 ${cfg.label}：${e.message}`);
    state.failures.push({ config: cfg.label, error: String(e.message).slice(0, 200) });
    emb.dispose();
    return null;
  }
  const collection = `kb_eval_${cfg.key}`.slice(0, 50);
  log(`▶ ${cfg.label} · 后端=${session.device} 精度=${session.dtype} 线程=${session.threads} 加载=${session.loadMs}ms`);

  const tEmbed = performance.now();
  let embedded;
  try {
    embedded = await withTimeout(emb.embedBatched(state.chunks.map((c) => c.text), Number($("batch").value) || 16), 420000, "文档向量化");
  } catch (e) {
    log(`  跳过 ${cfg.label}：${e.message}`);
    state.failures.push({ config: cfg.label, error: String(e.message).slice(0, 200) });
    emb.dispose();
    return null;
  }
  const { vectors, timing } = embedded;
  const embedMs = Math.round(performance.now() - tEmbed);
  if (state.stopped) return null;

  const rows = state.chunks.map((c, i) => ({ id: c.id, text: c.text, embedding: vectors[i], metadata: c.metadata }));
  let writeMs = 0;
  for (let i = 0; i < rows.length; i += 40) {
    const r = await api("/api/ingest", { collection, rows: rows.slice(i, i + 40), refresh: false });
    writeMs += r.timing_ms.write;
  }
  const rf = await api("/api/refresh", { collection });
  log(`  向量化 ${embedMs}ms（${timing.chunks_per_s} 切片/秒）· 写入 ${Math.round(writeMs)}ms · 刷新 ${rf.timing_ms.refresh}ms`);

  const queries = (await api("/eval/queries.json")).queries;
  const perMode = { hybrid: [], vector: [], text: [] };
  const latencies = { cold: [], warm: [] };
  const searchMs = { hybrid: [], vector: [], text: [] };
  const qvecs = {};
  const repeat = Math.max(1, Number($("repeat").value) || 1);
  const topK = Number($("topk").value) || 10;

  for (const [qi, q] of queries.entries()) {
    const te = performance.now();
    const { vectors: qv } = await emb.embedBatched([session.queryPrefix + q.text], 1);
    qvecs[q.id] = qv[0];
    const embedQ = performance.now() - te;
    let ranked = null;
    for (const mode of ["hybrid", "vector", "text"]) {
      const t1 = performance.now();
      const r = await api("/api/search", {
        collection,
        mode,
        keywords: textLeg(q.text),
        queryEmbedding: qv[0],
        topK,
      });
      const ms = performance.now() - t1;
      searchMs[mode].push(ms);
      const docs = chunksToDocs(r.ids, r.metadatas);
      if (mode === "hybrid") ranked = docs;
      perMode[mode].push({ id: q.id, text: q.text, gold: q.gold, ranked: docs, recall5: recallAtK(docs, q.gold, 5), ndcg10: ndcgAtK(docs, q.gold, 10), mrr: mrr(docs, q.gold) });
    }
    const e2e = embedQ + searchMs.hybrid[searchMs.hybrid.length - 1];
    latencies.cold.push(e2e);
    for (let r = 1; r < repeat; r++) {
      const t2 = performance.now();
      await api("/api/search", { collection, mode: "hybrid", keywords: textLeg(q.text), queryEmbedding: qv[0], topK });
      latencies.warm.push(performance.now() - t2);
    }
    if (qi % 10 === 9) log(`  查询进度 ${qi + 1}/${queries.length}`);
    $("progress").value = Math.round(((qi + 1) / queries.length) * 90);
    if (state.stopped) break;
  }

  const rrfSweep = await sweepRrf({ collection, queries, qvecs, topK });
  const best = [...rrfSweep].sort((a, b) => (b["ndcg@10"] || 0) - (a["ndcg@10"] || 0))[0];
  log(`  RRF 调参最优：rank_constant=${best.rank_constant} window=${best.rank_window} → nDCG@10=${best["ndcg@10"]} Recall@1=${best["recall@1"]}`);

  const evidence = (await api("/api/search?explain=1", { collection, mode: "hybrid", keywords: textLeg("混合检索"), queryEmbedding: vectors[0], topK: 3 })) || {};
  const run = {
    config: cfg,
    session: { device: session.device, dtype: session.dtype, threads: session.threads, loadMs: session.loadMs, buildMs: session.buildMs, source: session.source, notes: session.notes, dimension: session.dimension },
    collection,
    sizes: filesFor(cfg.dtype),
    corpus: { documents: state.corpus.length, chunks: state.chunks.length, chars: state.chunks.reduce((a, c) => a + c.text.length, 0) },
    timing: {
      model_load_ms: session.loadMs,
      embed_total_ms: embedMs,
      embed_first_batch_ms: timing.first_batch_ms,
      embed_warm_batch_ms: timing.warm_batch_ms,
      chunks_per_s: timing.chunks_per_s,
      chars_per_s: timing.chars_per_s,
      write_ms: Math.round(writeMs),
      refresh_ms: rf.timing_ms.refresh,
      search_ms_p50: { hybrid: median(searchMs.hybrid), vector: median(searchMs.vector), text: median(searchMs.text) },
      search_ms_p95: { hybrid: p95(searchMs.hybrid), vector: p95(searchMs.vector), text: p95(searchMs.text) },
      e2e_first_ms: round(latencies.cold[0]),
      e2e_median_ms: round(median(latencies.cold.slice(1))),
      warm_search_only_ms: round(median(latencies.warm)),
      total_ms: Math.round(performance.now() - t0),
    },
    quality: {
      hybrid: summarise(perMode.hybrid),
      vector: summarise(perMode.vector),
      text: summarise(perMode.text),
    },
    perQuery: perMode.hybrid.map((h) => ({ ...h, ranked: h.ranked.slice(0, 10).map((d) => d.docId) })),
    rrf_sweep: rrfSweep,
    rrf_best: best,
    evidence_sql: evidence.evidence || [],
  };
  emb.dispose();
  log(`  ✔ 质量 Recall@1=${run.quality.hybrid["recall@1"]} Recall@5=${run.quality.hybrid["recall@5"]} nDCG@10=${run.quality.hybrid["ndcg@10"]} · 端到端中位=${run.timing.e2e_median_ms}ms`);
  return run;
}

async function runAblation() {
  const ran = state.runs.filter(Boolean);
  const cfg = ran.length ? ran[0].config : CONFIGS[0];
  const model = Object.keys(MODELS)[0];
  const out = { config: cfg.label, batch_sweep: [], thread_sweep: [], refresh_strategy: [] };
  const sample = state.chunks.slice(0, 32).map((c) => c.text);
  const save = () => saveRecord().catch((e) => log("  消融中间保存失败：" + e.message));

  // 一个会话内只改批大小，避免反复创建 WebGPU device
  const emb = new Embedder();
  try {
    await withTimeout(emb.init({ model, dtype: cfg.dtype, device: cfg.device }), 120000, "消融·模型加载");
    for (const bs of [1, 4, 16]) {
      await withTimeout(emb.embedBatched(sample.slice(0, bs), bs), 120000, "消融·预热");
      const t = performance.now();
      await withTimeout(emb.embedBatched(sample, bs), 180000, "消融·批大小 " + bs);
      const ms = Math.round(performance.now() - t);
      out.batch_sweep.push({ batch: bs, total_ms: ms, chunks_per_s: Math.round((sample.length / ms) * 1000) });
      log(`  消融·批大小 ${bs} → ${out.batch_sweep.at(-1).chunks_per_s} 切片/秒（${ms}ms / ${sample.length} 条）`);
    }
  } catch (e) {
    out.batch_sweep_error = String(e.message).slice(0, 160);
    log("  消融·批大小失败：" + e.message);
  }
  emb.dispose();
  await save();

  for (const th of [1, 8]) {
    const e2 = new Embedder();
    try {
      const s = await withTimeout(e2.init({ model, dtype: "q8", device: "wasm", threads: th }), 120000, "消融·WASM 加载");
      const t = performance.now();
      await withTimeout(e2.embedBatched(sample, 16), 180000, "消融·WASM 线程 " + th);
      const ms = Math.round(performance.now() - t);
      out.thread_sweep.push({ requested: th, threads: s.threads, total_ms: ms, chunks_per_s: Math.round((sample.length / ms) * 1000) });
      log(`  消融·WASM 线程 ${th} → 实测 ${s.threads} 线程 · ${out.thread_sweep.at(-1).chunks_per_s} 切片/秒`);
    } catch (e) {
      out.thread_sweep.push({ requested: th, error: String(e.message).slice(0, 120) });
      log("  消融·WASM 线程 " + th + " 失败：" + e.message);
    }
    e2.dispose();
  }
  await save();

  const collection = "kb_eval_refresh";
  const emb3 = new Embedder();
  try {
    await withTimeout(emb3.init({ model, dtype: cfg.dtype, device: cfg.device }), 120000, "消融·刷新加载");
    const { vectors } = await withTimeout(emb3.embedBatched(state.chunks.slice(0, 60).map((c) => c.text), 16), 300000, "消融·向量化");
    const rows = state.chunks.slice(0, 60).map((c, i) => ({ id: c.id, text: c.text, embedding: vectors[i], metadata: c.metadata }));
    const perBatch = [];
    for (let i = 0; i < rows.length; i += 20) perBatch.push(rows.slice(i, i + 20));
    let t = performance.now();
    for (const b of perBatch) await api("/api/ingest", { collection, rows: b, refresh: true });
    const everyRefresh = Math.round(performance.now() - t);
    await api("/api/reset", { collection });
    t = performance.now();
    for (const b of perBatch) await api("/api/ingest", { collection, rows: b, refresh: false });
    await api("/api/refresh", { collection });
    const singleRefresh = Math.round(performance.now() - t);
    await api("/api/reset", { collection });
    out.refresh_strategy.push({ mode: "每批刷新", total_ms: everyRefresh }, { mode: "末尾一次刷新", total_ms: singleRefresh });
    log(`  消融·刷新策略：每批 ${everyRefresh}ms vs 末尾一次 ${singleRefresh}ms`);
  } catch (e) {
    out.refresh_strategy_error = String(e.message).slice(0, 160);
    log("  消融·刷新策略失败：" + e.message);
  }
  emb3.dispose();

  state.ablation = out;
  $("ablation").innerHTML = table(
    ["优化项", "取值", "结果"],
    [
      ...out.batch_sweep.map((b) => ["embedding 批大小", b.batch, b.error || `${b.chunks_per_s} 切片/秒（${b.total_ms}ms / ${sample.length} 条）`]),
      ...out.thread_sweep.map((b) => ["WASM 线程数", b.requested, b.error || `实测 ${b.threads} 线程 → ${b.chunks_per_s} 切片/秒`]),
      ...out.refresh_strategy.map((b) => ["索引刷新策略", b.mode, `${b.total_ms}ms / 60 条`]),
    ]
  );
  await save();
  return out;
}

const table = (heads, rows) =>
  `<table><thead><tr>${heads.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${r.map((c) => `<td>${c ?? "—"}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;

function render() {
  const runs = state.runs.filter(Boolean);
  if (!runs.length) return;
  $("quality").innerHTML = table(
    ["配置", "模型体积", "压缩比", "Recall@1", "Recall@5", "Recall@10", "nDCG@10", "MRR", "Hit@1", "ΔnDCG vs fp32 基线"],
    runs.map((r) => {
      const base = runs.find((x) => x.config.dtype === "fp32");
      const d = base && base !== r ? round((r.quality.hybrid["ndcg@10"] - base.quality.hybrid["ndcg@10"]) * 100, 2) : null;
      const cls = d === null ? "" : d >= 0 ? "ok-txt" : "bad-txt";
      return [
        r.config.label,
        r.sizes ? (r.sizes.totalBytes / 1048576).toFixed(1) + " MB" : "—",
        r.sizes && r.sizes.coreBytes ? round(filesFor("fp32").coreBytes / r.sizes.coreBytes, 2) + "×" : "—",
        r.quality.hybrid["recall@1"],
        r.quality.hybrid["recall@5"],
        r.quality.hybrid["recall@10"],
        r.quality.hybrid["ndcg@10"],
        r.quality.hybrid.mrr,
        r.quality.hybrid["hit@1"],
        d === null ? "—" : `<span class="${cls}">${d > 0 ? "+" : ""}${d} pt</span>`,
      ];
    })
  );
  $("perf").innerHTML = table(
    ["配置", "模型加载", "首批向量化", "稳态批次", "吞吐(切片/秒)", "写入", "索引刷新", "检索 p50", "检索 p95", "首个端到端", "稳态端到端", "总计"],
    runs.map((r) => [
      r.config.label,
      fmt(r.timing.model_load_ms),
      fmt(r.timing.embed_first_batch_ms),
      fmt(r.timing.embed_warm_batch_ms),
      r.timing.chunks_per_s,
      fmt(r.timing.write_ms),
      fmt(r.timing.refresh_ms),
      fmt(r.timing.search_ms_p50.hybrid),
      fmt(r.timing.search_ms_p95.hybrid),
      fmt(r.timing.e2e_first_ms),
      fmt(r.timing.e2e_median_ms),
      fmt(r.timing.total_ms),
    ])
  );
  const modeRows = [];
  for (const r of runs) {
    for (const m of ["hybrid", "vector", "text"]) {
      modeRows.push([r.config.label, m, r.quality[m]["recall@1"], r.quality[m]["recall@5"], r.quality[m]["ndcg@10"], r.quality[m].mrr, fmt(r.timing.search_ms_p50[m])]);
    }
  }
  $("modes").innerHTML = table(["配置", "检索模式", "Recall@1", "Recall@5", "nDCG@10", "MRR", "检索 p50"], modeRows);
  for (const r of runs) {
    if (!r.rrf_sweep?.length) continue;
    const rows = r.rrf_sweep.map((s) => [
      s.rank_constant,
      s.rank_window,
      s["recall@1"],
      s["recall@5"],
      s["ndcg@10"],
      s.mrr,
      s === r.rrf_best ? "★ 最优" : "",
    ]);
    $("modes").innerHTML += `<h2 style="margin-top:18px">RRF 融合参数扫描 · ${r.config.label}</h2>` + table(["rank_constant", "rank_window", "Recall@1", "Recall@5", "nDCG@10", "MRR", ""], rows);
  }
}

const fmt = (n) => (n === null || n === undefined ? "—" : n + " ms");

$("btn-run").onclick = async () => {
  $("btn-run").disabled = true;
  state.stopped = false;
  state.runs = [];
  state.failures = [];
  state.ablation = null;
  logEl.textContent = "";
  try {
    await boot();
    if (!state.chunks.length) await loadCorpus();
    const chosen = CONFIGS.filter((c) => document.querySelector(`input[data-key="${c.key}"]`).checked);
    for (const [i, cfg] of chosen.entries()) {
      if (state.stopped) break;
      log(`(${i + 1}/${chosen.length}) ${cfg.label}`);
      state.runs.push(await runConfig(cfg));
      $("progress").value = Math.round(((i + 1) / chosen.length) * 90);
    }
    render();
    await saveRecord();
    if ($("do-ablation").checked && !state.stopped) {
      log("▶ 优化项消融");
      await runAblation();
      await saveRecord();
    }
    log("完成，原始数据已写入 records/。");
  } catch (e) {
    log("!! 评测失败：" + (e.stack || e.message));
  } finally {
    $("btn-run").disabled = false;
  }
};

$("btn-stop").onclick = () => {
  state.stopped = true;
  log("已请求中止…");
};

async function saveRecord() {
  const payload = {
    kind: "edgekb-benchmark",
    started_at: new Date().toISOString(),
    environment: state.env,
    corpus: { documents: state.corpus.length, chunks: state.chunks.length },
    runs: state.runs.filter(Boolean),
    failures: state.failures,
    ablation: state.ablation,
  };
  const r = await api("/api/record", { name: $("record-name").value, payload });
  $("save-status").textContent = `已保存 ${r.saved}（${(r.bytes / 1024).toFixed(0)} KB）`;
  log("已保存 " + r.saved);
  return r;
}

$("btn-save").onclick = () => saveRecord().catch((e) => log("保存失败：" + e.message));

function defaultRecordName() {
  const d = new Date();
  const p = (x) => String(x).padStart(2, "0");
  return `bench-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-edgekb.json`;
}

$("record-name").value = defaultRecordName();
boot().catch((e) => log("环境初始化失败：" + e.message));
