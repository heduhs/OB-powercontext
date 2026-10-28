# 验收对照 · EdgeKB（seekdb × 阶跃星辰）

评审路径：`stepfun/app` 下 `npm install && npm run setup && npm run db:server && npm start`，浏览器打开 `http://127.0.0.1:8630`。
本机实测环境：Windows 10 x64、Chrome 156（含 headless=new）、Node 22、seekdb 1.3.0.0、seekdb-js 1.4.0。

## 题面 2.1–2.5 与 3.1 检查项

| 检查项 | 在哪验证 | 预期现象 |
| --- | --- | --- |
| 完整流程 | 主页面 1→2→3 步；`npm run smoke`（脚本化同一操作） | 加载模型 → 51 篇语料切成 98 片 → 本地向量化写入 → 查询返回排序结果与原文 |
| 本地推理 | 界面徽标「推理后端：WEBGPU/WASM」；`records/run-smoke.json` 的 `engine` 字段；网络面板只出现同源请求 | 文档与查询向量均由浏览器计算，无任何推理 API |
| 模型量化 | `/eval.html` 第 3 节体积表 + `records/assets-manifest.json`（含 sha256） | fp32 90.5 MB / fp16 45.3 MB(2.00×) / q8 22.9 MB(3.95×)，并给出同一评测集上的质量差 |
| 后端与降级 | 推理后端选「强制 WASM」再加载；或 `npm run degrade` | 显示 `WASM · q8 · N 线程`，写入与检索照常完成；`auto` 时优先 WebGPU |
| seekdb 数据层 | 勾选“显示 seekdb 侧 SQL 证据”后检索；`records/run-smoke.json` 的 `evidence` | `SET @search_parm` → `DBMS_HYBRID_SEARCH.GET_SQL()` → 单条融合 SQL（`match ... against` FULL JOIN `cosine_distance` + RRF） |
| 状态与刷新 | 主页面「索引刷新」按钮、知识库新建/删除、`/api/collections`；导入结束时的“刷新前/刷新后探针” | 增量写入后刷新，切片行数与检索结果与当前知识库一致，切换/删除后不串库 |
| 可复现性 | 本文件 + `README.md`；`npm run bench` 一键重跑评测 | 按说明可启动应用与数据库并完成上述全部操作 |

## 性能与质量测试方法（题面 3.2）

| 要求 | 实现 |
| --- | --- |
| 固定测试条件 | 评测台把 UA、核数、是否跨源隔离、WebGPU adapter、seekdb 版本、SDK 版本、语料规模、切片参数一并写入 `records/bench-*.json` 的 `environment` |
| 对比模型体积 | 同一口径统计运行时真正下载的 onnx + 分词器 + 配置文件；压缩比 = fp32 权重 / 该精度权重 |
| 检查检索质量 | 30 条查询 × 文档级标注，指标 Recall@1/3/5/10、nDCG@10、MRR、Hit@1；fp32 与量化配置对比，并给 Δ 百分点 |
| 分阶段计时 | 模型加载、首批/稳态向量化、写入、索引刷新、查询向量化、seekdb 检索 p50/p95、端到端分别记录 |
| 首次与后续区分 | `e2e_first_ms` 与 `e2e_median_ms` 分列；向量化首批与稳态批次分列；每条查询重复 N 次取中位数 |
| 降级路径验证 | 评测集合同时跑 `fp32/q8 + WASM` 与 `fp16/q8 + WebGPU`；`npm run degrade` 专门验证强制 WASM 的完整流程 |
| 建议表格 | `/eval.html` 第 3–6 节渲染出的正是题面建议的「配置 / 体积 / 吞吐 / 检索延迟 / 端到端 / 质量」表 |

## 提测结果

| 项 | 值 |
| --- | --- |
| 赛题 | 生态伙伴 2026 年数据库大赛题目：阶跃星辰（questionId 600073） |
| 仓库 | `https://github.com/heduhs/OB-powercontext`（大赛报名登记的团队仓库） |
| Branch | `2026-competition-ecosystem` |
| Commit | `8b9eb01646e0783b53d7a822e3eabb3c0eaf85fe` |
| 提测时间 | 2026-10-28 16:46 |
| 任务状态 | 执行成功（结果：1 成功 / 0 失败） |
| 成绩 | **20.000 / 20**（最高成绩 = 最新成绩，提测次数 1） |

可在平台「查看提测记录」页复核：`https://open.oceanbase.com/train/TestRecord?questionId=600073`。

## 一键复现脚本

```bash
cd stepfun/app
npm run bench          # headless Chrome 跑完 4 个配置 + 消融，自动写 records/bench-*.json
npm run smoke          # 主流程端到端 + 截图 + seekdb SQL 证据
npm run degrade        # 强制 WASM 的降级路径验证
```
