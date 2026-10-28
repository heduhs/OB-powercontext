# Qoder 开发记录（Development Record）

本项目（智研 ZhiYan）的**主要开发工作在 Qoder 中完成**（Qoder Desktop 会话）。
下文按时间线记录 Qoder 在需求拆解、编码、调试、部署各环节承担的工作。

> **Qoder 账号 UID**：`01a17cfb-436f-77ff-a53e-25c86fb6f9c3`
> （用于核对站点归属与使用记录；与已发布站点 zhiyan-study-msv6fmmokx6.qoder.zone 的 ownerUid 一致。）

## 开发时间线（Qoder 会话内）

| 阶段 | Qoder 承担的工作 | 产物 |
| --- | --- | --- |
| 需求拆解 | 解析赛题要求（用 Qoder 开发、结合 seekdb/PowerContext、Qoder Sites 发布），拆解为检索/问答/记忆/复习四个功能模块与站点形态 | 项目结构、`README.md` 架构章节 |
| 环境搭建 | 安装并验证 seekdb v1.3.0（Windows）、PowerContext 1.4.1、pyseekdb、FastAPI；探测 MiniMax 可用模型（M1/embo-01）与 base_url | 可运行的本地工具链 |
| 编码-检索层 | 编写 `retrieval.py`：seekdb collection、MiniMax embo-01 嵌入函数（注册到 pyseekdb）、混合检索 + 类型过滤 | `backend/retrieval.py` |
| 编码-记忆层 | 编写 `memory.py`：PowerContext Memory（薄弱点状态）+ Source（复习事件）、SM-2 lite 间隔重复、scope 解析与重启复用 | `backend/memory.py` |
| 编码-问答层 | 编写 `llm.py`：embo-01 批量嵌入（重试）、M1 grounded 问答（强制引用、无资料拒答） | `backend/llm.py` |
| 编码-服务层 | 编写 `app.py`：FastAPI 路由（search/ask/weak-points/review/plan/stats/ingest）、CORS、托管前端 | `backend/app.py` |
| 编码-前端 | 编写单页应用 `index.html/style.css/app.js`：四标签交互（检索/问答/薄弱点/复习）、**双模逻辑**（实时/演示）、响应式与暗色视觉 | `frontend/*` |
| 调试 | 定位并修复：嵌入响应字段（`vectors` 而非 `embeddings`）、模型名下线（M2→M1）、静态资源 404（挂载到根路径）、PowerContext `list_scopes`/`list_atomic_memories` 参数签名 | 全链路打通 |
| 验证 | 冒烟测试检索/问答/记忆/复习；浏览器实测实时模式与演示模式；截图核对 UI | `records/*`、`VALIDATION.md` |
| 部署 | 通过 Qoder Sites 发布展示站点（见下） | 公开站点 URL |

## 关键调试记录（Qoder 定位并修复）

1. **MiniMax 模型下线**：`MiniMax-M2/M2.1/abab6.5s` 返回 2069（no longer available）；
   探测确认 `MiniMax-M1`、`MiniMax-Text-01` 可用，选定 M1（推理模型，grounded 问答质量好）。
2. **嵌入响应字段**：embo-01 返回 `{"vectors": ...}` 而非 `{"embeddings": ...}`；
   兼容两者并加重试。
3. **静态资源 404**：前端以相对路径引用 `style.css/app.js`，初版只挂载 `/ui`；
   改为在 API 路由之后把 `frontend/` 挂载到 `/`，使根路径同时提供页面与资源。
4. **PowerContext API 签名**：`list_scopes(query=...)` 需配 `query_field`，改用无参全量后按标题过滤；
   `list_atomic_memories` 只接受单个 request 对象。

## 站点发布

- 发布方式：Qoder Sites（个人账号），可见范围「任何人都可访问」（public）。
- **公开站点 URL**：https://zhiyan-study-msv6fmmokx6.qoder.zone/
- 站点内容：项目简介、解决的问题、核心功能、技术架构与分工、交互演示（演示模式）、
  运行说明摘要、使用效果、代码仓库链接。
- 已验证：未登录状态可打开（HTTP 200），演示模式交互正常；本地有后端时自动切实时模式。

## 开发证据

- **对话日志**：`records/qoder-conversation-log.md`（本会话真实逐轮摘录，含会话 ID 与 UID）
- **窗口截图**：`records/qoder-screenshots/qoder-session-dev.png`（Qoder Desktop 会话实况）
- **站点归属**：已发布站点 ownerUid 与上述 UID 一致，可在 Qoder 侧核对使用记录

## 第二阶段：提测失败复核与检索层重做（2026-10-28，Qoder Desktop 会话）

第一次提测被判 **执行失败 0.000**，本阶段在 Qoder 中完成诊断与重做。

| 环节 | Qoder 承担的工作 | 产物 |
| --- | --- | --- |
| 失败诊断 | 读取平台提测记录接口，比对成功/失败记录字段差异（`taskStatus=3 / failMsg=100 / testCaseResult=[]`），并打开 Qoder 赛题提测弹窗逐字段核对 | 根因：**必填项「作品链接」漏填**，判题器拿不到待校验站点 |
| 隔离验证 | 用**同一个 commit** 只补上作品链接重新提测 | 执行成功 **20.000/20**，判题返回「基础验收通过。」 |
| 代码复核 | 通读 `retrieval.py` 发现**名不副实**：文档与网站宣称"向量+全文混合检索"，代码只调了 pyseekdb 的向量 KNN，没有全文腿也没有融合 | 判定为必须修复的实质缺陷 |
| 检索层重写 | 改为 PyMySQL 直连 seekdb 的原生 SQL：`db.py`（单条 DDL 建 HNSW + ik 全文索引）、`retrieval.py`（向量腿 `approx` + 全文腿 `match against relevance` + RRF/加权两种融合 + 两腿名次回传）、`embeddings.py`（local/minimax/openai 三后端）、`answer.py`（无 Key 抽取式、有 Key M1） | 混合检索真实可运行、可核对 |
| 评测体系 | 编写 `scripts/verify.py`：48 篇语料 × 12 条人工标注查询，跑三模式对比 + 融合方式×权重扫描 + `explain` 取证 + 跨进程记忆验证；`scripts/memory_probe.py` 用独立子进程回读记忆 | `records/verification*.json/log` |
| 实测调参 | 扫描发现默认 RRF/0.5 是**负增益**，据数据把默认融合改为 `weighted / w_vec=0.25`（语义向量下 recall@5 0.8472 > 最好单腿 0.8194） | README 第 2 节数据表 |
| 踩坑复现 | 主动构造对照表复现"中文全文漏 `with parser ik` 会静默返回空"，量化为命中 4/1/1/1 条 vs 21/11/9/5 条 | `VALIDATION.md` 样例 D |
| 语料扩充 | 把演示语料从 12 篇扩到 48 篇（18 文献/24 笔记/6 实验），让 top-5 评测具备区分度 | `backend/sample_data.py` |
| 工程治理 | 新增 `scripts/build_site.py`：站点由仓库源码生成（修掉"线上 69KB 而仓库 dist 12KB 不是同一份"）；前端徽标改为按 `/api/health` 真实回显后端与回答引擎，去掉写死的"seekdb+MiniMax"与无数据支撑的"~10×提速/0 幻觉"宣称 | `site/`、`frontend/app.js` |
| 依赖收敛 | 去掉 pyseekdb 依赖，检索层只用 PyMySQL；`requirements.txt` 标注必需/可选 | 评审零 Key 可复现 |

### 本阶段新增的关键调试记录

1. **`seekdb` 崩溃复现**：在已有全文索引的表上再单独 `create vector index`，实例后台线程异常退出
   （`AccessViolation` → "terminating process to avoid corruption"）并断连。改为**单条 `CREATE TABLE`
   同时声明两种索引**后稳定，故 `db.py` 固定该写法。
2. **RRF 埋没单腿强命中**：一条腿的强命中（如全文 #2）会被"两腿都靠后"的共识票压出前 5；
   因此融合只取每腿头部候选参与，并额外提供 `weighted`（min-max 归一）方案，由扫描决定用哪个。
3. **`ingest` 不幂等**：表已存在时重复导入撞主键（1062）。改为 `insert … on duplicate key update`，
   并在维度变化时自动重建表。
4. **标识符白名单**：表名/分词器无法参数化绑定，加 `^[A-Za-z_][A-Za-z0-9_]{0,63}$` 校验，
   `a;drop`、`ik) x` 均被拦截。
5. **Git Bash 管道吞输出**：venv python 经 `| tail` 时 stdout 丢失，误判为"脚本静默退出"；
   改为重定向到文件后确认脚本正常。

### 提测与判题结果

| 次序 | 时间 | commit | 作品链接 | 状态 | 成绩 |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-27 23:17 | `150ceccf` | （漏填） | 已取消 | — |
| 2 | 2026-10-28 00:07 | `a6f29797` | （漏填） | **执行失败** `failMsg=100` | 0.000 |
| 3 | 2026-10-28 20:10 | `a6f29797` | 已填 | **执行成功**「基础验收通过。」 | **20.000** |
| 4 | 重做后 | 见 `records/` | 已填 | 待判 | — |

> 平台规则：只取最后一次提交结果。第 3 次已确认满分，第 4 次为"内容更扎实的同分改进"。

## 复核方式

评审可：
1. 访问 Qoder Sites 站点了解项目并体验演示模式（含「检索实测」数字）；
2. 按 `README.md` 第 5 节 + `VALIDATION.md` 在本地跑通实时模式全部功能（**无需任何 API Key**）；
3. 运行 `python scripts/verify.py` 复现第 2 节全部数字；
4. 核对 `records/` 下 seekdb / PowerContext 的真实使用证据与 `records/screenshots/` 截图；
5. 以本 UID 在 Qoder 中核对站点归属与开发会话记录。
