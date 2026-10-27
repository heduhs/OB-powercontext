# 技术使用记录（Technology Usage Record）

本文件汇总三项指定技术在本任务中的**实际参与证据**，均指向 `records/` 下的原始记录。

---

## 一、MiniMax 开发记录

- **模型**：`MiniMax-M1`（推理模型），接口 `POST {MINIMAX_BASE_URL}/text/chatcompletion_v2`
- **原始记录**：`records/minimax-dev-record-20261027-215046.json`
  - 包含：本次任务描述、提供给模型的完整上下文（system+user 两条消息）、
    模型原始返回、改动文件、一句话摘要、目标/回归测试结果、耗时。
- **关键事实**（取自该记录）：
  - 任务：TASK-001 为 `list_orders` 增加分页；
  - 提供的上下文：适用规格条目（PS/QS/CS/LS）、`ordersvc/core.py` 完整源码、
    目标测试与回归测试、PowerContext 中的既有任务状态；
  - 模型改动文件：`ordersvc/core.py`；
  - 模型摘要：*“Added optional `page` (1-based) and `page_size` parameters to
    `list_orders`, implementing pagination rules PS-1..PS-5 while preserving all
    existing filtering and ordering behavior.”*；
  - 目标测试通过：`true`；回归测试通过：`true`；耗时约 10s。
- **与提交代码差异的对应**：`records/code-diff-TASK-001.patch` 即本次模型修改产生的
  差异（before/after 快照见 `snapshots/`），与开发记录中的 `changed_files`、`summary` 一致。

> MiniMax 同时以 `embo-01` 承担工程资料的向量嵌入（见下节），是检索质量的来源。

---

## 二、数据库（seekdb）查询记录

- **引擎**：OceanBase seekdb v1.3.0，MySQL 协议，`127.0.0.1:2881`
- **库/集合**：`sddctx` / `sddctx_materials`，共 **37** 个 chunk（spec 26 / code 5 / test 6）
- **嵌入**：MiniMax `embo-01`（1536 维），seekdb 内做**向量 + 全文混合检索**
- **原始记录**：`records/db-query-record.json`
- **示例查询**：`为订单查询接口 list_orders 增加分页参数 page 和 page_size，不能破坏既有查询和状态机行为`
- **等价 SQL**：
  ```sql
  SELECT symbol, source, text
  FROM sddctx_materials
  WHERE kind = 'spec'
  ORDER BY hybrid_score(:query_embedding, MATCH(text) AGAINST(:query))
  LIMIT 5;
  ```
- **检索命中（真实结果，distance 越小越相关）**：

  | 命中 | 来源 | distance |
  | --- | --- | --- |
  | QS-4 status 过滤合法性 | SPEC-001 | 0.1788 |
  | **PS-1 分页参数定义** | SPEC-001 | 0.1902 |
  | CS-4 新订单初始 draft | SPEC-001 | 0.2134 |
  | **PS-3 末页/越界行为** | SPEC-001 | 0.2275 |
  | QS-5 多条件 AND 语义 | SPEC-001 | 0.2313 |

- **结论**：seekdb 中确实保存了工程资料索引，检索结果（含分页规格 PS-*）被直接用于
  构造任务上下文，并进入 MiniMax 的输入。

---

## 三、PowerContext 状态写入与复用记录

- **服务**：PowerContext 1.4.1，HTTP，`127.0.0.1:8000`（基础记忆模式，SQLite 存储）
- **Scope**：`scp_7sb3sp5hmm4qbmtz3r7qc1d7cf`（标题“SDD 上下文管理任务状态”）
- **Memory kind**：`sdd-task-state`
- **原始记录**：`records/powercontext-state-record.json`
- **写入**：`sddctx run` 成功后调用 `remember_memory` 保存 TASK-001 的完成状态、
  改动文件、目标/回归测试结果；并以 `create_source` 追加测试日志作为证据。
- **复用（重启后读取）**：在**全新进程**中调用 `sddctx state --task-id TASK-001`，
  从 PowerContext 读回 1 条 `active` 记录：
  ```json
  {
    "task_id": "TASK-001", "status": "done",
    "summary": "Added optional page (1-based) and page_size ... PS-1..PS-5 ...",
    "changed_files": ["ordersvc/core.py"],
    "target_tests_passed": true, "regression_tests_passed": true,
    "_state": "active", "_ref": "art_f9da56f3b6f1435d9e872e62a3e3091d"
  }
  ```
- **注入后续上下文**：`sddctx context` / `sddctx run` 会读取该状态并作为
  “已完成事项 / 既有验证结果”拼入提示词，实现跨会话任务恢复。

---

## 四、测试与验证记录

- **测试日志**：`records/test-log-20261027-215046.txt`（含命令、退出码、逐条结果）
- **端到端验证日志**：`records/validation-log-2026-10-27.json`（step0..step7 全部 PASS）
- **代码版本**：本仓库提交后的 commit（见提测平台记录的分支/commit SHA）
- **修改前后快照**：`snapshots/before/`、`snapshots/after/`
- **代码差异**：`records/code-diff-TASK-001.patch`

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 能够运行 | PASS | `validate_demo.py` step0–step3 |
| MiniMax 实际参与 | PASS | `records/minimax-dev-record-*.json` + `code-diff-TASK-001.patch` |
| 上下文正确 | PASS | step3 命中 PS-*；step7 规格更新后命中 PS-6 |
| 数据库实际参与 | PASS | `records/db-query-record.json` |
| 状态能够复用 | PASS | `records/powercontext-state-record.json`（重启后读回） |
| 代码修改有效 | PASS | step2 修改前 FAIL → step5 修改后 PASS；回归 step2b/step5b 均 PASS |
