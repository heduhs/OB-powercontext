# EdgeKB —— 端侧 AI 推理 + seekdb 混合检索的迷你知识库

> seekdb × 阶跃星辰 赛题作品：文档在**用户浏览器本地**完成向量化（Transformers.js / ONNX Runtime Web，
> WebGPU 优先、WASM 自动降级），向量与原文写入 **seekdb**，查询由 seekdb 在**数据库内部**完成
> 向量 + 全文的 RRF 混合检索。全程不调用任何云端推理 API。

## 1. 30 秒跑起来

```bash
cd stepfun/app
npm install                 # 只依赖 seekdb JS SDK（含 mysql2）
npm run setup               # 下载开源 ONNX 模型(fp32/fp16/q8) + 端侧运行时到本地
npm run db:server           # 启动本地 seekdb server（127.0.0.1:2881，首次初始化约 1–2 分钟）
npm start                   # 打开 http://127.0.0.1:8630
```

浏览器打开后：**加载模型 → 载入示例语料 → 向量化并写入 seekdb → 输入查询 → 检索**。
右上角三个徽标实时显示推理后端（WebGPU/WASM）、模型与精度、seekdb 连接方式。

评测页 `http://127.0.0.1:8630/eval.html` 一键复现全部实测数据（量化对比 + 分阶段计时 + 优化消融），
并可把原始数据保存为 `records/bench-*.json`。

### 已经验证过的环境

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 10 (19045) x64 |
| 浏览器 | Chrome / Edge 113+（本次实测 Chrome 150，WebGPU adapter 可用且支持 `shader-f16`） |
| Node.js | 20+（本次实测 v22.20.0） |
| seekdb | 1.3.0.0（server 模式，MySQL 协议 `127.0.0.1:2881`） |
| seekdb JS SDK | `seekdb@1.4.0`（`SeekdbClient` / `Collection.hybridSearch` / `refresh_index`） |
| 端侧运行时 | `@huggingface/transformers@4.3.2` + ONNX Runtime Web（WebGPU / WASM） |
| 模型 | `Xenova/bge-small-zh-v1.5`（512 维，中英混排；Apache-2.0） |

## 2. 目录结构

```
stepfun/
├── README.md            本文件：启动、复现、配置
├── ONE_PAGER.md         一页技术说明（模型与量化 / 端侧运行 / seekdb 使用 / 状态与优化）
├── VALIDATION.md        验收对照表：每一项要求 → 怎么看、在哪验证、证据在哪
├── records/             实测证据（模型资产清单、评测原始数据、检索 SQL 证据、截图）
└── app/
    ├── server.mjs       本地服务：静态资源 + REST API（不做任何推理）
    ├── lib/config.mjs   全部可配置项（环境变量）
    ├── lib/store.mjs    seekdb 接入层：collection 建表/写入/刷新/混合检索
    ├── public/
    │   ├── index.html / app.js / styles.css      主应用
    │   ├── eval.html  / eval.js  / metrics.js    评测台（Recall/nDCG/MRR + 计时 + 消融）
    │   ├── embedder.worker.js                    浏览器内推理（模型加载、后端选择、批处理）
    │   ├── embedder.js                           worker 门面、分块、REST 客户端
    │   └── models.js                             模型登记表（来源、维度、池化、查询前缀）
    ├── data/corpus/                              示例语料（开源项目文档节选 + 作者笔记，51 篇）
    ├── data/edge-notes.json                      作者撰写语料的源文件
    ├── eval/queries.json                         30 条查询 + 文档级人工标注（评测集）
    └── scripts/
        ├── fetch-assets.mjs                      下载模型/运行时并生成 sha256 体积清单
        ├── build-corpus.mjs                      语料生成方式（可复现）
        └── start-seekdb.mjs                      本地 seekdb server 启动脚本
```

## 3. 配置项

全部通过环境变量设置，**不含任何凭据**。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` / `HOST` | `8630` / `127.0.0.1` | 应用监听地址 |
| `SEEKDB_MODE` | `server` | `server`（本地 seekdb 服务）或 `embedded`（SDK 原生嵌入式） |
| `SEEKDB_HOST` / `SEEKDB_PORT` | `127.0.0.1` / `2881` | server 模式地址 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | `root` / 空 | server 模式账号 |
| `SEEKDB_DATABASE` | `stepfun_kb` | 库名，服务启动时自动 `CREATE DATABASE IF NOT EXISTS` |
| `SEEKDB_PATH` | `app/.data/seekdb.db` | embedded 模式数据目录 |
| `KB_DIM` / `KB_DISTANCE` | `512` / `cosine` | 向量维度与距离度量，需与模型一致（换 `all-MiniLM-L6-v2` 用 `384`） |
| `KB_ANALYZER` / `KB_IK_MODE` | `ik` / `smart` | 全文索引分析器（中文用 `ik`，也可 `ngram2`/`space`） |
| `KB_MAX_BATCH_ROWS` | `500` | 单次 `collection.add` 的最大行数 |
| `SEEKDB_QUERY_TIMEOUT` | `60000` | seekdb 查询超时（毫秒） |
| `KB_RECORDS_DIR` | `stepfun/records` | 评测数据落盘目录 |

## 4. 复现实测数据

1. `npm run setup -- --dtypes fp32,fp16,q8 --vendor`：把三种精度的 ONNX 权重与端侧运行时全部拉到本地，
   并写出 `records/assets-manifest.json`（每个文件的字节数 + sha256，体积口径以此为准）。
   网络受限时加 `--endpoint https://hf-mirror.com`。
2. 打开 `/eval.html`，勾选 4 个配置（fp32+WASM 基线、q8+WASM、fp16+WebGPU、q8+WebGPU），点击“开始评测”。
   评测台会：浏览器内切块 → 本地向量化 51 篇语料（约 120 条切片）→ 写入 seekdb → 索引刷新 →
   对 30 条查询分别做 hybrid / vector / text 三种检索 → 计算 Recall@1/3/5/10、nDCG@10、MRR、Hit@1 →
   分阶段计时（模型加载 / 首批 vs 稳态向量化 / 写入 / 刷新 / 检索 p50 p95 / 端到端）→ 追加优化项消融。
3. 点击“写入 records/*.json”保存原始数据；本次运行结果与截图已存放在 `records/`。

要更换语料：把 `.txt/.md` 拖进主页面即可（同样在浏览器内切块与向量化）。

## 5. 边界与诚实声明

- 向量化 100% 发生在用户设备：只有向量、原文与元数据会被发送给 seekdb；服务端与浏览器都不调用任何推理 API。
- 混合检索的两路召回与 RRF 融合都在 seekdb 内部完成（`collection.hybridSearch` →
  `DBMS_HYBRID_SEARCH.GET_SQL()` 返回融合 SQL → 由数据库执行），可用“显示 seekdb 侧 SQL 证据”勾选后查看
  `gv$ob_sql_audit` 抓到的真实语句。
- 全文检索字符串会被 seekdb 拼进 `match(...) against('…')`，因此服务端对文本腿做了字符白名单过滤，
  避免引号破坏语句（`sanitizeTextLeg`）。
- `embedded` 模式依赖 `@seekdb/js-bindings` 的原生运行时（首次使用会从 OceanBase 官方构建源下载约 90 MB），
  实测在本机冷启动较慢，故默认使用 server 模式；两条路径都保留在配置里。
