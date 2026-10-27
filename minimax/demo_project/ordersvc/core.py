"""Order management core logic.

Implements the behavior described in specs/SPEC-001 and specs/SPEC-002.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

VALID_STATUSES = ("draft", "paid", "shipped", "closed", "cancelled")
ALLOWED_TRANSITIONS = {
    "draft": ("paid", "cancelled"),
    "paid": ("shipped", "cancelled"),
    "shipped": ("closed",),
    "closed": (),
    "cancelled": (),
}


class OrderError(Exception):
    """Base error for order operations."""


class InvalidTransitionError(OrderError):
    """Raised when a status transition is not allowed by SPEC-002."""


@dataclass
class Order:
    order_id: str
    customer: str
    amount_cents: int
    status: str = "draft"
    created_on: Optional[date] = None
    items: List[str] = field(default_factory=list)


class OrderService:
    """In-memory order store with lifecycle management."""

    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}

    def create_order(
        self,
        customer: str,
        amount_cents: int,
        created_on: Optional[date] = None,
        items: Optional[List[str]] = None,
    ) -> Order:
        if not customer or not customer.strip():
            raise OrderError("customer must be a non-empty string")
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            raise OrderError("amount_cents must be a positive integer")
        order = Order(
            order_id=uuid.uuid4().hex[:12],
            customer=customer.strip(),
            amount_cents=amount_cents,
            created_on=created_on or date.today(),
            items=list(items or []),
        )
        self._orders[order.order_id] = order
        return order

    def get_order(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderError(f"unknown order_id: {order_id}")

    def advance_status(self, order_id: str, new_status: str) -> Order:
        if new_status not in VALID_STATUSES:
            raise OrderError(f"invalid status: {new_status}")
        order = self.get_order(order_id)
        allowed = ALLOWED_TRANSITIONS.get(order.status, ())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"cannot move order from '{order.status}' to '{new_status}'"
            )
        order.status = new_status
        return order

    def list_orders(
        self,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Order]:
        """Return orders matching the filters, ordered by created_on then order_id.

        Date bounds are inclusive on both ends (SPEC-001 §Query rules).
        """
        if status is not None and status not in VALID_STATUSES:
            raise OrderError(f"invalid status filter: {status}")
        results = []
        for order in self._orders.values():
            if status is not None and order.status != status:
                continue
            if date_from is not None and order.created_on < date_from:
                continue
            if date_to is not None and order.created_on > date_to:
                continue
            results.append(order)
        results.sort(key=lambda o: (o.created_on, o.order_id))
        return results
