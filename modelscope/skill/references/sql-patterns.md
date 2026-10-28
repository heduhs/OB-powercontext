# seekdb SQL 与索引语法速查（实测）

以下每一条都在 **OceanBase seekdb v1.3.0.0**（Windows x64，单机 `root@2881`，MySQL 协议）上执行通过，版本不同请以 `show create table` 回读为准。

## 1. 连接

```bash
mysql -h127.0.0.1 -P2881 -uroot -Dtest
```

```python
import pymysql
conn = pymysql.connect(host="127.0.0.1", port=2881, user="root", password="",
                       database="test", charset="utf8mb4", autocommit=True)
```

`charset="utf8mb4"` 不是可选项：不写时中文写进去可能是乱码，而 `match against` 会表现为“查不到”，非常容易被误判成分词器问题。

## 2. 建表：向量列 + 两种索引

```sql
create table docs(
  doc_id varchar(64) primary key,
  title varchar(512),
  body varchar(4000) not null,
  category varchar(64),
  url varchar(1024),
  embedding vector(256) not null,
  fulltext index ftidx (body) with parser ik,
  vector index vidx (embedding) with (distance=cosine, type=hnsw)
);
```

- 向量列类型写作 `vector(维度)`，维度必须等于 embedding 输出维度。
- 写入可以直接给字符串字面量：`insert into docs(..., embedding) values(..., '[0.1,0.2,...]')`。
- **把两种索引写在同一条 `CREATE TABLE` 里**。在一张已经有全文索引的表上再单独 `create vector index`，本机复现过一次实例后台线程异常退出并导致连接中断；一起声明没有这个问题。
- `with (distance=cosine, type=hnsw)` 还支持 `LIB=VSAG`、`M`、`EF_CONSTRUCTION`，`show create table` 会把默认值一起回显出来：

```sql
show create table docs\G
-- ... VECTOR KEY `vidx` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=...)
```

## 3. 索引自检

```sql
show index from docs;
-- INDEX_NAME | INDEX_TYPE
-- PRIMARY    | BTREE
-- ftidx      | FULLTEXT
-- vidx       | VECTOR
```

只有这三行都在，后面的召回才有意义。

## 4. 向量召回

```sql
select doc_id, cosine_distance(embedding, '[...]') score
from docs
order by cosine_distance(embedding, '[...]') approx
limit 50;
```

- `approx` 是让优化器走 HNSW 的关键字，漏写会退化成全表精算（小表看不出来，十万行级就是线性劣化）。
- 距离函数：`l2_distance`（欧氏）、`cosine_distance`（余弦距离，值越小越像）、`inner_product`（内积，值越大越像）。**查询用的距离要和建索引的 `distance=` 一致**。
- 余弦距离转相似度：`similarity = 1 - cosine_distance`，本技能的 `evaluate.py` 就是这么换算的。
- 带标量过滤的写法（谓词下推，实测可用）：

```sql
select doc_id from docs
where category = 'hybrid'
order by cosine_distance(embedding, '[...]') approx
limit 10;
```

- 检索期候选宽度：`select @@ob_hnsw_ef_search;` 默认 `64`。召回不足时先加大它，比盲目加大 `limit` 更对症。

## 5. 全文召回

```sql
-- 自然语言模式：命中判断 + 相关度
select doc_id, match(body) against('向量索引') relevance
from docs
where match(body) against('向量索引')
order by relevance desc
limit 50;

-- 布尔模式：必须包含 / 排除
select doc_id, match(body) against('+向量 +索引 -教程' in boolean mode) relevance
from docs
where match(body) against('+向量 +索引 -教程' in boolean mode);
```

中文必须 `with parser ik`。不加时的表现是**静默返回空**：

| 建索引写法 | `against('向量检索')` | `against('MySQL')` |
|---|---|---|
| `fulltext index ftx (body)`（默认空格分词） | 0 行 | 命中 |
| `fulltext index ftx (body) with parser ik` | 命中，且带 relevance | 命中 |

这就是本技能诊断表里“中文全文腿空结果率 100%”的由来，实测该配置下全文腿 recall@10 从 0.8542 掉到 0.3021、空结果率 0.5625。

停用词：`show variables like 'ft%';` → `ft_stopword_file = built-in`。专有名词被切碎时，把同义词写进自定义词典比改查询更有效。

## 6. 一条 SQL 完成混合检索（RRF）

```sql
with v as (
  select doc_id, row_number() over(order by cosine_distance(embedding, '[...]')) rn
  from docs order by cosine_distance(embedding, '[...]') approx limit 10),
f as (
  select doc_id, row_number() over(order by match(body) against('向量索引') desc) rn
  from docs where match(body) against('向量索引'))
select doc_id, sum(1.0/(60+rn)) rrf
from (select * from v union all select * from f) t
group by doc_id
order by rrf desc
limit 5;
```

要点：

- `row_number() over(order by ...)` 里**不要**再写 `approx`，`approx` 属于查询级的 `order by ... approx limit`；窗口里只排序，近似性由 CTE 内的 `limit` 保证。
- 加权 RRF 把两段的 `1.0/(60+rn)` 改成 `0.6/(60+rn)` 与 `0.4/(60+rn)` 即可，和本技能的 `--w-vec` 语义一致。
- 单条 SQL 的好处是少一次往返、并且融合结果能被优化器一起计划；需要按查询动态改权重时，仍然建议客户端融合，可读性和可测性更好。

## 7. 执行计划：怎么确认索引真的被用了

```sql
explain select doc_id from docs order by cosine_distance(embedding,'[...]') approx limit 3;
-- |0 |VECTOR INDEX SCAN|docs(vidx)|36 |96 |

explain select doc_id from docs where match(body) against('向量索引') limit 3;
-- |0 |TEXT RETRIEVAL SCAN|docs(ftidx)|22 |131 |
--    calc_relevance=true, match_expr(MATCH(docs.body) AGAINST('向量索引')),
--    pushdown_match_filter(...), sort_keys([MATCH(...) , DESC], limit(3)
```

判读口径：

- 看到 `VECTOR INDEX SCAN <表>(<向量索引名>)` → 向量索引生效；变成 `TABLE FULL SCAN` 就是 `approx` 缺失或距离度量与索引不一致。
- 看到 `TEXT RETRIEVAL SCAN <表>(<全文索引名>)` 且 `calc_relevance=true` → 全文索引生效并参与排序。

## 8. 库内 AI 函数

```sql
select ai_embed('<实例中已注册的模型名>', '待向量化文本');
select ai_embed('<实例中已注册的模型名>', '待向量化文本', 512);  -- 第三个参数必须是整数
```

实测错误信息可以直接用来定位：

| 现象 | 报错 |
|---|---|
| 只给 1 个参数 | `(1582, "Incorrect parameter count in the call to native function 'ai_embed'")` |
| 模型名不存在 | `(1210, 'Incorrect arguments to ai_function, ai model not found, please check if the model exists')` |
| 第三个参数写成小数 | `(1210, 'Incorrect arguments to ai_embed, dimension parameter must be an integer, not a decimal or float')` |

模型注册方式与部署形态相关（本技能未在 v1.3.0.0 上验证到 `CREATE AI MODEL` 语法，`create ai model ...` 会返回 1064 语法错误），因此 `--backend seekdb` 只保证调用形态与错误处理，注册步骤请查你所用版本的官方文档。

## 9. 取证：数据库到底执行了什么

```sql
select query_sql from oceanbase.gv$ob_sql_audit
where is_inner_sql = 0
order by request_time desc limit 20;
```

调参时用它确认“我以为是 A 语句，实际发出去的是不是 A 语句”，比翻应用日志可靠。参数化查询会被记录成带字面量的完整语句，注意别把密钥类字符串打进日志。

## 10. 运维相关（评测环境常踩）

- Windows 本机启动：`seekdb.exe --nodaemon --base-dir=<dir> --port=2881 --parameter memory_limit=2G --log-level=WARN`。
- **默认日志级别写盘非常快**（本机观测到数百 MB/分钟量级），长时间评测务必 `--log-level=WARN`，否则磁盘先于数据库成为瓶颈。
- 容器部署时 `-m` 限制内存后 seekdb 会推导 `memory_limit`；小规模评测建议 ≥2G。
- 嵌入模式（pyseekdb 本地文件模式）首次要下载原生依赖，冷启动可能很慢；评测流水线建议统一用 server 模式，避免把环境耗时算进检索延迟。
