"""Target feature tests for TASK-001 (pagination).

These tests define the NEW required behavior. They FAIL before the task is
implemented and MUST pass after, while all regression tests keep passing.
Spec source: specs/SPEC-001 v1.2 §Pagination rules (PS-1..PS-5).
"""

from datetime import date

import pytest

from ordersvc.core import OrderError, OrderService


@pytest.fixture()
def svc_with_five():
    svc = OrderService()
    for i in range(5):
        svc.create_order(f"cust{i}", 100 * (i + 1), created_on=date(2026, 2, i + 1))
    return svc


class TestPagination:
    def test_page_and_page_size_limit_results(self, svc_with_five):
        # PS-1: list_orders accepts page (1-based) and page_size
        page1 = svc_with_five.list_orders(page=1, page_size=2)
        assert len(page1) == 2

    def test_pages_are_disjoint_and_ordered(self, svc_with_five):
        # PS-2: pages follow the same ordering as the unpaginated list
        full = svc_with_five.list_orders()
        page1 = svc_with_five.list_orders(page=1, page_size=2)
        page2 = svc_with_five.list_orders(page=2, page_size=2)
        assert [o.order_id for o in page1] == [o.order_id for o in full[0:2]]
        assert [o.order_id for o in page2] == [o.order_id for o in full[2:4]]

    def test_last_page_can_be_partial(self, svc_with_five):
        # PS-3: a partial last page returns the remaining items
        page3 = svc_with_five.list_orders(page=3, page_size=2)
        assert len(page3) == 1

    def test_page_beyond_end_returns_empty(self, svc_with_five):
        # PS-3: page beyond the end returns an empty list, not an error
        assert svc_with_five.list_orders(page=99, page_size=2) == []

    @pytest.mark.parametrize("page,page_size", [(0, 2), (1, 0), (-1, 2), (1, -5)])
    def test_invalid_pagination_raises(self, svc_with_five, page, page_size):
        # PS-4: page must be >= 1 and page_size >= 1, else OrderError
        with pytest.raises(OrderError):
            svc_with_five.list_orders(page=page, page_size=page_size)

    def test_pagination_combines_with_filters(self, svc_with_five):
        # PS-5: pagination applies AFTER filtering (AND semantics preserved)
        svc_with_five.advance_status(
            svc_with_five.list_orders()[0].order_id, "paid"
        )
        drafts = svc_with_five.list_orders(status="draft", page=1, page_size=2)
        assert len(drafts) == 2
        assert all(o.status == "draft" for o in drafts)
