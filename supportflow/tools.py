"""Agent 工具定义与服务端业务上下文绑定。

user_id 由服务端注入，模型参数中没有 user_id，避免模型替用户选择身份。
request_exchange 只准备申请，实际写入由 graph.py 的审批节点控制。
"""

from langchain_core.tools import tool

from supportflow.business import Store
from supportflow.retrieval import Retriever


def build_tools(store: Store, retriever: Retriever, user_id: str):
    @tool
    def get_order(order_id: str) -> dict:
        """查询当前用户的订单状态；缺少订单号时先追问，不能猜测订单号。"""
        return store.get_order(user_id, order_id.upper())

    @tool
    def search_policy(query: str) -> dict:
        """检索售后、物流和发票政策。回答政策问题需要引用命中的政策 id。"""
        if not 1 <= len(query.strip()) <= 500:
            raise ValueError("检索词长度必须为1至500字。")
        return retriever.search(query)

    @tool
    def check_exchange(order_id: str) -> dict:
        """校验订单是否满足模拟质量问题换货的时间、品类与签收条件。"""
        return store.check_exchange(user_id, order_id.upper())

    @tool
    def request_exchange(order_id: str, reason: str) -> dict:
        """发起质量问题换货申请。必须提供订单号和用户描述的问题。

        调用后系统会等待用户确认；不能自行确认，不代表申请已经创建。
        必须单独调用，不能与其他工具放在同一批调用中。
        """
        check = store.check_exchange(user_id, order_id.upper())
        if not 2 <= len(reason.strip()) <= 500:
            raise ValueError("问题描述需要2至500字。")
        return {**check, "reason_text": reason.strip()}

    return {t.name: t for t in (get_order, search_policy, check_exchange, request_exchange)}
