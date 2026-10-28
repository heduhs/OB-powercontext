# 一页技术说明 · EdgeKB（端侧 AI 推理性能优化与 seekdb 混合检索）

## 1 · 模型与量化

| 项 | 内容 |
| --- | --- |
| 模型 | `BAAI/bge-small-zh-v1.5`（中英混排，4 层 BERT，输出 **512 维**，最大 512 token） |
| ONNX 来源与版本 | `Xenova/bge-small-zh-v1.5` @ `main` 分支（HuggingFace，Apache-2.0 社区官方转换仓库）；每个文件的字节数与 sha256 见 `records/assets-manifest.json` |
| 获取方式 | `npm run setup` → `scripts/fetch-assets.mjs`（网络受限时加 `--endpoint https://hf-mirror.com`） |
| 池化 / 归一化 | CLS 池化 + L2 归一化（与模型发布方一致）；查询侧加检索指令前缀 |
| 量化方案 | **INT8**：ONNX 动态量化权重 `onnx/model_quantized.onnx`（Transformers.js `dtype:'q8'`）；**FP16**：`onnx/model_fp16.onnx`（WebGPU 以 f16 计算）；基线 `onnx/model.onnx`（fp32） |

体积（同一口径：运行时真正下载的 ONNX 图 + 分词器/配置；压缩比 = fp32 权重 / 该精度权重）

| 精度 | ONNX 权重 | 分词器+配置 | 合计 | 压缩比 |
| --- | --- | --- | --- | --- |
| fp32（量化前基线） | 90.5 MB | 0.53 MB | 91.0 MB | 1.00× |
| fp16 | 45.3 MB | 0.53 MB | 45.8 MB | **2.00×** |
| q8 / INT8 | 22.9 MB | 0.53 MB | 23.4 MB | **3.95×** |

检索质量变化。评测集：51 篇文档 → 98 条切片（分块 320/重叠 48）× 30 条查询（文档级人工标注，gold 1–3 篇）；
同一设备、同一浏览器、同一查询集，只改变精度与后端；每条查询重复 3 次取中位数。

| 配置 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | nDCG@10 | MRR | Hit@1 | ΔnDCG vs 基线 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fp32 + WASM（基线） | 0.4889 | 0.75 | 0.8444 | 0.8556 | 0.7988 | 0.8306 | 0.7333 | — |
| q8/INT8 + WASM | 0.4778 | 0.75 | 0.8333 | 0.8556 | 0.7933 | 0.8139 | 0.70 | −0.55 pt |
| fp16 + WebGPU | 0.4611 | 0.75 | 0.8333 | 0.8556 | 0.7945 | 0.8194 | 0.70 | −0.43 pt |
| q8/INT8 + WebGPU | 0.5278 | 0.75 | 0.80 | 0.8556 | 0.8017 | 0.8417 | 0.7667 | +0.29 pt |

结论：量化带来的质量变化在 **±0.6 pt nDCG@10** 内，与 HNSW 近似检索本身的抖动同量级（同一配置两次运行 nDCG 相差约 0.04）；
体积与速度收益是确定的。逐查询排名与原始数据见 `records/bench-configs.json`（`runs[].perQuery`）。

## 2 · 端侧运行

- **运行时**：`@huggingface/transformers@4.3.2` + ONNX Runtime Web（1.31.0-dev）。`scripts/fetch-assets.mjs` 会把 ESM 依赖链与 wasm 二进制镜像到 `app/vendor/`，模型镜像到 `app/models/`，因此前端**不依赖任何 CDN 或外网**；推理全部在 Web Worker 中执行。
- **后端选择**：显式传 `{device, dtype}`。`plan()` 先 `navigator.gpu.requestAdapter()` 探测：有 adapter 且具备 `shader-f16` → `webgpu + fp16`；有 adapter 无 f16 → `webgpu + fp32`；无 adapter → `wasm + q8`。若 `pipeline()` 抛错则自动整链路降级并把原因写进界面“降级说明”。
- **验证结果**（Windows 10 x64 / Chrome 156 / AMD gcn-5 adapter，`records/run-smoke.json`、`records/screenshots/`）：
  - 默认路径：徽标显示 `推理后端：WEBGPU · fp16 · 1 线程`，512 维校验通过，98 切片本地向量化 → 写入 → 混合检索返回排序结果；
  - 降级路径：后端选「强制 WASM」后徽标变为 `WASM · q8 · 8 线程`，同一套流程照常完成（`npm run degrade`）；
  - 评测台把 `fp32/q8 × WASM` 与 `fp16/q8 × WebGPU` 四种组合同时跑一遍，即上表数据来源。
- **无云端推理**：服务端只做静态资源与数据库读写，没有任何推理代码；浏览器请求仅指向同源 `/models/`、`/vendor/`、`/api/*`（可在 DevTools Network 面板核对）。

## 3 · seekdb 使用

- **接入**：`seekdb@1.4.0`（`SeekdbClient` / `SeekdbAdminClient`）。默认 server 模式（MySQL 协议 `127.0.0.1:2881`，`npm run db:server` 提供启动脚本），同时保留 `SEEKDB_MODE=embedded` 嵌入式路径。
- **表结构**（由 SDK collection 抽象生成 v2 表）：`_id`＝`<docId>#<chunkIndex>`、`document`＝切片原文、`embedding VECTOR(512)`（HNSW / cosine）、`metadata`（JSON：`doc_id / title / chunk_index / source / lang / session`）、全文索引 `WITH PARSER ik PARSER_PROPERTIES=(ik_mode=smart)`。
  向量一律由浏览器算好后随 `add({embeddings})` 写入，集合以 `embeddingFunction: null` 创建 —— **服务端不做任何向量化**。
- **混合检索**：`collection.hybridSearch({ query.whereDocument.$contains, knn.queryEmbeddings, rank.rrf })`。seekdb 内部先 `SET @search_parm = '<搜索体 JSON>'`，再由 `DBMS_HYBRID_SEARCH.GET_SQL()` 生成融合 SQL，最后由数据库执行；两路召回与 RRF 融合都在库里完成（勾上“显示 seekdb 侧 SQL 证据”即可看到 `gv$ob_sql_audit` 抓到的真实语句）：

  ```sql
  (ifnull(1/(_fts._keyword_rank+60),0) + ifnull(1/(_vs._semantic_rank+60),0)) as _score
  FROM (SELECT …, match(document) against('…' in natural language mode) as _keyword_score …) _fts
  FULL JOIN (SELECT …, cosine_distance(embedding, '[查询向量]') as _semantic_score …) _vs
  ```
- **写入与刷新**：分批 `add()`（≤500 行/批）→ 批量结束后一次 `collection.refresh_index()`；导入结束时用最后一条切片做“刷新前 / 刷新后”命中探针并在界面报告；`/api/refresh` 也可手动触发。
- **集合与会话状态**：每个「会话 × 知识库」一个 collection（`kb_<session>_<kb>`，名称经 `^kb_[a-z0-9_]{1,48}$` 校验），新建/切换/删除都走服务端，删除即 `deleteCollection`；进程内缓存集合句柄，连续导入与切换后 `/api/collections` 的行数与检索结果始终与当前知识库一致。
- **安全**：文本腿会被数据库拼进 `match(...) against('…')`，服务端对关键词做字符白名单（`sanitizeTextLeg`）避免引号破坏语句；集合名白名单校验；静态资源路径穿越检查；`/api/record` 只接受 `*.json` 文件名。

## 4 · 状态与优化（实测）

分阶段计时（同一评测台，中位数；`records/bench-configs.json`）：

| 配置 | 模型加载 | 首批向量化 | 稳态批次 | 吞吐 | 写入 98 条 | 索引刷新 | 检索 p50 | 端到端首次 | 端到端中位 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fp32 + WASM（基线） | 5314 ms | 3639.9 ms | 2290.4 ms | 6 切片/s · 1338 字/s | 734 ms | 310.6 ms | 18.61 ms | 78.8 ms | 84.2 ms |
| q8/INT8 + WASM | **1466 ms** | 2946.5 ms | 1804.9 ms | 8 切片/s · 1701 字/s | 640 ms | 412.3 ms | 17.71 ms | 95.1 ms | 55.0 ms |
| fp16 + WebGPU | 6553 ms | **1522.5 ms** | **719.6 ms** | **18 切片/s · 3872 字/s** | 1422 ms | 732.1 ms | **15.77 ms** | 415.3 ms | **68.8 ms** |
| q8/INT8 + WebGPU | 4065 ms | 5510.9 ms | 4004.7 ms | 4 切片/s · 792 字/s | 905 ms | 411.4 ms | 15.15 ms | 174.1 ms | 147.0 ms |

优化点与结论：

1. **量化 → 体积与加载**：INT8 让权重从 90.5 MB 降到 22.9 MB（3.95×），WASM 冷加载从 5314 ms 降到 1466 ms（**−72%**），吞吐 6→8 切片/s（+33%）。
2. **后端选择 → 吞吐**：WebGPU + fp16 达到 18 切片/s，是 WASM+fp32 基线的 **3.0×**、WASM+q8 的 **2.25×**；而 q8 在 WebGPU 上反而最慢（4 切片/s，int8 反量化在 GPU 上开销大）——这正是 `auto` 策略把 WebGPU 配 fp16、WASM 配 q8 的实测依据。注意端侧吞吐受 GPU 占用与驱动影响明显：同一 fp16+WebGPU 配置在功能验证轮（`records/run-smoke.json`）实测 7 切片/s，上表取评测台同一批次连续四个配置的相对结果。
3. **多线程 WASM（最大单项收益）**：服务端下发 COOP/COEP 让页面进入跨源隔离，`SharedArrayBuffer` 可用 → ORT wasm `numThreads` 从 1 提到 8。同一 32 条切片、q8+WASM 实测 **11568 ms → 5762 ms（3 → 6 切片/s，2.0×）**。
4. **索引刷新策略**：批量导入结束后一次 `refresh_index()`，而不是每批刷新。60 条切片 / 3 个写入批次实测 **每批刷新 4462 ms → 末尾一次刷新 945 ms（4.7×）**；刷新后新内容可检索（导入流程内置“刷新前/刷新后”命中探针）。
5. **批处理与预热**：一次前向处理 16 条切片；首批（含着色器编译与图预热）1522 ms，稳态批次 719 ms；端到端首次 415 ms → 预热后 68.8 ms。批大小消融（fp16+WebGPU，32 条）为 1/4/16 → 10/10/9 切片/s，说明**短切片场景下 GPU 已被单批打满，批大小的收益主要出现在 WASM 路径**，因此默认值 16 保持不变，未做过度调优。
6. **RRF 超参扫描**（`records/bench-configs.json` 的 `runs[].rrf_sweep`，12 组参数 × 30 查询）：nDCG@10 在 0.774–0.802 之间，`rank_window_size=60` 一致最优，`rank_constant` 对 Top-1 排序无影响（RRF 只依赖名次）——因此保留默认 60/60，未做过度调参。
7. **三种检索模式对照**（fp16+WebGPU）：hybrid Recall@5 **0.8333** > text 0.7889 > vector 0.7167；nDCG@10 text 0.8119 > hybrid 0.7945 > vector 0.7168。在这个 51 篇、主题密集的中文语料上全文腿很强，混合的价值体现在 **Recall@5 提升 11.6 pt**（补上纯关键词漏掉的语义近义）与专有名词/代码标识符的精确命中（纯向量会漏），且检索延迟只差 ~4 ms（15.77 vs 11.83 ms）。

评测方法与题面 3.2 对齐：固定设备/浏览器/语料/查询集，只改变被考察变量；体积按运行时实际下载文件统计；质量用 Recall@K / nDCG@K / MRR；分阶段计时并区分首次与预热；重复取中位数；WebGPU 可用与不可用两条路径分别验证。原始数据与截图在 `records/`。
