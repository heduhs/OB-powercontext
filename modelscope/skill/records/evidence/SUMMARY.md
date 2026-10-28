# 实测证据汇总

复现方式：`bash scripts/run_evidence.sh`（该脚本按顺序产出本目录全部文件）。

环境：OceanBase **seekdb v1.3.0.0**，Windows 10 x64 单机 `seekdb.exe --port=2881 --parameter memory_limit=2G --log-level=WARN`，Python 3.12 + PyMySQL，`backend=local`、`dim=256`、语料 36 篇、标注查询 16 条（相关文档共 23 篇）。
建表自检：`show index` 回读为 `PRIMARY/BTREE`、`ftidx/FULLTEXT`、`vidx/VECTOR` 三项齐全。

## 1. 基线（`run_default`，depth=50，k=10）

| strategy | recall@1 | recall@5 | recall@10 | precision@10 | MRR | nDCG@10 | empty | 平均延迟 ms |
|---|---|---|---|---|---|---|---|---|
| vector | 0.4583 | 0.7708 | 0.8021 | 0.1438 | 0.8234 | 0.7477 | 0.0 | 1.6 |
| fulltext | 0.5417 | 0.8333 | 0.8542 | 0.1500 | 0.8854 | 0.8100 | 0.0 | 1.6 |
| hybrid_rrf | 0.5104 | 0.8333 | 0.8854 | 0.1562 | 0.8667 | 0.8175 | 0.0 | 1.6 |
| hybrid_weighted | 0.5417 | 0.8333 | 0.8958 | 0.1562 | 0.8906 | 0.8427 | 0.0 | 1.6 |

形态：两条混合腿都优于任一单腿，本例最优是加权融合（recall@10 相对最好单腿 +0.0416）。

## 2. 中文分词器消融（`run_no_ik`）

同一份语料，全文索引分别用 `with parser ik` 与默认空格分词：

| 全文索引写法 | fulltext recall@10 | fulltext empty_result_rate |
|---|---|---|
| `fulltext index ftidx (body) with parser ik` | 0.8542 | 0.0 |
| `fulltext index ftidx (body)`（默认空格分词） | **0.3021** | **0.5625** |

这是本技能最想传达的一条经验：默认分词器下中文 `match against` **静默返回空、不报错**，只靠 review SQL 很难发现；向量腿不受影响（0.8021 不变），因此“混合腿看起来还行”会掩盖关键词腿已经废掉的事实。

## 3. 融合权重扫描（`run_wvec_*`）

`--w-vec` = 向量腿权重（0.0 等价纯全文，1.0 等价纯向量）：

| w_vec | recall@10 | MRR | nDCG@10 |
|---|---|---|---|
| 0.0 | 0.8542 | 0.8854 | 0.8100 |
| 0.3 | **0.9167** | 0.8875 | 0.8393 |
| 0.5 | 0.8958 | 0.8906 | 0.8427 |
| 0.7 | 0.8646 | **0.9271** | 0.8395 |
| 1.0 | 0.8021 | 0.8234 | 0.7477 |

recall 峰在 0.3、MRR 峰在 0.7，两个目标不在同一点 → 先定业务指标再定权重。

## 4. 候选池深度扫描（`run_depth_*`）

| depth | vector | fulltext | hybrid_rrf | hybrid_weighted |
|---|---|---|---|---|
| 10 | 0.8021 | 0.8542 | 0.8958 | 0.8958 |
| 20 | 0.8021 | 0.8542 | 0.8854 | 0.8646 |
| 50 | 0.8021 | 0.8542 | 0.8854 | 0.8958 |
| 100 | 0.8021 | 0.8542 | 0.8854 | 0.8958 |

诚实结论：**36 篇的小语料上 depth 没有可辨识信号**，20/50 之间的抖动是噪声。候选池调优要在千篇级以上才有意义（对应 `references/diagnostics.md` 的 E 节）。

## 5. 标量过滤与标注集一致性（`run_filter_matched` / `run_filter_unmatched`）

| 场景 | filter_ceiling | vector | fulltext | hybrid_weighted |
|---|---|---|---|---|
| `--filter category=hybrid --only-tag hybrid`（过滤值与查询子集一致） | 1.0 | 1.0000 | 0.7500 | 1.0000 |
| `--filter category=hybrid`（不加 `--only-tag`，与标注不一致） | **0.1724** | 0.1250 | 0.0938 | 0.1250 |

同一个过滤条件，指标差 8 倍，差别不在检索而在评测口径。报告会把上限算出来：
`标量过滤 category=hybrid 使标注文档只有 17% 落在过滤范围内，这就是本轮 recall 的上限`。

## 6. 预检与错误路径（逐条人工触发）

| 场景 | 行为 |
|---|---|
| 表不存在 | `表 'no_such_table' 不存在（或不在 test 库）。先跑 build_index.py --table ...` |
| 表上没有 FULLTEXT / VECTOR 索引 | 直接拒绝并给出对应建索引语句，避免把驱动报错当成检索问题 |
| `--depth < --k` | `--depth 必须 >= --k，否则混合检索的候选池被截断` |
| 未知策略名 | 列出可选策略 |
| `--filter` 列不存在 | `过滤列 'secret' 不在 demo_kb 的列里` |
| 表名/分词器含非法字符 | 标识符白名单拦截（`表名 'a;drop' 非法`、`--parser 'ik) x' 非法`），SQL 不做字符串拼接注入 |
| `--backend api` 缺环境变量 | 打印需要设置的三个变量与示例 |
| corpus 中 doc_id 重复 / embedding 维度不一致 | 建库阶段直接拦截 |

## 7. 语法与执行计划取证

`explain` 实测输出（完整文本见 `../../references/sql-patterns.md` 第 7 节）：

- 向量检索 → `VECTOR INDEX SCAN demo_kb(vidx)`
- 全文检索 → `TEXT RETRIEVAL SCAN demo_kb(ftidx)`，并带 `calc_relevance=true`、`sort_keys([MATCH(...), DESC])`
- 单条 SQL 的 RRF 混合检索（CTE + `row_number()` + `union all` + `group by`）在 v1.3.0.0 上可直接执行并返回融合排序
- `select @@ob_hnsw_ef_search;` → `64`
- 库内 AI 函数：`ai_embed('<model>', '<text>'[, <dim:int>])`；模型未注册时报 `ai model not found, please check if the model exists`，维度写成小数报 `dimension parameter must be an integer`
- 一条反面教训：在已有全文索引的表上再单独 `create vector index`，本机复现过一次后台线程异常退出并断连；因此 `db.py` 固定用单条 `CREATE TABLE` 同时声明两种索引

> 表中数字均为 `backend=local`（词法哈希向量）下的结果，用途是验证链路、指标实现与诊断规则；换真实 embedding 后绝对值会变化，但对比方法与扫描口径不变。
