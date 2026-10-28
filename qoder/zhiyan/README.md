# 智研 ZhiYan · 文献笔记智能检索 + 个性化复习助手

> OceanBase 数据库大赛 · **Qoder 赛题**作品（20 分）
> 用 **Qoder** 开发，核心功能同时使用 **seekdb**（混合检索）与 **PowerContext**（学习记忆），
> 网站通过 **Qoder Sites** 发布。
>
> **项目网站（未登录可访问）**：https://zhiyan-study-msv6fmmokx6.qoder.zone/
> **代码仓库**：https://github.com/heduhs/OB-powercontext （分支 `2026-competition-ecosystem`，目录 `qoder/zhiyan/`）
> **提测结果**：commit `332ac5e0` 判题**执行成功，成绩 20.000 / 20**，返回「基础验收通过。」
> （2026-10-28 22:43；此前一次 0 分的原因是平台必填项「作品链接」漏填，见 `DEV_RECORD.md`）

---

## 1. 它解决什么问题

论文、课程笔记、实验记录散落在不同文件里：复习时找不到上学期的内容，关键词搜索又匹配不到语义相近的段落；
每次都错在同样的知识点，却没人帮你记着；直接问大模型，它没有你的资料，答案泛泛甚至编造。

智研把这三件事接成一条链路：

| 能力 | 由谁承担 | 实际作用 |
| --- | --- | --- |
| 语料存储与**混合检索** | **seekdb** | 一张表里同时做 HNSW 向量召回与 ik 中文全文召回，再融合排序，支持按文献/笔记/实验类型过滤 |
| **学习记忆** | **PowerContext** | 持久化保存每个薄弱知识点与复习进度（Memory），把标记与复习事件记成流水（Source），重启后仍可用 |
| 基于资料的问答 | seekdb 检索 + 可选 MiniMax | 只依据检索到的段落作答并标注 `[编号]` 出处；无 Key 时用离线抽取式回答，同样带出处 |
| 间隔重复调度 | 本项目（SM-2 lite）+ PowerContext | 按复习评分动态推后下次复习时间，生成「今日复习」计划 |

## 2. 为什么值得看（一句话结论 + 数据）

**混合检索不是"多接一路就一定更好"。** 我们在 seekdb 上用 48 篇语料、12 条人工标注查询实测：

| 配置 | recall@5（语义 embedding：MiniMax embo-01） | recall@5（离线词法 embedding） |
| --- | --- | --- |
| 仅向量 | 0.4305 | 0.6250 |
| 仅全文（ik 分词） | 0.8194 | 0.8194 |
| **混合：加权融合 w=0.25（本项目默认）** | **0.8472** | 0.7986 |
| 混合：教科书 RRF w=0.5 | 0.7639 | 0.7986 |

- 用**语义** embedding 时，混合比最好单腿 **+0.0278**，且默认配置就是扫描最优；
- 用**词法** embedding 时，混合**没有增益**（−0.0208），最优退化为纯全文；
- 同一批两腿数据，融合配置从 `weighted/0.25` 换成 `RRF/0.5` 会掉 **0.0833**。

所以本项目把"带标注的评测 + 融合方式/权重扫描"做成了仓库里的一条命令（`scripts/verify.py`），
而不是凭感觉调参。完整原始输出见 `records/verification.log` 与 `records/verification-offline.log`。

## 3. 目录结构

```
qoder/zhiyan/
├── backend/
│   ├── app.py            FastAPI：/api/health /search /ask /weak-points /review /plan /stats /explain
│   ├── db.py             seekdb 连接与建表（单条 DDL 同时建 HNSW 向量索引与 ik 全文索引）
│   ├── retrieval.py      向量腿 + 全文腿 + RRF/加权两种融合（返回两腿名次）
│   ├── embeddings.py     local（默认，零依赖）/ minimax / openai 兼容
│   ├── answer.py         抽取式回答（默认）/ MiniMax-M1 grounded 回答
│   ├── memory.py         PowerContext 记忆（Memory 存状态 + Source 存流水）+ SM-2 lite
│   ├── sample_data.py    48 篇示例语料（文献/笔记/实验，合成数据，仅用于演示）
│   └── config.py         全部配置来自环境变量，仓库内无任何密钥
├── frontend/             站点源码（html/css/js/img）
├── site/                 构建产物 = 实际发布到 Qoder Sites 的内容（由 build_site.py 生成）
├── scripts/
│   ├── verify.py         端到端验证 + 融合扫描，产出 records/verification*
│   ├── build_site.py     由仓库源码生成 site/（保证线上与仓库一致）
│   └── memory_probe.py   独立进程读回记忆，用于证明跨进程复用
├── records/              实测证据（verification*.json/log、db/powercontext 记录、截图）
├── README.md             本文件：运行说明
├── VALIDATION.md         验证样例：逐步操作 + 预期结果
├── DEV_RECORD.md         Qoder 开发记录（含 Qoder 账号 UID）
├── EVIDENCE.md           证据索引：每条主张对应哪个文件
└── requirements.txt
```

## 4. 运行环境（实测版本）

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.12.10（要求 ≥3.11） | |
| seekdb | **v1.3.0.0**（`select version()` 回读） | MySQL 协议，默认端口 2881 |
| PowerContext | **1.4.1** server | HTTP，默认 `127.0.0.1:8000` |
| fastapi / uvicorn | 0.162.0 / 0.54.0 | |
| PyMySQL | 2.2.8 | 检索层直连 MySQL 协议，不用 pyseekdb |
| 操作系统 | Windows 10 x64（Linux/Docker 同套 SQL 与命令） | |

> **不需要任何 API Key 即可跑通全部验收项。** 默认 embedding 是离线确定性向量、回答是抽取式；
> 配了 `MINIMAX_API_KEY` 才升级为语义 embedding 与 M1 生成式回答。

## 5. 部署与启动（从安装到运行）

### 5.1 起 seekdb

```bash
# 方式一：Docker
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest

# 方式二：Windows 本机二进制（首次初始化约 2 分钟）
"C:\Program Files\seekdb\bin\seekdb.exe" --nodaemon --base-dir=<数据目录> \
    --port=2881 --parameter memory_limit=2G --log-level=WARN
```

### 5.2 起 PowerContext

```bash
pip install "powercontext>=1.4.1"
powercontext server run --env-file .env      # 监听 127.0.0.1:8000
```

`.env` 最小配置（不配推理模型即可，基础记忆功能可用）：

```
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=8000
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

### 5.3 配置本项目的环境变量（都有默认值，可不设）

```bash
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=
export ZHIYAN_DB=zhiyan                      # 库名，脚本会自动 create database
export POWERCONTEXT_URL=http://127.0.0.1:8000
export ZHIYAN_EMBED_BACKEND=local            # local | minimax | openai
# 可选：export MINIMAX_API_KEY=...           # 配了才启用语义 embedding 与 M1 回答
```

### 5.4 安装依赖、自检、启动

```bash
cd qoder/zhiyan
pip install -r requirements.txt

python -m backend.ingest        # 建表 + 导入 48 篇示例语料，回读三种索引
python scripts/verify.py        # 端到端实测：三模式对比 + 融合扫描 + 跨进程记忆，写出 records/
python -m backend.app           # 启动后端并托管站点，默认 http://127.0.0.1:8010
```

浏览器打开 <http://127.0.0.1:8010> —— 右上角徽标会显示后端**真实**的 embedding 与回答引擎，
检索结果里每条都会标出「向量 #n / 全文 #m」。

## 6. 关键实现说明（对应赛题"技术结合有效"）

### 6.1 seekdb：一条 DDL 建两种索引

```sql
create table zhiyan_docs(
  doc_id varchar(64) primary key,
  title varchar(512), section varchar(512), doc_type varchar(32),
  tags varchar(512), text_body varchar(4000) not null, source_url varchar(512),
  embedding vector(256) not null,
  fulltext index ftx (text_body) with parser ik,
  vector index vidx (embedding) with (distance=cosine, type=hnsw)
);
```

- `with parser ik` 是中文全文检索的前提。不加时 `match against('中文词')` 会**静默返回空**且不报错
  （默认按空格切词，整句中文被当成一个词）——这是本项目实测到的坑，已写进 `VALIDATION.md` 的排查项。
- 两种索引写在同一条 `CREATE TABLE`：在已有全文索引的表上再单独 `create vector index`，本机复现过一次
  实例后台线程异常退出并断连。

### 6.2 两腿召回与融合

```sql
-- 向量腿（approx 让它走 HNSW）
select doc_id, cosine_distance(embedding, ?) score from zhiyan_docs
 order by cosine_distance(embedding, ?) approx limit 10;

-- 全文腿（ik 分词 + BM25 风格 relevance）
select doc_id, match(text_body) against(?) relevance from zhiyan_docs
 where match(text_body) against(?) order by relevance desc limit 10;
```

融合支持 `rrf`（`score = Σ w/(k0+rank)`）与 `weighted`（两腿分数 min-max 归一后加权）两种，
默认 `weighted, w_vec=0.25`——该默认值来自第 2 节的实测扫描，不是猜的。
每条结果返回 `vector_rank` / `fulltext_rank` / `matched_by`，评审能直接看到两路各自的贡献。

`GET /api/explain?q=...` 返回两腿的 `explain` 计划（`VECTOR INDEX SCAN` / `TEXT RETRIEVAL SCAN`）
以及**一条 SQL 完成 RRF 融合**的等价写法，用于证明索引真的被用到、融合也能下推到数据库。

### 6.3 PowerContext：记忆分工与跨进程复用

- **Memory**（`kind="weak-point"`）存"当前状态"：复习次数、间隔、下次复习日期、版本号；
  每次复习写入一条新版本，读取时按 `version` 取最新，因此**重启进程后仍在**。
- **Source** 存"不可变流水"：`weak_point_flagged` / `review_event` 事件，便于回溯学习过程。
- 复习评分用 SM-2 lite：`quality≥3` 按 1 → 6 → `interval*ease` 递增间隔，`quality<3` 重置为 1 天。
- `scripts/memory_probe.py` 由 `verify.py` 以**独立子进程**调用，证明"另一个进程能读回刚写入的薄弱点"
  （实测 `found=true`，见 `records/verification.json` 的 `memory.cross_process_read`）。

### 6.4 与 MiniMax 的关系（可选增强，不是必需）

`embo-01`（1536 维）提供语义 embedding，`MiniMax-M1` 提供 grounded 生成。两者缺失时系统自动降级为
离线向量 + 抽取式回答，**功能与验收不受影响**；降级状态会在 `/api/health` 与站点徽标上如实显示。

## 7. Qoder 与 Qoder Sites

- 需求拆解、编码、调试、验证与文档由 **Qoder** 完成，过程记录与账号 UID 见 `DEV_RECORD.md`。
- 网站通过 **Qoder Sites** 发布，可见范围「任何人都可访问」（`access_mode: public`，未登录可打开）。
- 站点内容由 `scripts/build_site.py` 从仓库源码生成到 `site/`，因此线上版本可以与仓库逐文件比对。
- **当前状态如实说明**：本次重做后的 `site/`（含「检索实测」数字、两腿名次徽章、真实后端徽标）
  尚未重新发布上线——Qoder Sites 插件在本次会话中对写操作调用持续返回连接错误，发布动作待重试。
  **线上现行版本仍是上一版构建**（公开可访问、内容有效、已通过平台基础验收）。
  执行 `python scripts/build_site.py` 后在 Qoder 的 Sites 面板点「发布」，线上即与 `site/` 一致。

## 8. 已知边界（诚实声明）

1. 示例语料是**合成的演示数据**，不是真实文献引用；`records/` 里的检索与记忆数字来自这份语料。
2. 评测集为 12 条查询、48 篇文档，样本量小，1~2 个点的差异属噪声；本文给出的结论是**形态性**结论
   （融合配置影响结果、词法 embedding 下向量腿无增益），不是可外推的绝对分数。
3. 离线 `local` embedding 是字符 n-gram 哈希，**没有语义泛化能力**，只用于保证零依赖可复现。
4. `weighted` 融合用 min-max 归一化，跨查询不可比；这也是同时保留 `rrf` 的原因。
5. 站点为纯静态前端 + 演示模式；实时模式需要评审在本地按第 5 节启动后端（Qoder Sites 托管静态站点）。
