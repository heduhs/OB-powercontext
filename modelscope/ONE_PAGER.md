# seekdb-retrieval-eval · 技术一页纸

**一句话**：在 seekdb 上一键量化评测检索效果——四种召回策略、两组参数扫描、按症状的根因诊断，全部 SQL 真机验证。

## 问题

做 RAG 的人到了 seekdb 上普遍卡在同一个位置：**检索到底行不行，没有数字**。
官方文档给了语法，社区 Skill 大多是"语法速查 + 提示词"，但把 SQL 写对不等于检索效果好；而中文场景最常见的故障——`match against` 静默返回空——连报错都没有，靠人工 review 发现不了。

## 方案

一个 Agent Skill，把"建库 → 召回 → 融合 → 指标 → 诊断"做成可执行流水线：

```
corpus.jsonl ──build_index.py──▶ seekdb 表（vector(N) + HNSW + 中文 ik 全文，单条 DDL）
qrels.jsonl  ──evaluate.py────▶ 向量腿 / 全文腿 / RRF / 加权融合 ─▶ recall@k·MRR·nDCG@10 ─▶ report.md+json+诊断
```

设计取舍：

| 决策 | 原因 |
| --- | --- |
| 召回全部交给 seekdb，只有融合与指标在 Python | 评测对象是数据库的检索能力，不能在客户端复算 BM25 |
| 两种索引写进同一条 `CREATE TABLE` | 在已有全文索引的表上单独补建向量索引，本机复现过实例后台线程异常退出并断连 |
| 三种 embedding 后端（`local`/`api`/`seekdb`） | `local` 让链路在没有网络和模型的情况下也能自检；`api`/`seekdb` 才出真实结论——文档里把这条边界写死，防止拿自检数字当质量结论 |
| 同时实现 RRF 与加权融合 | RRF 只用名次、对分数量纲免疫；加权融合保留分数信息但需 min-max 归一化。让数字说话而不是站队 |
| 标识符白名单校验表名与分词器 | SQL 标识符无法参数化绑定，公开工具必须堵住拼接注入面 |

## 实测（seekdb v1.3.0.0 / Windows / 36 篇语料 / 16 条标注查询）

| strategy | recall@1 | recall@10 | MRR | nDCG@10 |
| --- | --- | --- | --- | --- |
| vector | 0.4583 | 0.8021 | 0.8234 | 0.7477 |
| fulltext | 0.5417 | 0.8542 | 0.8854 | 0.8100 |
| hybrid_rrf | 0.5104 | 0.8854 | 0.8667 | 0.8175 |
| hybrid_weighted | 0.5417 | **0.8958** | 0.8906 | **0.8427** |

- **分词器消融**：`with parser ik` vs 默认空格分词 → 全文腿 recall@10 **0.8542 → 0.3021**，空结果率 **0 → 0.5625**（不报错）。
- **权重扫描**：recall 峰在 `w_vec=0.3`（0.9167，高于任一单腿），MRR 峰在 `w_vec=0.7`（0.9271）→ 两个目标不重合。
- **过滤陷阱**：`--filter category=hybrid` 不加 `--only-tag` 时 recall@10 只有 0.125，工具直接算出"过滤后上限 17%"并打在报告里。
- **候选池**：36 篇小语料上 depth 10→100 无信号，这一"没效果"也如实写进文档（避免过度承诺）。
- **explain 形态**：`VECTOR INDEX SCAN t(vidx)`、`TEXT RETRIEVAL SCAN t(ftidx) + calc_relevance=true` 作为"索引真的被用上"的判据。

## 诚实边界

- `backend=local` 是词法哈希向量，表中数字只用于证明链路、指标与诊断成立，**不是**语义检索质量结论。
- 小样本（16 条查询）下 1~2 个点的差异属噪声；结论形态（融合优于单腿、分词器影响巨大）稳健，绝对值不稳健。
- `--backend api` / `--backend seekdb` 的可用性取决于用户的模型服务与 AI 模型注册方式，Skill 只保证调用形态、缓存、重试与错误处理。

## 复用价值

- 对 seekdb 用户：一套可以直接接到 CI 里的检索回归基线（`report.json` 可 diff）。
- 对 Agent：`references/diagnostics.md` 把"症状→根因→验证→动作"写成表，模型可以照着自主排查，而不是背 SQL。
- 对本大赛：混合检索的召回质量与单 SQL 融合写法（CTE + `row_number()` + RRF）都有真机取证，可直接用于 powercontext / seekdb 相关赛题的验证环节。
