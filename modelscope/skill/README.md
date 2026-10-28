# seekdb-retrieval-eval

在 **OceanBase seekdb** 上量化评测检索效果的 Agent Skill：一条命令建库（HNSW 向量索引 + ik 中文全文索引），一条命令跑完向量 / 全文 / RRF 混合 / 加权混合四种召回策略，产出 recall@k、MRR、nDCG@10 对比表、权重与候选池扫描，并按症状给出根因诊断。

- 面向场景：RAG 上线前体检、混合检索调参、中文全文检索“查不到”排查、换 embedding 模型后证明收益。
- 依赖：Python 3.9+、`pymysql`、一个可连的 seekdb 实例（默认 `127.0.0.1:2881`）。无其他框架。
- 全部 SQL 与结论在 seekdb v1.3.0.0 真机验证；示例语料 36 篇 + 16 条标注查询随包携带，开箱可跑。

## 安装

魔搭 Skills 中心：

```bash
modelscope skills add seekdb-retrieval-eval
# 或直接把本目录放进你的技能目录（~/.agents/skills/ 或项目的 .qoder/skills/ 等）
```

## 最短路径

```bash
pip install pymysql
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD= SEEKDB_DATABASE=test

python scripts/build_index.py --corpus assets/sample/corpus.jsonl --table demo_kb
python scripts/evaluate.py  --qrels assets/sample/qrels.jsonl   --table demo_kb --out report
```

## 实测基线（backend=local，仅用于验证链路）

| strategy | recall@1 | recall@10 | MRR | nDCG@10 | empty_result_rate |
|---|---|---|---|---|---|
| vector | 0.4583 | 0.8021 | 0.8234 | 0.7477 | 0.0000 |
| fulltext | 0.5417 | 0.8542 | 0.8854 | 0.8100 | 0.0000 |
| hybrid_rrf | 0.5104 | 0.8854 | 0.8667 | 0.8175 | 0.0000 |
| hybrid_weighted | 0.5417 | 0.8958 | 0.8906 | 0.8427 | 0.0000 |

分词器消融（同一语料，全文索引不写 `with parser ik`）：全文腿 recall@10 从 **0.8542 → 0.3021**，空结果率 **0 → 0.5625**。

融合权重扫描（`--w-vec` = 向量腿权重）：

| w_vec | 0.0 | 0.3 | 0.5 | 0.7 | 1.0 |
|---|---|---|---|---|---|
| recall@10 | 0.8542 | **0.9167** | 0.8958 | 0.8646 | 0.8021 |
| MRR | 0.8854 | 0.8875 | 0.8906 | **0.9271** | 0.8234 |

> `local` 后端是字符 n-gram 哈希的词法向量，用来证明“跑得通、指标算得对、诊断能触发”。**语义检索质量结论请用 `--backend api` 或 `--backend seekdb`。**

## 换成真实 embedding

```bash
# OpenAI 兼容的 /v1/embeddings（魔搭 API-Inference、硅基流动、vLLM、Ollama 均可）
export EMBED_BASE_URL=https://api-inference.modelscope.cn/v1
export EMBED_API_KEY=ms-xxxxxxxx
export EMBED_MODEL=<你的 embedding 模型>
export EMBED_CACHE=$PWD/embed_cache.json

python scripts/build_index.py --corpus my_corpus.jsonl --table kb --backend api
python scripts/evaluate.py   --qrels my_qrels.jsonl   --table kb --backend api
```

## 用库内 AI 函数

```bash
python scripts/build_index.py --corpus my_corpus.jsonl --table kb \
  --backend seekdb --model <实例中已注册的 AI 模型名>
```

对应 SQL 形态（v1.3.0.0 实测）：`select ai_embed('<模型名>', '<文本>'[, <维度:int>])`。模型不存在时报 `ai model not found, please check if the model exists`。

## 常用开关

```
--strategies vector,fulltext,hybrid_rrf,hybrid_weighted   选择要跑的策略
--k 10            指标截断位      --depth 50   每条召回腿的候选数（必须 >= --k）
--w-vec 0.5       向量腿权重      --rrf-k0 60  RRF 常数
--boolean-mode    全文腿用 boolean 语法（+词 -词）
--filter category=hybrid          标量过滤（报告会给出过滤后的 recall 上限）
--only-tag hybrid                 只用某个 tag 的查询，常与 --filter 配对
--parser ik|none                  建索引时的全文分词器（none 用于复现中文静默空结果）
--out reports/run1                report.md / report.json 前缀
```

一键复现全部证据：

```bash
bash scripts/run_evidence.sh
```

## 目录

```
SKILL.md                      Agent 使用的说明（含诊断表与 SQL）
scripts/db.py                 连接 + 单条 CREATE TABLE 同时建两种索引
scripts/embed.py              local / api / seekdb 三种 embedding 后端
scripts/build_index.py        建表、灌语料、自检摘要
scripts/evaluate.py           四策略评测 + 扫描 + 诊断 + 报告
scripts/metrics.py            recall@k / precision@k / MRR / nDCG@k
scripts/run_evidence.sh       一键复现证据矩阵
references/sql-patterns.md    seekdb SQL 速查（实测）
references/diagnostics.md     症状 → 根因 → 验证 → 动作
references/metrics-and-tuning.md  指标定义与调参纪律
assets/sample/                36 篇语料 + 16 条标注查询
records/evidence/             本次实测的原始 report.json / md
```

## 已知边界

- 小语料（示例集 36 篇）上 `--depth` 几乎不影响结果，调候选池要等语料上千篇再看信号。
- 二元相关指标对标注完整性敏感：漏标会压低所有策略，泛标会让策略间差异消失。
- `--backend api` 的可用性取决于你的 embedding 服务；本技能只做批量调用、重试与缓存，未针对某一家服务做适配层。
- 全文相关度的 `relevance` 由 seekdb 计算，本技能不做 BM25 复算，跨库对比时不要直接比这个分数。

## 许可

Apache-2.0。
