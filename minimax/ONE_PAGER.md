# 一页技术说明（One-Pager）

## 模块结构

```
任务描述 ──▶ [上下文构造] ──▶ [MiniMax 修改] ──▶ [测试验证] ──▶ [状态保存]
                 │                   │                 │              │
              seekdb 混合检索     MiniMax-M1        pytest         PowerContext
            (规格/代码/测试)     (embo-01 嵌入)   (目标+回归)      (进展/验证结果)
                 ▲                                                     │
                 └───────────────── 重启后读回，注入后续上下文 ◀────────┘
```

核心代码在 `sddctx/`：

| 文件 | 职责 |
| --- | --- |
| `cli.py` | 调用入口：`import` / `context` / `run` / `state` / `stats` |
| `store.py` | 解析规格(按规则条目 CS/QS/PS/LS/INV 切分)、代码(按符号切分)、测试；写入 seekdb 并做混合检索 |
| `embeddings.py` | MiniMax `embo-01` 嵌入；无 Key 时回退本地 ONNX |
| `minimax_editor.py` | 组装提示词→调用 MiniMax-M1→解析文件→落盘→跑测试→回滚失败改动→记录 |
| `state.py` | PowerContext 任务状态保存/读回（scope 本地缓存，重启复用） |

## 上下文选择方式

1. **解析**：规格按“规则条目”粒度切分（保留章节标题作上下文），代码/测试按顶层符号切分，
   每块带稳定 ID 与 `kind/source/symbol` 元数据。
2. **检索**：任务描述经 `embo-01` 嵌入，在 seekdb 内做**向量 + 全文混合检索**，
   按 `kind` 分别取规格/代码/测试的 Top-K（中文检索 distance 低至 ~0.18）。
3. **组装**：
   - 规格条目 → “适用规格”；
   - 命中代码所在**文件的完整源码** → “需修改代码”（确保模型看到全部 import 与类定义，
     避免臆造模块）；
   - 目标/回归测试 → “必须通过的测试”；
   - PowerContext 中该任务的历史状态 → “已完成事项/既有验证结果”。
4. **演进**：规格更新并重新导入后，检索自动使用新内容（step7 验证 PS-6 可被命中），
   不返回固定答案。

## 各项技术的分工

| 技术 | 分工 | 不可替代性 |
| --- | --- | --- |
| **MiniMax-M1** | 依据上下文完成真实代码修改 | 产生 `code-diff-TASK-001.patch`，通过目标+回归测试 |
| **MiniMax embo-01** | 工程资料向量化 | 中文语义检索质量来源（distance 0.18 vs 本地 0.5+） |
| **seekdb** | 存储规格/代码/测试索引，混合检索构造上下文 | 上下文来源真实可查（`db-query-record.json`） |
| **PowerContext** | 保存任务进展与验证结果，重启后读回复用 | 跨会话任务恢复（`powercontext-state-record.json`） |

## 正确性保障

- **修改前失败、修改后通过**：目标测试 `test_orders_pagination.py` 修改前因功能未实现而 FAIL
  （非环境错误），修改后 PASS；回归测试 `test_orders_regression.py` 前后均 PASS。
- **失败即回滚**：若目标或回归测试未全过，自动还原被改文件，不污染工程。
- **不放宽测试**：测试集修改前后一致，未删断言/跳过/改预期。
- **端到端可复现**：`python validate_demo.py` 一键复现，最近一次 **7/7 PASS**。
