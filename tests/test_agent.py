import pytest
from langchain_core.messages import AIMessage

from supportflow.business import BusinessError
from supportflow.demo_model import call
from supportflow.service import AgentService, RunFailed


def pending(service):
    session = service.store.create_session("alice")
    response = service.chat("alice", session, "O1001的耳机坏了，帮我申请换货")
    return session, response


def test_clarification_and_approval(service):
    session = service.store.create_session("alice")
    first = service.chat("alice", session, "我的耳机坏了，帮我申请换货")
    assert "订单号" in first["reply"]
    response = service.chat("alice", session, "O1001")
    assert response["status"] == "awaiting_approval"
    assert not service.store.list_tickets("alice")
    result = service.approve("alice", session, response["approval"]["approval_id"], True)
    assert result["status"] == "completed"
    assert "工单号" in result["reply"]
    assert len(service.store.list_tickets("alice")) == 1


def test_cancel_does_not_write(service):
    session, response = pending(service)
    result = service.approve("alice", session, response["approval"]["approval_id"], False)
    assert "取消" in result["reply"]
    assert not service.store.list_tickets("alice")


def test_repeated_approval_and_conflicting_decision(service):
    session, response = pending(service)
    approval_id = response["approval"]["approval_id"]
    result = service.approve("alice", session, approval_id, True)
    assert service.approve("alice", session, approval_id, True) == result
    assert len(service.store.list_tickets("alice")) == 1
    with pytest.raises(BusinessError, match="不同的确认"):
        service.approve("alice", session, approval_id, False)


def test_stale_approval_is_rejected(service):
    session, _ = pending(service)
    with pytest.raises(BusinessError, match="过期"):
        service.approve("alice", session, "old-approval", True)
    assert not service.store.list_tickets("alice")


def test_no_new_chat_while_approval_pending(service):
    session, _ = pending(service)
    with pytest.raises(BusinessError, match="当前任务尚未结束"):
        service.chat("alice", session, "换货期限是多少")


def test_checkpoint_survives_service_restart(settings):
    first = AgentService(settings)
    session, response = pending(first)
    approval_id = response["approval"]["approval_id"]
    first.close()
    second = AgentService(settings)
    try:
        assert second.response("alice", session)["approval"]["approval_id"] == approval_id
        assert second.approve("alice", session, approval_id, True)["status"] == "completed"
    finally:
        second.close()


def test_eligibility_is_rechecked_after_pause(service):
    session, response = pending(service)
    with service.store.connection() as conn:
        conn.execute("UPDATE orders SET status = 'returned' WHERE id = 'O1001'")
    result = service.approve("alice", session, response["approval"]["approval_id"], True)
    assert result["status"] == "completed"
    assert not service.store.list_tickets("alice")


def test_knowledge_answer_has_reference(service):
    session = service.store.create_session("alice")
    result = service.chat("alice", session, "换货期限是多少")
    assert "[exchange-electronics]" in result["reply"]
    assert result["references"][0]["id"] == "exchange-electronics"


def test_unknown_policy_abstains(service):
    session = service.store.create_session("alice")
    result = service.chat("alice", session, "退款规则是什么")
    assert "没有找到相关依据" in result["reply"]


class ScriptedModel:
    """离线验证真实模型分支的协议；不能替代真实 provider 的端到端验证。"""

    def __init__(self, replies):
        self.replies = iter(replies)

    def bind_tools(self, tools):
        assert {t.name for t in tools} == {
            "get_order",
            "search_policy",
            "check_exchange",
            "request_exchange",
        }
        return self

    def invoke(self, messages):
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_model_failure_can_resume_from_checkpoint(settings):
    model = ScriptedModel([RuntimeError("simulated timeout"), AIMessage(content="恢复成功")])
    service = AgentService(settings, model=model)
    try:
        session = service.store.create_session("alice")
        with pytest.raises(RunFailed):
            service.chat("alice", session, "你好")
        assert service.response("alice", session)["status"] == "retry_required"
        assert service.retry("alice", session)["reply"] == "恢复成功"
    finally:
        service.close()


def test_model_cannot_bypass_approval(settings):
    model = ScriptedModel(
        [
            call("request_exchange", order_id="O1001", reason="右耳没声音"),
            AIMessage(content="申请已受理"),
        ]
    )
    service = AgentService(settings, model=model)
    try:
        session = service.store.create_session("alice")
        response = service.chat("alice", session, "帮我处理耳机故障")
        assert response["approval"]
        assert not service.store.list_tickets("alice")
        service.approve("alice", session, response["approval"]["approval_id"], True)
        assert len(service.store.list_tickets("alice")) == 1
    finally:
        service.close()


def test_infinite_tool_loop_stops(settings):
    model = ScriptedModel([call("get_order", order_id="O1001") for _ in range(20)])
    service = AgentService(settings, model=model)
    try:
        session = service.store.create_session("alice")
        response = service.chat("alice", session, "查询订单")
        assert "最大执行步数" in response["reply"]
        assert response["status"] == "completed"
    finally:
        service.close()


def test_summary_failure_after_write_does_not_duplicate_ticket(settings):
    model = ScriptedModel(
        [
            call("request_exchange", order_id="O1001", reason="右耳没声音"),
            RuntimeError("simulated summary failure"),
            AIMessage(content="工单已提交，尚未审核。"),
        ]
    )
    service = AgentService(settings, model=model)
    try:
        session = service.store.create_session("alice")
        pending_response = service.chat("alice", session, "帮我申请换货")
        approval_id = pending_response["approval"]["approval_id"]
        with pytest.raises(RunFailed):
            service.approve("alice", session, approval_id, True)
        assert len(service.store.list_tickets("alice")) == 1
        assert service.approve("alice", session, approval_id, True)["status"] == "completed"
        assert len(service.store.list_tickets("alice")) == 1
    finally:
        service.close()


def test_request_tool_cannot_run_in_a_batch(settings):
    exchange = call("request_exchange", order_id="O1001", reason="右耳没声音")
    order = call("get_order", order_id="O1001")
    model = ScriptedModel(
        [
            AIMessage(content="", tool_calls=exchange.tool_calls + order.tool_calls),
            AIMessage(content="需要单独发起申请。"),
        ]
    )
    service = AgentService(settings, model=model)
    try:
        session = service.store.create_session("alice")
        response = service.chat("alice", session, "帮我申请换货")
        assert response["approval"] is None
        assert not service.store.list_tickets("alice")
        assert any(e.get("result", {}).get("error") == "approval_batch" for e in response["events"])
    finally:
        service.close()
