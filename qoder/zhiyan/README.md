# 智研 ZhiYan · 文献笔记智能检索 + 个性化复习助手

> OceanBase 数据库大赛 · **Qoder 赛题**作品。
> 用 **Qoder** 开发，结合 **seekdb**（混合检索）与 **PowerContext**（学习记忆），
> 通过 **Qoder Sites** 发布项目网站。

## 1. 项目是什么 / 解决什么问题

科研与学习中，论文、课程笔记、实验记录散落各处：复习时找不到上学期的内容，
每次都错在同样的知识点却没有系统记录，直接问大模型又缺少你的私有资料、容易幻觉。

**智研**把这三件事串起来：

1. **混合检索**：把文献/笔记/实验导入 **seekdb**，向量语义 + 全文关键词混合检索，
   快速定位相关段落与出处，可按文献/笔记/实验类型过滤；
2. **基于资料的问答**：**MiniMax-M1** 严格依据检索到的资料作答并标注 [编号] 出处；
3. **薄弱点记忆 + 间隔重复**：**PowerContext** 持久化保存每个薄弱知识点与复习进度，
   按 SM-2 lite 调度生成「今日复习」计划，跨会话、重启不丢——越用越懂你。

项目形式为 **Web 应用**（FastAPI 后端 + Web 前端），前端同时作为 Qoder Sites 展示站点。

## 2. 技术结合（对应赛题要求）

| 组成 | 要求与作用 | 在本项目中的实际作用 | 证据 |
| --- | --- | --- | --- |
| **Qoder**（必选） | 承担主要开发；通过 Qoder Sites 发布网站 | 全部代码/文档由 Qoder 开发；本网站经 Qoder Sites 发布 | `DEV_RECORD.md` + 站点 |
| **seekdb** | 数据存储、查询与检索 | 存储语料并做向量+全文混合检索、类型过滤 | `records/db-query-record.json` |
| **PowerContext** | 记忆与上下文，保存并在后续复用 | 保存薄弱知识点与复习进度（Memory）+ 复习事件（Source），重启复用 | `records/powercontext-record.json` |
| **MiniMax** | （增强）嵌入与问答 | embo-01 生成 1536 维嵌入；M1 做 grounded 问答 | `records/minimax-record.json` |

> seekdb 与 PowerContext 二者均实际使用并发挥核心作用（满足“至少一种”，本项目两者都用）。

## 3. 目录结构

```
qoder/zhiyan/
├── backend/
│   ├── app.py          # FastAPI 主应用（API + 托管前端）
│   ├── retrieval.py    # seekdb 混合检索（pyseekdb + MiniMax 嵌入）
│   ├── memory.py       # PowerContext 薄弱点/复习进度（SM-2 lite）
│   ├── llm.py          # MiniMax embo-01 嵌入 + M1 grounded 问答
│   ├── sample_data.py  # 内置示例语料（12 篇文献/笔记/实验）
│   ├── ingest.py       # 语料初始化入口
│   └── config.py       # 环境变量配置（不含真实凭据）
├── frontend/
│   ├── index.html      # 单页应用（展示 + 交互演示）
│   ├── style.css
│   └── app.js          # 双模逻辑：实时模式(后端) / 演示模式(内置示例)
├── records/            # 技术使用证据（检索/记忆/MiniMax/验证日志）
├── README.md           # 本文件（运行说明）
├── VALIDATION.md       # 验证样例（逐步操作 + 预期结果）
├── DEV_RECORD.md       # Qoder 开发记录
└── requirements.txt
```

## 4. 运行环境

- Python 3.11+（验证于 3.12）
- seekdb 服务端 v1.3.0（MySQL 协议，默认 `127.0.0.1:2881`）
- PowerContext 1.4.1（基础记忆模式，SQLite，无需模型 API）
- MiniMax API Key（大赛赠送 Token Plan）

```bash
pip install -r requirements.txt
```

## 5. 配置（环境变量，均为占位/默认，不提交真实凭据）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MINIMAX_API_KEY` | （空） | MiniMax Key，问答/嵌入必需 |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com/v1` | |
| `MINIMAX_CHAT_MODEL` | `MiniMax-M1` | |
| `SEEKDB_HOST` / `SEEKDB_PORT` | `127.0.0.1` / `2881` | |
| `SEEKDB_USER` / `SEEKDB_TENANT` / `SEEKDB_PASSWORD` | `root` / `sys` / （空） | |
| `ZHIYAN_DB` | `zhiyan` | seekdb 库名 |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | |
| `ZHIYAN_HOST` / `ZHIYAN_PORT` | `127.0.0.1` / `8010` | 后端监听 |

## 6. 从安装到启动（完整步骤）

```bash
# 6.1 启动 seekdb（Windows 服务，安装后）
"C:\Program Files\seekdb\bin\seekdb_manage.ps1" start

# 6.2 启动 PowerContext（首次生成配置）
powercontext config init --template -o .env
powercontext server run --env-file .env        # 保持运行

# 6.3 设置 MiniMax Key
export MINIMAX_API_KEY=你的key

# 6.4 初始化语料到 seekdb
cd qoder/zhiyan
python -m backend.ingest

# 6.5 启动后端（同时托管前端）
python -m backend.app        # 或: uvicorn backend.app:app --port 8010

# 6.6 打开浏览器访问
#    http://127.0.0.1:8010      -> 实时模式（真实 seekdb + MiniMax + PowerContext）
```

前端为单页应用；当检测不到本地后端时自动进入**演示模式**（内置示例数据，明确标注），
这正是发布到 Qoder Sites 的站点形态。

## 7. API 一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（语料数/MiniMax/PowerContext） |
| POST | `/api/ingest` | 导入示例语料到 seekdb |
| POST | `/api/search` | 混合检索 `{query, top_k, type}` |
| POST | `/api/ask` | grounded 问答 `{question}` |
| POST | `/api/weak-points` | 标记薄弱点 `{point, source, note}` |
| GET | `/api/weak-points` | 列出薄弱点 |
| POST | `/api/review` | 记录复习评分 `{point, quality:0-5}` |
| GET | `/api/review/plan` | 今日复习计划 |
| GET | `/api/stats` | 语料 + 记忆统计 |

## 8. 已知限制

- 演示模式（发布站点）使用内置示例语料与客户端检索，问答为示例拼接并明确标注；
  真实检索/问答/记忆需按上文启动本地后端（实时模式）。
- 语料为示例数据；接入个人真实文献/笔记只需向 `sample_data.py` 结构添加条目后重新 ingest。
- MiniMax-M1 为推理模型，单次问答约 5–30s。
