import { Embedder, chunkText, api, textLeg } from "/embedder.js";
import { MODELS, pickModel } from "/models.js";

const $ = (id) => document.getElementById(id);
const els = {
  backend: $("badge-backend"), model: $("badge-model"), db: $("badge-db"),
  modelSel: $("model"), dtype: $("dtype"), device: $("device"), batch: $("batch"),
  chunkSize: $("chunk-size"), chunkOverlap: $("chunk-overlap"),
  btnLoad: $("btn-load"), loadStatus: $("load-status"), engineInfo: $("engine-info"),
  kb: $("collection"),
  btnSample: $("btn-sample"), files: $("files"), paste: $("paste"),
  btnIngest: $("btn-ingest"), btnRefresh: $("btn-refresh"), ingestStatus: $("ingest-status"),
  progress: $("progress"), ingestMetrics: $("ingest-metrics"),
  query: $("query"), mode: $("mode"), topk: $("topk"), btnSearch: $("btn-search"),
  rrfC: $("rrf-c"), rrfW: $("rrf-w"),
  explain: $("explain"), queryMetrics: $("query-metrics"), results: $("results"),
  evidenceBox: $("evidence-box"), evidence: $("evidence"),
  state: $("state"), btnNewKb: $("btn-new-kb"), btnDropKb: $("btn-drop-kb"),
};

const store = {
  session: localStorage.getItem("edgekb.session") || newSession(),
  kb: localStorage.getItem("edgekb.kb") || "",
  docs: [],
  chunks: [],
  lastMetrics: null,
};
localStorage.setItem("edgekb.session", store.session);

const embedder = new Embedder();

function newSession() {
  return "s" + Math.random().toString(36).slice(2, 8);
}
const collectionName = () => `kb_${store.session}_${store.kb || "main"}`;

const fmt = (n, unit = "ms") => (n === null || n === undefined ? "—" : `${n} ${unit}`);
const bytes = (n) => (n > 1024 * 1024 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(0) + " KB");
const kv = (obj) =>
  Object.entries(obj)
    .map(([k, v]) => `<span><b>${k}</b> ${v === undefined || v === null ? "—" : v}</span>`)
    .join("");

function setBadge(el, text, cls = "") {
  el.textContent = text;
  el.className = "badge " + cls;
}

async function refreshHealth() {
  const h = await api("/api/health");
  store.health = h;
  if (h.db.connected) {
    setBadge(els.db, `seekdb ${h.db.mode} · ${h.db.database} · ${h.db.analyzer}`, "ok");
  } else {
    setBadge(els.db, "seekdb 未连接", "warn");
    console.warn("seekdb:", h.db.error);
  }
  const modelId = els.modelSel.value;
  const meta = pickModel(modelId);
  const files = (h.models.files || []).filter((f) => f.file.startsWith(modelId + "/"));
  const total = files.reduce((a, f) => a + f.bytes, 0);
  if (!els.modelSel.options.length) {
    els.modelSel.innerHTML = Object.entries(MODELS)
      .map(([id, m]) => `<option value="${id}">${m.label} · ${m.dimension} 维 · ${m.langs}</option>`)
      .join("");
  }
  els.modelSel.value = modelId;
  setBadge(
    els.model,
    files.length ? `${meta.label} · 本地 ${files.length} 文件 / ${bytes(total)}` : `${meta.label} · 未下载（将走 HF）`,
    files.length ? "ok" : "warn"
  );
  await refreshCollections(h.db.collections || []);
  renderState();
  return h;
}

async function refreshCollections(list) {
  const cols = list.filter((c) => c.name.startsWith(`kb_${store.session}_`));
  els.kb.innerHTML =
    (cols.length ? "" : `<option value="">（当前会话还没有知识库）</option>`) +
    cols.map((c) => `<option value="${c.name.replace(`kb_${store.session}_`, "")}">${c.name.replace(`kb_${store.session}_`, "")} · ${c.rows ?? "?"} 条</option>`).join("");
  if (!cols.some((c) => c.name === collectionName())) {
    store.kb = cols[0] ? cols[0].name.replace(`kb_${store.session}_`, "") : "main";
    localStorage.setItem("edgekb.kb", store.kb);
  }
  els.kb.value = store.kb;
}

function renderState() {
  const cols = (store.health?.db?.collections || []).filter((c) => c.name.startsWith(`kb_`));
  els.state.innerHTML =
    `<span>会话 <b>${store.session}</b></span><span>知识库 <b>${collectionName()}</b></span>` +
    `<span>维度 <b>${pickModel(els.modelSel.value).dimension}</b></span>` +
    `<span>距离 <b>${store.health?.app?.distance || "cosine"}</b></span>` +
    `<span>全文分析器 <b>${store.health?.app?.analyzer || "ik"}</b></span>` +
    `<span>已建集合 <b>${cols.length}</b>（共 ${cols.reduce((a, c) => a + (c.rows || 0), 0)} 条切片）</span>`;
}

/* ---------------- model ---------------- */

async function loadModel() {
  els.btnLoad.disabled = true;
  els.loadStatus.textContent = "加载中…";
  const t0 = performance.now();
  try {
    const s = await embedder.init({
      model: els.modelSel.value,
      dtype: els.dtype.value,
      device: els.device.value,
    });
    store.sessionInfo = s;
    setBadge(els.backend, `推理后端：${s.device.toUpperCase()} · ${s.dtype} · ${s.threads} 线程`, s.device === "webgpu" ? "ok" : "info");
    els.loadStatus.textContent = `已就绪（${Math.round(performance.now() - t0)} ms）`;
    els.engineInfo.innerHTML = kv({
      模型: `${s.label} (${s.model})`,
      向量维度: s.dimension,
      池化: s.pooling,
      精度: s.dtype,
      后端: s.device,
      WASM线程: s.threads,
      权重来源: s.source,
      "runtime 来源": s.libSource.startsWith("/") ? "本地 /vendor" : "CDN",
      加载耗时: fmt(s.loadMs),
      adapter: s.gpuOk ? `WebGPU 可用${s.f16 ? " · 支持 shader-f16" : " · 不支持 shader-f16"}` : "WebGPU 不可用",
    });
    if (s.notes?.length) els.engineInfo.innerHTML += `<div class="muted">降级说明：${s.notes.join("；")}</div>`;
    els.btnIngest.disabled = false;
    els.btnRefresh.disabled = false;
  } catch (e) {
    els.loadStatus.textContent = "加载失败：" + e.message;
    setBadge(els.backend, "后端不可用", "warn");
  } finally {
    els.btnLoad.disabled = false;
  }
}

/* ---------------- ingest ---------------- */

async function loadSample() {
  const idx = await api("/data/corpus/index.json");
  const docs = [];
  for (const item of idx.documents) {
    const res = await fetch("/data/corpus/" + item.file);
    const text = await res.text();
    docs.push({ id: item.id, title: item.title, source: item.source, lang: item.lang, text });
  }
  store.docs = docs;
  els.paste.value = "";
  els.ingestStatus.textContent = `已载入示例语料 ${docs.length} 篇，可点击“向量化并写入”`;
  toastDocs();
}

function toastDocs() {
  els.ingestMetrics.innerHTML = kv({ 待处理文档: store.docs.length, 说明: "文档在浏览器内切块并向量化" });
}

function collectDocs() {
  const docs = [...store.docs];
  const pasted = els.paste.value.trim();
  if (pasted) docs.push({ id: "paste-" + Date.now().toString(36), title: "粘贴内容", source: "paste", lang: "zh", text: pasted });
  return docs;
}

async function readFiles(fileList) {
  const out = [];
  for (const f of fileList) {
    const text = await f.text();
    out.push({
      id: f.name.replace(/\.[^.]+$/, "") + "-" + Math.random().toString(36).slice(2, 6),
      title: f.name,
      source: f.name,
      lang: /[一-龥]/.test(text) ? "zh" : "en",
      text,
    });
  }
  store.docs = [...store.docs, ...out];
  els.ingestStatus.textContent = `已加入 ${out.length} 个文件，共 ${store.docs.length} 篇待处理`;
  toastDocs();
}

async function ingest() {
  if (!embedder.session) return alert("请先加载模型");
  const docs = collectDocs();
  if (!docs.length) return alert("没有可写入的文档");
  const size = Number(els.chunkSize.value) || 320;
  const overlap = Number(els.chunkOverlap.value) || 48;
  const batch = Number(els.batch.value) || 16;

  store.chunks = [];
  for (const d of docs) {
    for (const [i, text] of chunkText(d.text, size, overlap).entries()) {
      store.chunks.push({
        id: `${d.id}#${i}`,
        text,
        metadata: { doc_id: d.id, title: d.title || d.id, chunk_index: i, source: d.source || d.id, lang: d.lang || "zh", session: store.session },
      });
    }
  }
  els.btnIngest.disabled = true;
  els.ingestStatus.textContent = `切块完成：${docs.length} 篇 → ${store.chunks.length} 条切片，正在本地向量化…`;
  els.progress.value = 0;

  const tAll = performance.now();
  const { vectors, timing } = await embedder.embedBatched(store.chunks.map((c) => c.text), batch, (done, total) => {
    els.progress.value = Math.round((done / total) * 70);
    els.ingestStatus.textContent = `本地向量化 ${done}/${total} 切片…`;
  });

  const rows = store.chunks.map((c, i) => ({ id: c.id, text: c.text, embedding: vectors[i], metadata: c.metadata }));
  const perReq = 40;
  let writeMs = 0;
  for (let i = 0; i < rows.length; i += perReq) {
    const slice = rows.slice(i, i + perReq);
    const r = await api("/api/ingest", { collection: collectionName(), rows: slice, refresh: false });
    writeMs += r.timing_ms.write;
    els.progress.value = 70 + Math.round(((i + slice.length) / rows.length) * 25);
    els.ingestStatus.textContent = `写入 seekdb ${Math.min(i + perReq, rows.length)}/${rows.length} 切片…`;
  }

  els.progress.value = 97;
  const beforeRefresh = await probeNewChunk();
  const rf = await api("/api/refresh", { collection: collectionName() });
  const afterRefresh = await probeNewChunk();
  const state = await api("/api/collections");
  await refreshHealth();
  els.progress.value = 100;
  els.btnIngest.disabled = false;

  store.lastMetrics = {
    chunks: rows.length,
    docs: docs.length,
    embedTotalMs: timing.total_ms,
    chunksPerS: timing.chunks_per_s,
    charsPerS: timing.chars_per_s,
    firstBatchMs: timing.first_batch_ms,
    warmBatchMs: timing.warm_batch_ms,
    writeMs: Math.round(writeMs),
    refreshMs: rf.timing_ms.refresh,
    probeBeforeRefresh: beforeRefresh,
    probeAfterRefresh: afterRefresh,
    totalMs: Math.round(performance.now() - tAll),
    batch,
    chunkSize: size,
  };
  els.ingestStatus.textContent = `完成：${docs.length} 篇 → ${rows.length} 切片已写入 ${collectionName()}`;
  els.ingestMetrics.innerHTML = kv({
    本地向量化: fmt(store.lastMetrics.embedTotalMs),
    吞吐: `${store.lastMetrics.chunksPerS} 切片/秒 · ${store.lastMetrics.charsPerS} 字符/秒`,
    "首批/稳态": `${fmt(store.lastMetrics.firstBatchMs)} / ${fmt(store.lastMetrics.warmBatchMs)}`,
    写入: fmt(store.lastMetrics.writeMs),
    索引刷新: fmt(store.lastMetrics.refreshMs),
    端到端: fmt(store.lastMetrics.totalMs),
    刷新前探针: store.lastMetrics.probeBeforeRefresh.found ? "已命中" : "未命中",
    刷新后探针: store.lastMetrics.probeAfterRefresh.found ? "已命中" : "未命中",
  });
}

/** Search for the newest chunk's own vector, used to show refresh semantics. */
async function probeNewChunk() {
  const last = store.chunks[store.chunks.length - 1];
  if (!last) return { found: false };
  const target = last.id;
  const r = await api("/api/search", {
    collection: collectionName(),
    mode: "hybrid",
    keywords: textLeg(last.text),
    queryEmbedding: (await embedder.embedBatched([last.text], 1)).vectors[0],
    topK: 20,
  });
  const rank = r.ids.indexOf(target);
  return { found: rank >= 0, rank: rank + 1, took_ms: r.took_ms };
}

/* ---------------- search ---------------- */

async function search() {
  if (!embedder.session) return alert("请先加载模型");
  const q = els.query.value.trim();
  if (!q) return;
  const mode = els.mode.value;
  const t0 = performance.now();
  const emb = await embedder.embedBatched([pickModel(els.modelSel.value).queryPrefix + q], Number(els.batch.value) || 1);
  const tEmbed = performance.now() - t0;
  const body = {
    collection: collectionName(),
    mode,
    keywords: textLeg(q),
    queryEmbedding: emb.vectors[0],
    topK: Number(els.topk.value) || 6,
    rrfConstant: Number(els.rrfC.value) || 60,
    rrfWindowSize: Number(els.rrfW.value) || 60,
  };
  const t1 = performance.now();
  const r = await api("/api/search" + (els.explain.checked ? "?explain=1" : ""), body);
  const tDb = performance.now() - t1;

  els.queryMetrics.innerHTML = kv({
    模式: mode,
    "查询向量化（本地）": fmt(Math.round(tEmbed)),
    "seekdb 检索": fmt(r.took_ms),
    端到端: fmt(Math.round(tEmbed + tDb)),
    命中: r.ids.length,
    知识库: collectionName(),
  });

  els.results.innerHTML =
    r.ids.length === 0
      ? `<div class="empty">没有结果。先导入语料，或换一种检索模式。</div>`
      : r.ids
          .map((id, i) => {
            const m = r.metadatas[i] || {};
            const d = r.distances[i];
            // hybrid/text: seekdb returns the fused _score (bigger = better); vector: cosine distance (smaller = better)
            const label = mode === "vector" ? "cosine_distance" : "_score";
            const score = typeof d === "number" ? `${label}=${d.toFixed(5)}` : `${label}=—`;
            return `<li class="${i === 0 ? "hit" : ""}">
        <div class="res-head"><span class="rank">#${i + 1}</span><span>${escapeHtml(m.title || id)}</span>
          <span class="score">${score}</span>
          <span class="chip">${escapeHtml(id)}</span><span class="chip">chunk ${m.chunk_index ?? "-"}</span><span class="chip">${escapeHtml(m.lang || "?")}</span></div>
        <p class="res-text">${highlight(escapeHtml(r.documents[i] || ""), body.keywords)}</p>
      </li>`;
          })
          .join("");

  if (r.evidence?.length) {
    els.evidenceBox.hidden = false;
    els.evidence.textContent = r.evidence.join("\n\n");
  } else {
    els.evidenceBox.hidden = true;
  }
  await refreshHealth();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function highlight(text, keywords) {
  const words = String(keywords || "").split(/\s+/).filter((w) => w.length >= 2).slice(0, 8);
  if (!words.length) return text;
  const re = new RegExp("(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  return text.replace(re, '<mark class="res-mark">$1</mark>');
}

/* ---------------- wiring ---------------- */

els.btnLoad.onclick = loadModel;
els.btnSample.onclick = loadSample;
els.btnIngest.onclick = () => ingest().catch((e) => (els.ingestStatus.textContent = "失败：" + e.message));
els.btnRefresh.onclick = async () => {
  const r = await api("/api/refresh", { collection: collectionName() });
  els.ingestStatus.textContent = `索引刷新完成（${r.timing_ms.refresh} ms）`;
};
els.btnSearch.onclick = () => search().catch((e) => (els.queryMetrics.innerHTML = `<span class="bad-txt">检索失败：${e.message}</span>`));
els.query.onkeydown = (e) => e.key === "Enter" && search().catch(() => {});
els.files.onchange = (e) => readFiles([...e.target.files]).catch((err) => console.error(err));
els.kb.onchange = (e) => {
  store.kb = e.target.value;
  localStorage.setItem("edgekb.kb", store.kb);
  renderState();
};
els.btnNewKb.onclick = async () => {
  const name = (prompt("新知识库名称（字母/数字，将创建同名 seekdb collection）", "kb" + Date.now().toString(36).slice(-4)) || "").trim();
  if (!/^[A-Za-z0-9]{1,20}$/.test(name)) return;
  store.kb = name.toLowerCase();
  localStorage.setItem("edgekb.kb", store.kb);
  await api("/api/ingest", { collection: collectionName(), rows: [], refresh: true }).catch(() => {});
  await refreshHealth();
};
els.btnDropKb.onclick = async () => {
  if (!confirm("删除知识库 " + collectionName() + " ？")) return;
  await api("/api/reset", { collection: collectionName() });
  await refreshHealth();
};
els.dtype.onchange = () => els.btnLoad.click();

(async function boot() {
  store.kb = store.kb || "main";
  try {
    await refreshHealth();
  } catch (e) {
    setBadge(els.db, "后端服务不可用", "warn");
    els.state.innerHTML = `<span class="bad-txt">无法读取 /api/health：${e.message}。请先运行 npm start。</span>`;
  }
  els.modelSel.value = Object.keys(MODELS)[0];
  els.query.value = "seekdb 如何在一条 SQL 里完成向量与全文混合检索？";
})();
