"""Regression tests: existing behavior that MUST keep passing (SPEC-001/SPEC-002 invariants)."""

from datetime import date

import pytest

from ordersvc.core import (
    InvalidTransitionError,
    OrderError,
    OrderService,
)


@pytest.fixture()
def svc():
    return OrderService()


class TestCreation:
    def test_create_defaults_to_draft(self, svc):
        order = svc.create_order("alice", 1000)
        assert order.status == "draft"

    def test_customer_is_trimmed(self, svc):
        order = svc.create_order("  bob  ", 500)
        assert order.customer == "bob"

    @pytest.mark.parametrize("bad_customer", ["", "   ", None])
    def test_rejects_empty_customer(self, svc, bad_customer):
        with pytest.raises(OrderError):
            svc.create_order(bad_customer, 500)

    @pytest.mark.parametrize("bad_amount", [0, -1, 10.5, "100"])
    def test_rejects_bad_amount(self, svc, bad_amount):
        with pytest.raises(OrderError):
            svc.create_order("alice", bad_amount)

    def test_default_created_on_is_today(self, svc):
        order = svc.create_order("alice", 100)
        assert order.created_on == date.today()


class TestQuery:
    def _seed(self, svc):
        a = svc.create_order("a", 100, created_on=date(2026, 1, 1))
        b = svc.create_order("b", 200, created_on=date(2026, 1, 2))
        c = svc.create_order("c", 300, created_on=date(2026, 1, 2))
        return a, b, c

    def test_list_all(self, svc):
        self._seed(svc)
        assert len(svc.list_orders()) == 3

    def test_sorted_by_date_then_id(self, svc):
        a, b, c = self._seed(svc)
        result = svc.list_orders()
        assert result[0].order_id == a.order_id
        assert [o.order_id for o in result[1:]] == sorted([b.order_id, c.order_id])

    def test_date_bounds_are_inclusive(self, svc):
        a, b, c = self._seed(svc)
        result = svc.list_orders(date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))
        assert [o.order_id for o in result] == [a.order_id]

    def test_status_filter(self, svc):
        a, b, c = self._seed(svc)
        svc.advance_status(a.order_id, "paid")
        paid = svc.list_orders(status="paid")
        assert [o.order_id for o in paid] == [a.order_id]

    def test_invalid_status_filter_raises(self, svc):
        with pytest.raises(OrderError):
            svc.list_orders(status="unknown")

    def test_filters_combine_with_and(self, svc):
        a, b, c = self._seed(svc)
        svc.advance_status(b.order_id, "paid")
        result = svc.list_orders(status="draft", date_from=date(2026, 1, 2))
        assert [o.order_id for o in result] == [c.order_id]


class TestLifecycle:
    def test_happy_path(self, svc):
        order = svc.create_order("alice", 100)
        svc.advance_status(order.order_id, "paid")
        svc.advance_status(order.order_id, "shipped")
        svc.advance_status(order.order_id, "closed")
        assert svc.get_order(order.order_id).status == "closed"

    def test_terminal_states_reject_transitions(self, svc):
        order = svc.create_order("alice", 100)
        svc.advance_status(order.order_id, "cancelled")
        with pytest.raises(InvalidTransitionError):
            svc.advance_status(order.order_id, "paid")
        assert svc.get_order(order.order_id).status == "cancelled"

    def test_illegal_skip_transition(self, svc):
        order = svc.create_order("alice", 100)
        with pytest.raises(InvalidTransitionError):
            svc.advance_status(order.order_id, "shipped")
        assert svc.get_order(order.order_id).status == "draft"

    def test_invalid_status_value(self, svc):
        order = svc.create_order("alice", 100)
        with pytest.raises(OrderError):
            svc.advance_status(order.order_id, "bogus")

    def test_get_unknown_order(self, svc):
        with pytest.raises(OrderError):
            svc.get_order("nope")
