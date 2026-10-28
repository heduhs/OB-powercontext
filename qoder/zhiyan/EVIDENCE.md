# 证据索引（EVIDENCE）

把 README / 站点里的**每一条主张**对应到可核对的文件与复现命令。评审不需要读代码就能抽查。

复现环境：Windows 10 x64，Python 3.12.10，seekdb v1.3.0.0（`127.0.0.1:2881`），PowerContext 1.4.1（`127.0.0.1:8000`）。
除标注"需要 Key"的条目外，全部命令**无需任何 API Key** 即可复现。

| # | 主张 | 证据文件 | 复现方式 |
| --- | --- | --- | --- |
| 1 | 语料真的存在 seekdb 里，且同时具备向量索引与中文全文索引 | `records/verification-offline.json` → `seekdb.indexes`；`records/ingest.log` | `python -m backend.ingest` |
| 2 | 数据库引擎版本 | `records/verification*.json` → `seekdb.engine` = `5.7.25-OceanBase seekdb-v1.3.0.0` | `select version()` |
| 3 | 向量腿真的走 HNSW 索引（不是全表精算） | `records/verification*.json` → `hybrid.explain.vector_plan` 含 `VECTOR INDEX SCAN zhiyan_docs(vidx)` | `curl "http://127.0.0.1:8010/api/explain?q=混合检索"` |
| 4 | 全文腿真的走 ik 全文索引 | 同上 → `fulltext_plan` 含 `TEXT RETRIEVAL SCAN zhiyan_docs(ftx)` | 同上 |
| 5 | 混合检索由两路召回融合而成，可核对每路贡献 | `records/verification*.json` → `hybrid.fusion_example[].vector_rank / fulltext_rank / matched_by`；网页结果徽章 | `VALIDATION.md` 样例 B |
| 6 | 混合优于单腿（语义向量） | `records/verification.log`：仅向量 0.4305 / 仅全文 0.8194 / **混合 0.8472** | `python scripts/verify.py --embed minimax`（需 `MINIMAX_API_KEY`） |
| 7 | 融合配置错了是负增益（不吹"混合一定更好"） | 同上：RRF w=0.5 → 0.7639，比纯全文低 0.0555；离线后端下混合 0.7986 < 纯全文 0.8194 | `python scripts/verify.py`（看 [2b] 扫描） |
| 8 | 中文全文漏 `with parser ik` 会静默返回空 | `VALIDATION.md` 样例 D 的命中对照表（4/1/1/1 vs 21/11/9/5） | 样例 D 的 SQL |
| 9 | 薄弱点记忆写在 PowerContext 服务端、可跨进程读回 | `records/verification*.json` → `memory.cross_process_read`（`found=true`，读取进程 PID 与写入进程不同） | `python scripts/verify.py`（[5] 段） |
| 10 | 记忆可跨天复用（非本次运行造的假象） | `records/verification-offline.json` → `memory.inventory.weak_points=11`，其中含 2026-10-27 写入的「B+树与B树的区别」等条目 | `python scripts/memory_probe.py "B+树与B树的区别"` |
| 11 | 复习评分驱动间隔重排（SM-2 lite） | `records/verification*.json` → `memory.after_review`（`version` 递增、`next_review` 推后、`review_count` +1） | `VALIDATION.md` 样例 F |
| 12 | 回答严格基于检索资料并带出处 | `records/verification*.json` → `answer.answer` / `answer.citations` / `answer.retrieved` | `VALIDATION.md` 样例 E |
| 13 | 单条 SQL 也能完成 RRF 融合（可下推到数据库） | `records/verification*.json` → `hybrid.explain.single_sql_rrf` | `GET /api/explain` |
| 14 | 网站公开、未登录可访问 | 站点 https://zhiyan-study-msv6fmmokx6.qoder.zone/ 返回 HTTP 200（未登录）；`site/` 为待发布的最新构建 | 无痕窗口打开站点 |
| 15 | 实时模式可核对（非纯静态演示） | `records/screenshots/live-hybrid-legs.png`（徽标显示 `seekdb(local) · 回答:extractive`，结果带两腿名次） | `python -m backend.app` 后打开 `127.0.0.1:8010` |
| 16 | 提测与判题结果 | `records/screenshots/test-record-20.png`；`DEV_RECORD.md` 末表（4 次提测对照） | `https://open.oceanbase.com/train/TestRecord?questionId=600078` |
| 17 | 开发过程由 Qoder 完成 | `records/qoder-conversation-log.md`、`records/qoder-screenshots/`、`DEV_RECORD.md`（含 Qoder UID） | Qoder 侧按 UID 核对 |

## 说明与边界

- **示例语料是合成数据**（`backend/sample_data.py` 顶部已声明），因此第 6/7 条的数字是"该管线在本语料上的实测结果"，
  不是对外部真实知识库的效果承诺。
- 评测集 12 条查询 / 48 篇文档，样本量小，**1~2 个点的差异属噪声**；本文只主张形态性结论
  （融合配置会改变结果、词法 embedding 下向量腿无增益）。
- 第 6 条需要 MiniMax Key；未执行时请以第 7 条的离线结果为准（离线条件下混合确实没有增益，我们如实写出）。
- 站点 PV/UV 属平台侧统计，未纳入本表主张。
