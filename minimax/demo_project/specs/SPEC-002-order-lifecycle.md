# SPEC-002 订单状态机规格（Order Lifecycle Specification）

版本: v1.0
状态: 已生效
适用范围: `ordersvc.core.OrderService.advance_status`

## 状态定义

合法状态: `draft`, `paid`, `shipped`, `closed`, `cancelled`

## 转移规则（Transition rules）

- LS-1: 允许的状态转移仅有：
  - `draft` → `paid` 或 `cancelled`
  - `paid` → `shipped` 或 `cancelled`
  - `shipped` → `closed`
- LS-2: `closed` 与 `cancelled` 为终态，不允许任何转出。
- LS-3: 非法转移必须抛出 `InvalidTransitionError`，且订单状态保持不变。
- LS-4: 非法状态值（不在合法状态表中）必须抛出 `OrderError`。

## 必须保持的既有行为（Invariants）

- INV-4: LS-1 至 LS-4 全部规则；任何新功能不得放宽或收紧既有转移表。
