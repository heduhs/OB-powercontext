# TASK-001 为订单查询接口增加分页能力

## 要改什么（Target change）

为 `ordersvc.core.OrderService.list_orders` 增加可选分页参数 `page`（从1开始）
与 `page_size`，行为遵循 SPEC-001 v1.2 新增的 §Pagination rules（PS-1 至 PS-5）。

目标功能测试：`tests/test_orders_pagination.py`
（修改前必须全部失败——功能未实现；修改后必须全部通过。）

## 哪些原有行为必须保持（Must-not-break）

- SPEC-001 §Creation rules（CS-1..CS-4）与 §Query rules（QS-1..QS-5）全部语义；
- SPEC-002 状态机全部规则（LS-1..LS-4）；
- 回归测试：`tests/test_orders_regression.py` 修改前后都必须全部通过。

## 验收方式

1. 修改前运行目标测试 → 失败（功能未实现，而非环境错误）；
2. 通过 `python -m sddctx run` 由 MiniMax 依据检索到的规格/代码/测试上下文完成修改；
3. 修改后目标测试与回归测试全部通过；
4. 任务状态写入 PowerContext，重启模块后可通过 `python -m sddctx state` 读回。
