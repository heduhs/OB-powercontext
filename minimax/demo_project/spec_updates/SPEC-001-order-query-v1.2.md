# SPEC-001 订单查询规格（Order Query Specification）

版本: v1.2
状态: 已生效
适用范围: `ordersvc.core.OrderService`

## 创建规则（Creation rules）

- CS-1: `customer` 必须为非空字符串，前后空白应被去除；空白或空字符串必须拒绝并抛出 `OrderError`。
- CS-2: `amount_cents` 必须为正整数（> 0）；零、负数或非整数必须拒绝并抛出 `OrderError`。
- CS-3: 未提供 `created_on` 时，使用当天日期。
- CS-4: 新订单初始状态必须为 `draft`。

## 查询规则（Query rules）

- QS-1: `list_orders()` 不带过滤条件时返回全部订单。
- QS-2: 结果必须按 `created_on` 升序排列；同一天内按 `order_id` 升序排列。
- QS-3: `date_from` 与 `date_to` 均为**闭区间**（包含边界当天）。
- QS-4: `status` 过滤值必须是合法状态之一，否则抛出 `OrderError`。
- QS-5: 多个过滤条件同时给出时，取交集（AND 语义）。

## 分页规则（Pagination rules，v1.2 新增）

- PS-1: `list_orders` 接受可选参数 `page`（1-based）与 `page_size`；缺省时保持原有全量返回行为。
- PS-2: 分页在**排序之后**切片，页内顺序与未分页结果一致。
- PS-3: 末页不足 `page_size` 时返回剩余条目；`page` 超出总页数时返回空列表，不报错。
- PS-4: `page < 1` 或 `page_size < 1` 必须抛出 `OrderError`。
- PS-5: 分页与 `status`/日期过滤组合时，先过滤再分页（AND 语义不变）。

## 必须保持的既有行为（Invariants，改动不得破坏）

- INV-1: 上述 CS-1 至 CS-4 的全部校验行为。
- INV-2: QS-1 至 QS-5 的全部查询语义。
- INV-3: `list_orders` 返回 `Order` 对象列表，不改变内部存储顺序。
