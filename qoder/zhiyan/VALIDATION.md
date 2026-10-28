# 验证样例（VALIDATION）

赛题要求：**至少一组可按步骤执行的样例，包含初始数据、输入、操作步骤、预期结果，以及指定技术发挥作用的记录。**

下面 8 组样例全部在本机实测通过（seekdb v1.3.0.0 + PowerContext 1.4.1 + Python 3.12.10，Windows 10）。
**A–G 不需要任何 API Key。** 每组都标明"该结果证明哪个技术真的在起作用"。

前置：按 `README.md` 第 5 节启动 seekdb、PowerContext，并在 `qoder/zhiyan/` 目录下操作。

---

## 样例 A：建库与索引自检（seekdb）

**初始数据**：`backend/sample_data.py` 内置 48 条合成语料（18 文献 / 24 笔记 / 6 实验）。

**操作**

```bash
python -m backend.ingest
```

**预期结果**（实测输出）

```
seekdb 导入 48 篇，库内现有 48 篇，向量维度 256
embedding: local / char-ngram-hashing
索引: [{'name': 'PRIMARY', 'column': 'doc_id', 'type': 'BTREE'},
       {'name': 'ftx', 'column': 'text_body', 'type': 'FULLTEXT'},
       {'name': 'vidx', 'column': 'embedding', 'type': 'VECTOR'}]
```

**技术作用**：三种索引齐全，说明向量与全文两条腿都真的建在 seekdb 里，不是应用层模拟。

---

## 样例 B：混合检索与"两腿名次"核对（seekdb）

**输入**：`HNSW 索引参数 efSearch 召回率`

**操作**

```bash
curl -s http://127.0.0.1:8010/api/search -H "Content-Type: application/json" \
     -d "{\"query\":\"HNSW 索引参数 efSearch 召回率\",\"top_k\":3,\"mode\":\"hybrid\"}"
```

或在网页「智能检索」输入同一查询（模式选「混合检索」）。

**预期结果**（实测，顺序稳定）

| doc_id | 标题 | 向量腿名次 | 全文腿名次 | 融合得分 |
| --- | --- | --- | --- | --- |
| doc-09 | 实验记录：向量检索性能对比 | #3 | #1 | 0.903167 |
| doc-46 | 实验记录：图索引参数扫描 | #1 | #4 | 0.461309 |
| doc-02 | Retrieval-Augmented Generation… | #2 | #6 | 0.312268 |

网页上每条结果会显示 `向量 #n` `全文 #m` `两路命中` 徽章。

**技术作用**：结果里同时带有两条腿各自的名次——可以直接看出"这条是被全文命中的、那条是两路共同命中的"，
这是"向量+全文混合"真实发生的最直接证据。

---

## 样例 C：三种召回对比 + 融合扫描（seekdb，核心样例）

**操作**

```bash
python scripts/verify.py                 # 离线词法向量
# 或（可选）python scripts/verify.py --embed minimax     # 需 MINIMAX_API_KEY
```

**预期结果**（实测，`verify.py` 会打印同样两张表并写入 `records/`）

`records/verification-offline.log`（零依赖）：

| 召回方式 | recall@5 |
| --- | --- |
| 仅向量 | 0.6250 |
| 仅全文 | 0.8194 |
| 混合（默认 weighted w=0.25） | 0.7986 |
| 混合（RRF w=0.5） | 0.7986 |
| 本次扫描最优 | rrf w=0.0 → 0.8194 |

`records/verification.log`（MiniMax embo-01 语义向量）：

| 召回方式 | recall@5 |
| --- | --- |
| 仅向量 | 0.4305 |
| 仅全文 | 0.8194 |
| **混合（默认 weighted w=0.25）** | **0.8472** |
| 混合（RRF w=0.5） | 0.7639 |

**判读**：语义向量下混合超过最好单腿（+0.0278）；词法向量下混合无增益。
同一批两腿数据，融合配置换掉会差 0.0833 —— 所以权重必须由评测决定，这正是本项目把扫描写进脚本的原因。

---

## 样例 D：中文全文分词器消融（seekdb 的关键坑）

**操作**（在同一库内建一张不加 `with parser ik` 的对照表，插入同样 48 篇语料）

```sql
create table zhiyan_no_ik(
  doc_id varchar(64) primary key,
  text_body varchar(4000) not null,
  fulltext index ftx (text_body));        -- 注意：没有 with parser ik
-- 插入与 zhiyan_docs 相同的 text_body 后：
select count(*) from zhiyan_no_ik where match(text_body) against('HNSW 索引参数 efSearch 召回率');
select count(*) from zhiyan_docs   where match(text_body) against('HNSW 索引参数 efSearch 召回率');
select count(*) from zhiyan_no_ik where match(text_body) against('Attention');
```

**预期结果**（本机实测命中条数）

| 查询 | 无 ik | 有 ik |
| --- | --- | --- |
| `HNSW 索引参数 efSearch 召回率` | 4 | 21 |
| `两阶段提交 协调者 原子性` | 1 | 11 |
| `LoRA 低秩 参数高效微调` | 1 | 9 |
| `PagedAttention KV Cache 显存碎片` | 1 | 5 |
| `Attention`（英文） | 1（正常命中） | — |

**判读**：默认分词按空格切，整句中文被当成一个词，中文查询几乎全灭，而英文照常命中——
说明索引存在、只是中文不可切分。**这个故障不报错**，是本类项目最容易踩且最难发现的坑。

---

## 样例 E：基于资料的问答（seekdb 检索 + 回答引擎）

**输入**：`RAG 是如何减少大模型幻觉的？`

**操作**：网页「AI 问答」提交，或

```bash
curl -s http://127.0.0.1:8010/api/ask -H "Content-Type: application/json" \
     -d "{\"question\":\"RAG 是如何减少大模型幻觉的？\",\"top_k\":3}"
```

**预期结果**：`engine` 为 `extractive`（无 Key）或 `MiniMax-M1`（有 Key）；答案句子末尾带 `[编号]`，
`citations` 与 `sources` 给出 3 条出处（含 doc-02《Retrieval-Augmented Generation…》）。
无 Key 时实测首句为：

```
RAG 将参数化记忆（预训练 seq2seq 模型）与非参数化记忆（可检索的稠密向量索引）结合。 [1]
```

**技术作用**：回答内容完全来自 seekdb 检索回的段落，可逐句回溯到 `doc_id`。

---

## 样例 F：薄弱点记忆与间隔重复（PowerContext）

**操作**

1. 网页「薄弱知识点」输入 `MVCC 如何实现可重复读隔离`，来源填 `数据库课程笔记 第7讲`，点「标记薄弱点」；
2. 在该条目上点评分 `5`；
3. 再标记一条并评分 `1`。

**预期结果**（实测）

- 标记后：`review_count=0`，`next_review=今天`，`version=1`，出现在「今日复习」；
- 评 5 后：`review_count=1`，`interval_days=1`，`next_review=明天`，`version` 递增（实测 v1→v3，
  因为写入状态与更新状态各占一个版本）；
- 评 1 后：间隔重置为 1 天，`ease` 下调，仍留在复习计划里。

**技术作用**：状态与事件都落在 PowerContext（Memory 存状态、Source 存流水），不是浏览器本地存储。

---

## 样例 G：跨进程复用（PowerContext 持久性的硬证据）

**操作**

```bash
python scripts/verify.py        # 其中 [5] 段会 fork 一个独立进程回读
python scripts/memory_probe.py "验证用薄弱点 HHMMSS"   # 也可单独运行（参数为刚才写入的条目名）
```

**预期结果**（实测，写入进程与读取进程 PID 不同）

```json
{"found": true, "pid": 20984, "total_weak_points": 10, "version": 1, "next_review": "2026-10-28"}
```

**技术作用**：另一个操作系统进程能读回刚写入的记忆 → 证明是服务端持久化。
本项目还留有更长期的一条证据：`records/verification-offline.json` 的 `inventory.weak_points=11`
里包含**前一天**（10-27）标记的条目（如「B+树与B树的区别」），跨天、跨多次重启仍在同一 scope 下可读。

---

## 样例 H：网站公开可访问（Qoder Sites）

**操作**：用浏览器**无痕窗口**打开 https://zhiyan-study-msv6fmmokx6.qoder.zone/

**预期结果**：无需登录即可打开；右上角显示「演示模式 · 示例数据」；检索、问答、薄弱点、复习计划四个
标签页均可交互；导航含「检索实测」，其中数字与 `records/verification*.log` 一致；页脚含代码仓库链接。

**技术作用**：满足"项目网站通过 Qoder Sites 发布、未登录也能打开"，且网站内容可由评审逐条核对。

---

## 排查提示

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 中文检索恒为 0 条 | 建索引漏了 `with parser ik` | 用 `backend/db.py` 的 DDL 重建（样例 D 可复现该坑） |
| 启动时 `Unknown database 'zhiyan'` | 未建库 | `python -m backend.ingest` 会自动建库建表 |
| `/api/health` 报 seekdb 不可用 | seekdb 未启动或端口不是 2881 | 检查 `SEEKDB_HOST/PORT` |
| 徽标显示 `extractive` 而非 `MiniMax-M1` | 未设 `MINIMAX_API_KEY` | 属正常降级，不影响验收 |
| 记忆读写报连接错误 | PowerContext 服务未起 | `powercontext server run --env-file .env` |
| 向量维度与表定义不符（换后端后） | 表是 256 维、新模型 1536 维 | `python -m backend.ingest` 会自动重建表 |
