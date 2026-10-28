---
name: seekdb-retrieval-eval
description: 在 seekdb（OceanBase 开源的 AI 原生数据库）上量化评测检索效果时使用：把 JSONL 语料灌入 seekdb、一条 DDL 同时建 HNSW 向量索引与 ik 中文全文索引，然后对向量召回、全文召回、RRF 混合、加权融合四种策略跑同一批带标注的查询，输出 recall@k / MRR / nDCG@10 / precision@k 对比表、候选池与融合权重的扫描曲线，并按症状给出根因诊断（中文全文静默返回空、标量过滤压低指标、向量索引没被走到等）。当用户提到 RAG 召回质量、检索评测、指标、命中率、recall、MRR、nDCG、混合检索调参、seekdb 检索不准、向量检索与全文检索对比时使用。
version: "1.0.0"
license: "Apache-2.0"
tags: [seekdb, oceanbase, rag, retrieval-eval, vector-search, fulltext-search, hybrid-search, benchmark]
---

# seekdb 检索质量评测（seekdb-retrieval-eval）

给一个语料集和一组带标注的查询，这个技能回答三个问题：**向量腿、全文腿、混合各拿到多少 recall**，**融合权重与候选池应该定在哪**，以及**指标上不去的原因出在哪一层**。

所有召回都由 seekdb 自己执行（`approx` 向量检索 + `match against` 全文检索），融合与指标计算是纯 Python，不依赖任何框架。

## 何时使用

- 上线前要知道 RAG 检索层到底行不行，需要 recall@k / MRR 这类可比数字，而不是“看起来挺准”
- 混合检索调参：向量腿和关键词腿的权重、RRF 的 `k0`、候选池 `depth` 该定多少
- 怀疑中文全文检索没生效（`match against` 静默返回空是这个坑的典型表现）
- 换了 embedding 模型 / 改了切块粒度，要证明改动确实有收益
- 排查“结果不对”到底是索引没用上、还是召回策略选错

不适用：需要评测生成端答案质量（那是 LLM-as-judge 的事）、需要数据库内核级性能压测（TPS/延迟）、非 seekdb 数据库。

## 前置条件

```bash
# 1. 起一个 seekdb（任选其一）
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
# Windows 本机无 Docker 时：
# seekdb.exe --nodaemon --base-dir=<数据目录> --port=2881 --parameter memory_limit=2G --log-level=WARN

# 2. 依赖：Python 3.9+ 与 pymysql（技能本身只用标准库 + pymysql）
pip install pymysql

# 3. 连接信息（全部有默认值，本机默认 root / 空密码 / test 库）
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD= SEEKDB_DATABASE=test
```

## 30 秒跑通（自带示例数据）

```bash
cd seekdb-retrieval-eval

python scripts/build_index.py --corpus assets/sample/corpus.jsonl --table demo_kb
python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table demo_kb --out report
```

`build_index.py` 打印的收尾摘要（实测）：

```json
{ "table": "demo_kb", "docs_in_corpus": 36, "rows_stored": 36,
  "vector_dim": 256, "backend": "local", "fulltext_parser": "ik",
  "indexes": [{"name":"PRIMARY","column":"BTREE"},{"name":"ftidx","column":"FULLTEXT"},
              {"name":"vidx","column":"VECTOR"}] }
```

`evaluate.py` 输出对比表 + 自动诊断，并落 `report.md` / `report.json`：

| strategy | recall@1 | recall@10 | precision@10 | MRR | nDCG@10 | empty_result_rate |
|---|---|---|---|---|---|---|
| vector | 0.4583 | 0.8021 | 0.1438 | 0.8234 | 0.7477 | 0.0000 |
| fulltext | 0.5417 | 0.8542 | 0.1500 | 0.8854 | 0.8100 | 0.0000 |
| hybrid_rrf | 0.5104 | 0.8854 | 0.1562 | 0.8667 | 0.8175 | 0.0000 |
| hybrid_weighted | 0.5417 | 0.8958 | 0.1562 | 0.8906 | 0.8427 | 0.0000 |

> 上面这组数字来自 `backend=local`（离线自检后端，见下一节），它的作用是证明链路通、指标算得对、诊断能触发；**不是**语义检索质量的结论。

## 数据格式

语料 `corpus.jsonl`（每行一个 JSON，只有 `doc_id` 和 `body` 必需）：

```json
{"doc_id":"d08","title":"创建 HNSW 向量索引","body":"create vector index vidx on docs(embedding) with (distance=cosine, type=hnsw) ...","category":"vector"}
```

标注 `qrels.jsonl`（`relevant` 是这篇查询应命中的全部 doc_id，二元相关）：

```json
{"query_id":"q01","query":"创建向量索引的 SQL 怎么写","relevant":["d08","d07"],"tags":["vector"]}
```

写标注的两条纪律，否则指标没有意义：

1. **把该命中的都标上**，不要只标第一篇。二元相关下，漏标会让真正相关的结果被算成错。
2. **覆盖四类查询**：同义改写、专有名词、口语化提问、跨类目歧义。示例集 16 条查询就是按这四类配的。

## embedding 后端怎么选

`--backend` 决定文档向量和查询向量怎么来，两边必须同一个模型、同一维度。

| backend | 怎么配 | 用途 | 能不能拿来下结论 |
|---|---|---|---|
| `local` | 无需配置，默认 | 离线自检：跑通链路、验证指标与诊断逻辑 | **不能**，它是字符 n-gram 哈希的词法向量，没有语义泛化能力 |
| `api` | `EMBED_BASE_URL` / `EMBED_API_KEY` / `EMBED_MODEL`，OpenAI 兼容 `/v1/embeddings`（魔搭 API-Inference、硅基流动、vLLM、Ollama 都可） | 真实语义评测 | 能 |
| `seekdb` | `--model <实例里已注册的 AI 模型名>`，走库内 `ai_embed('<model>','<text>'[,<dim>])` | 不想把文本传出库时 | 能 |

`api` 后端会把结果缓存在 `EMBED_CACHE` 指向的文件里，重复评测不再花调用量；批量 16 条、失败重试 3 次。

## 四种策略与两个融合参数

- `vector`：`order by cosine_distance(embedding, ?) approx limit depth`
- `fulltext`：`where match(body) against(?)` + `order by relevance desc`（`--boolean-mode` 切到 boolean 语法，支持 `+词 -词`）
- `hybrid_rrf`：两腿各取 depth 条，`score = w/(k0+rank)` 相加；`--rrf-k0` 默认 60，`--w-vec` 给向量腿权重
- `hybrid_weighted`：两腿分数各自 min-max 归一化后 `a*vec + (1-a)*ft`，`--w-vec` 就是 `a`

```bash
# 融合权重扫描：a=0 等价纯全文，a=1 等价纯向量
mkdir -p sweep
for w in 0.0 0.3 0.5 0.7 1.0; do
  python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table demo_kb \
    --strategies vector,fulltext,hybrid_weighted --w-vec "$w" --out "sweep/w_$w" > /dev/null
done
grep -h hybrid_weighted sweep/w_*.md
```

在示例集上的实测扫描曲线（recall@10 / MRR / nDCG@10）：

| w_vec | recall@10 | MRR | nDCG@10 |
|---|---|---|---|
| 0.0（纯全文） | 0.8542 | 0.8854 | 0.8100 |
| 0.3 | **0.9167** | 0.8875 | 0.8393 |
| 0.5 | 0.8958 | 0.8906 | 0.8427 |
| 0.7 | 0.8646 | **0.9271** | 0.8395 |
| 1.0（纯向量） | 0.8021 | 0.8234 | 0.7477 |

结论形态很典型：**融合的最优 recall 高于任何单腿**，而 MRR 的最佳权重和 recall 的最佳权重不重合——先想清楚你优化的是“取全”还是“第一条就对”，再定 `a`。

## 诊断表：症状 → 根因 → 动作

`evaluate.py` 会把下面这些规则自动打出来，人工按表复核即可。

| 症状 | 大概率根因 | 验证方式 | 动作 |
|---|---|---|---|
| 中文查询全文腿大面积空结果，英文却能命中 | 建索引时没写 `with parser ik`，默认按空格切词，整句中文被当成一个词 | `show index from T` 看有没有 FULLTEXT；再 `explain` 是否出现 `TEXT RETRIEVAL SCAN` | 用 `--parser ik` 重建表；已入库数据要重建索引 |
| 加了 `--filter` 后所有策略 recall 一起塌 | 过滤范围与标注集不一致，指标上限被过滤压死 | 报告会打印“过滤使标注文档只有 X% 落在范围内” | 让过滤值与查询子集一致（示例：`--filter category=hybrid --only-tag hybrid`） |
| 向量腿 recall 远低于全文腿 | 词法型 embedding（含 `local`）没有语义泛化；或查询与文档用了不同模型 | 换成 `api` 后端重跑同一份标注 | 换真实 embedding 模型，或改切块粒度 |
| 结果对但越来越慢 | `approx` 没写成 / 距离函数与索引 `WITH (distance=...)` 不一致，优化器没走向量索引 | `explain` 应出现 `VECTOR INDEX SCAN T(vidx)` | 补 `approx` 关键字，统一距离度量 |
| 头部命中很准但取不全 | 候选池 `depth` 太小，第二名以后的相关文档根本没进入融合 | `--depth 10` 与 `--depth 100` 各跑一次 | 加大 `--depth`（必须 ≥ `--k`），再调 `--rrf-k0` |
| 向量列写不进去 / 距离算出乱值 | 维度与 `vector(N)` 定义不一致，或混用两个模型 | `build_index.py` 会直接拦住并报维度不一致 | 全量重建，统一维度 |

## 关键 SQL（全部在 seekdb v1.3.0.0 真机跑过）

```sql
-- 一条 DDL 同时建两种索引
create table demo_kb(
  doc_id varchar(64) primary key,
  title varchar(512),
  body varchar(4000) not null,
  category varchar(64),
  url varchar(1024),
  embedding vector(256) not null,
  fulltext index ftidx (body) with parser ik,
  vector index vidx (embedding) with (distance=cosine, type=hnsw)
);

-- 向量召回：approx 让它走 HNSW
select doc_id, cosine_distance(embedding, '[...]') score
from demo_kb order by cosine_distance(embedding, '[...]') approx limit 50;

-- 全文召回：relevance 取 BM25 风格分数
select doc_id, match(body) against('向量索引') relevance
from demo_kb where match(body) against('向量索引')
order by relevance desc limit 50;

-- 混合检索（RRF）也能写成一条 SQL
with v as (select doc_id, row_number() over(order by cosine_distance(embedding, '[...]')) rn
           from demo_kb order by cosine_distance(embedding, '[...]') approx limit 10),
f as (select doc_id, row_number() over(order by match(body) against('向量索引') desc) rn
      from demo_kb where match(body) against('向量索引'))
select doc_id, sum(1.0/(60+rn)) rrf
from (select * from v union all select * from f) t
group by doc_id order by rrf desc limit 5;
```

更多语法与踩坑（含 `@@ob_hnsw_ef_search`、AI 函数调用形式、SQL 审计取证）见 `references/sql-patterns.md`。

## 想进一步优化召回，按这个顺序

1. 先用 `--backend api` 把 embedding 换真，再看基线；
2. 索引层：确认 `explain` 命中索引，再动 `M` / `EF_CONSTRUCTION` / `@@ob_hnsw_ef_search`（默认 64）；
3. 词法层：中文同义词进自定义词典，`ft_stopword_file` 调整停用词；
4. 数据层：切块粒度、`title` 是否拼进 embedding 文本、脏数据；
5. 融合层：最后才调 `--w-vec` / `--rrf-k0`，并用权重扫描确认是 Interior 最优而不是端点；
6. 召回之后还嫌不够准，再上重排（交叉编码器或大模型），但别指望重排修好召回阶段的缺陷。

## 已验证 / 未验证

- 已验证：seekdb v1.3.0.0（Windows x64 单机，`root@2881`）跑完整证据链，见 `records/evidence/`；Docker 镜像同版本走同一套 SQL。
- 已验证：三种策略 SQL、`explain` 计划形态、ik 与默认分词器的差异、权重/深度扫描、过滤上限诊断。
- 未验证：`--backend api` 与 `--backend seekdb` 的真实模型输出维度取决于你的模型服务，本技能只保证调用形态与错误处理；示例数字不构成任何检索质量结论。
- 注意：`--parser none` 与 `with parser ik` 的对比必须在**重建表**之后做，`build_index.py` 默认会 `drop table`，追加模式请加 `--no-drop`。

## 目录

```
SKILL.md                     本文件
scripts/db.py                连接与 DDL（两种索引写在一个 CREATE TABLE 里）
scripts/embed.py             local / api / seekdb 三种 embedding 后端
scripts/build_index.py       建表 + 灌语料 + 摘要自检
scripts/evaluate.py          四策略评测、扫描、诊断、出 report.md/json
scripts/metrics.py           recall@k / MRR / nDCG@k / precision@k
scripts/run_evidence.sh      一键复现全部证据
references/sql-patterns.md   seekdb SQL 与索引语法速查（实测）
references/diagnostics.md    症状-根因-动作手册
references/metrics-and-tuning.md  指标定义与调参方法
assets/sample/               36 篇语料 + 16 条标注查询，可直接跑
```

<!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->
