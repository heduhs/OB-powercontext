# 验证样例（Validation Sample）

本样例对应赛题“验证样例”要求，明确**要改什么**与**哪些原有行为必须保持**，
并列出上下文中必须包含的规格、代码和测试来源，便于评审核对。

## 任务：TASK-001 为订单查询接口增加分页

- **要改什么**：为 `ordersvc.core.OrderService.list_orders` 增加可选参数
  `page`（1-based）与 `page_size`，行为遵循 SPEC-001 **v1.2** 新增的分页规则 PS-1..PS-5。
- **哪些原有行为必须保持**：
  - SPEC-001 创建规则 CS-1..CS-4、查询规则 QS-1..QS-5；
  - SPEC-002 状态机规则 LS-1..LS-4；
  - 回归测试 `tests/test_orders_regression.py` 修改前后均须全部通过。

## 有效规格（初始数据）

- 初始规格为 `demo_project/specs/SPEC-001-order-query.md` **v1.1（不含分页规则）**；
- 分页规则 PS-1..PS-5 位于 `demo_project/spec_updates/SPEC-001-order-query-v1.2.md`，
  由验证脚本在 step3 应用为正式规格后重新导入（演示“规格变更处理”）。

## 上下文中必须包含的来源（评审核对点）

| 类别 | 必须命中的来源 | 说明 |
| --- | --- | --- |
| 规格 | `specs/SPEC-001-order-query.md` 的 PS-1、PS-3、PS-4、PS-5 | 分页行为定义 |
| 规格 | `specs/SPEC-001-order-query.md` 的 QS-*、`SPEC-002` 的 LS-* | 必须保持的既有行为 |
| 代码 | `ordersvc/core.py`（完整源码，含 `list_orders`、`OrderError`、`InvalidTransitionError`） | 待修改文件 |
| 测试 | `tests/test_orders_pagination.py::TestPagination` | 目标功能测试 |
| 测试 | `tests/test_orders_regression.py`（TestQuery/TestLifecycle） | 回归测试 |

## 操作步骤与预期结果

一键执行（推荐）：

```bash
export MINIMAX_API_KEY=你的key
python validate_demo.py
```

预期逐步结果（与 `records/validation-log-*.json` 一致）：

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| step0 | 从 before 快照复位 demo_project | 恢复修改前状态 |
| step1 | `sddctx import` 导入规格/代码/测试 | 导入成功，provider=minimax |
| step2 | 运行目标测试 `test_orders_pagination.py` | **FAIL**（功能未实现，非环境错误） |
| step2b | 运行回归测试 `test_orders_regression.py` | **PASS** |
| step3 | 应用规格 v1.2 并重新导入，构造上下文 | 上下文命中 PS-1..PS-5 |
| step4 | `sddctx run`：MiniMax-M1 依据上下文修改 `ordersvc/core.py` | 修改成功，产出开发记录 |
| step5 | 再次运行目标测试 | **PASS**（分页已实现） |
| step5b | 再次运行回归测试 | **PASS**（既有行为未破坏） |
| step6 | 读取 PowerContext 中 TASK-001 状态 | 读到 ≥1 条已保存记录 |
| step7 | 应用规格 v1.3（新增 PS-6）重新导入，检索“page_size 最大值限制” | 上下文命中 PS-6（证明非固定答案） |

**整体预期：`OVERALL: PASS`（7/7）。**

## 分步手动执行（等价）

```bash
export MINIMAX_API_KEY=你的key

# 1) 修改前：目标测试失败、回归测试通过
cd demo_project
python -m pytest tests/test_orders_pagination.py -q    # 期望 FAILED
python -m pytest tests/test_orders_regression.py -q    # 期望 passed
cd ..

# 2) 导入 + 构造上下文
python -m sddctx import
python -m sddctx context --task "$(cat tasks/TASK-001.md)" --top-k 6

# 3) MiniMax 修改 + 验证 + 保存状态
python -m sddctx run --task-id TASK-001 --task "$(cat tasks/TASK-001.md)"

# 4) 修改后：目标测试与回归测试均通过
cd demo_project
python -m pytest tests/ -q                              # 期望 all passed
cd ..

# 5) 重启后读取状态（可另开一个进程验证“重启复用”）
python -m sddctx state --task-id TASK-001
```

## 重置方法

```bash
# 复位 demo_project 到修改前（从 before 快照恢复 core.py 与 SPEC-001 v1.1）
python validate_demo.py --dry-run   # step0 会先复位再执行到 step3（不调用 MiniMax）

# 或手动复位：
cp snapshots/before/ordersvc/core.py            demo_project/ordersvc/core.py
cp snapshots/before/specs/SPEC-001-order-query.md demo_project/specs/SPEC-001-order-query.md

# 清空 seekdb 索引与 PowerContext 状态（从空状态重跑）
python -c "import pyseekdb; c=pyseekdb.RemoteServerClient(host='127.0.0.1',port=2881,tenant='sys',user='root',password='',database='sddctx'); c.delete_collection('sddctx_materials')"
rm -f .state/scope_id.json     # 下次运行会新建 PowerContext scope
```

> 说明：目标测试在修改前的失败是**功能未实现**（断言 `list_orders` 不接受 page 参数 /
> 分页结果不符），不是缺少依赖或无法启动等环境错误，符合赛题要求。
