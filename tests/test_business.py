from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from supportflow.business import BusinessError, today


def test_order_access_is_enforced(service):
    with pytest.raises(BusinessError, match="订单不存在"):
        service.store.get_order("alice", "O2001")


@pytest.mark.parametrize("order_id,eligible", [("O1001", True), ("O1002", False), ("O1003", False)])
def test_exchange_eligibility(service, order_id, eligible):
    assert service.store.check_exchange("alice", order_id)["eligible"] is eligible


@pytest.mark.parametrize("days,eligible", [(0, True), (7, True), (8, False), (-1, False)])
def test_day_boundaries(service, days, eligible):
    with service.store.connection() as conn:
        conn.execute(
            "UPDATE orders SET delivered_at = ? WHERE id = 'O1001'",
            ((today() - timedelta(days=days)).isoformat(),),
        )
    assert service.store.check_exchange("alice", "O1001")["eligible"] is eligible


def test_business_layer_rejects_unconfirmed_write(service):
    with pytest.raises(BusinessError, match="必须确认"):
        service.store.create_ticket("alice", "O1001", "右耳没声音", "a", approved=False)
    assert not service.store.list_tickets("alice")


def test_concurrent_ticket_creation_is_idempotent(service):
    def create(i):
        return service.store.create_ticket(
            "alice", "O1001", "右耳没声音", f"request-{i}", approved=True
        )["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    assert len(service.store.list_tickets("alice")) == 1


def test_same_request_cannot_change_its_payload(service):
    service.store.create_ticket("alice", "O1001", "右耳没声音", "same", approved=True)
    with pytest.raises(BusinessError, match="不同内容"):
        service.store.create_ticket("alice", "O1001", "左耳故障", "same", approved=True)
