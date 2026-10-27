# Qoder 开发记录（Development Record）

本项目（智研 ZhiYan）的**主要开发工作在 Qoder 中完成**（Qoder Desktop 会话）。
下文按时间线记录 Qoder 在需求拆解、编码、调试、部署各环节承担的工作。

> **Qoder 账号 UID**：`{{请填写你的 Qoder UID}}`
> （用于核对站点归属与使用记录；在 Qoder 个人设置中可查看。）

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

- 发布方式：Qoder Sites（个人账号），可见范围「任何人都可访问」。
- 站点内容：项目简介、解决的问题、核心功能、技术架构与分工、交互演示（演示模式）、
  运行说明摘要、使用效果、代码仓库链接。
- 发布后 URL 见提测平台填写项 / 本仓库更新说明。

## 复核方式

评审可：
1. 访问 Qoder Sites 站点了解项目并体验演示模式；
2. 按 `README.md` 第 6 节 + `VALIDATION.md` 在本地跑通实时模式全部功能；
3. 核对 `records/` 下 seekdb / PowerContext / MiniMax 的真实使用证据；
4. 以本 UID 在 Qoder 中核对站点归属与开发会话记录。
