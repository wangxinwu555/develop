"""无 API Key 的规则驱动演示模型。不是 LLM，不用于证明模型效果。

它返回标准 AIMessage/tool_calls，以便复用并测试真实 LangGraph 执行流程。
真实模型实现位于 graph.py；本文件方便观察每一步的输入和输出。
"""

import json
import re
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call_" + uuid4().hex, "type": "tool_call"}],
    )


def demo_reply(messages, turn_start: int) -> AIMessage:
    current = messages[turn_start:]
    humans = [str(m.content) for m in messages if isinstance(m, HumanMessage)]
    latest = humans[-1]
    order = next(
        (
            m.group(0).upper()
            for text in reversed(humans)
            if (m := re.search(r"(?<![A-Za-z0-9])O\d{4}(?![A-Za-z0-9])", text, re.IGNORECASE))
        ),
        None,
    )
    # 只在补充订单号/问题描述时继承上一条意图，避免历史换货意图污染新问题。
    goal = latest
    if re.fullmatch(r"\s*O\d{4}\s*", latest, re.IGNORECASE) or (
        any(w in latest for w in ("坏", "故障", "没声音", "损坏"))
        and not any(w in latest for w in ("换货", "物流", "发票", "政策"))
    ):
        goal = next((text for text in reversed(humans[:-1]) if "换货" in text), latest)
    exchange = "换货" in goal and any(w in goal for w in ("申请", "帮我", "办理", "提交"))
    reason = next(
        (
            text
            for text in reversed(humans)
            if any(w in text for w in ("坏", "故障", "没声音", "损坏", "无法", "质量问题"))
        ),
        "",
    )
    last = current[-1]
    if isinstance(last, ToolMessage):
        data = json.loads(str(last.content))
        if data.get("error"):
            return AIMessage(content=data["message"] + " 你可以修正信息后重新提问。")
        if last.name == "get_order":
            if exchange:
                return call("search_policy", query="电子产品质量问题换货")
            return AIMessage(
                content=f"订单 {data['id']}：{data['product']}，"
                f"状态 {data['status']}，签收日期 {data['delivered_at'] or '尚未签收'}。"
                "无法据此预测准确送达时间。"
            )
        if last.name == "search_policy":
            if exchange:
                return call("check_exchange", order_id=order)
            if not data["hits"]:
                return AIMessage(content="当前知识库没有找到相关依据，请联系人工客服。")
            return AIMessage(
                content="\n\n".join(f"{hit['text']} [{hit['id']}]" for hit in data["hits"])
            )
        if last.name == "check_exchange":
            if data["eligible"]:
                return call("request_exchange", order_id=order, reason=reason[:500])
            return AIMessage(content=f"{data['reason']} [{data['policy_id']}]")
        if last.name == "request_exchange":
            if data.get("cancelled"):
                return AIMessage(content="已取消本次申请，没有创建新工单。")
            if not data.get("eligible", True):
                return AIMessage(content=f"{data['reason']} [{data['policy_id']}]")
            return AIMessage(
                content=f"申请已受理，工单号 {data['id']}，状态 {data['status']}。"
                f"申请尚未审核，不代表换货已审核通过。 [{data['policy_id']}]"
            )
    if exchange:
        if not order:
            return AIMessage(content="请提供订单号，例如 O1001。")
        if not reason:
            return AIMessage(content="请描述商品出现的质量问题，例如右耳没有声音。")
        return call("get_order", order_id=order)
    if any(w in goal for w in ("物流", "快递", "订单", "到货")) and "政策" not in goal:
        return call("get_order", order_id=order) if order else AIMessage(content="请提供订单号。")
    if any(w in goal for w in ("换货", "发票", "政策", "退款", "退货", "期限")):
        return call("search_policy", query=goal)
    return AIMessage(
        content="我能查询订单、说明售后政策，并在你确认后创建换货工单。"
        "请说明你的问题；演示订单 O1001 属于 Alice。"
    )
