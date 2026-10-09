"""会话操作、访问检查与审批重放。

只支持单进程运行。同会话加锁；跨进程的工单去重由数据库约束保证。
扩展多个 worker 时需要分布式会话锁和更合适的 checkpoint 数据库。
"""

import json
import logging
import threading
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from supportflow.business import BusinessError, Store
from supportflow.config import Settings
from supportflow.graph import AgentGraph
from supportflow.retrieval import Retriever

logger = logging.getLogger(__name__)


class RunFailed(Exception):
    pass


class AgentService:
    def __init__(self, settings: Settings, model=None):
        settings.validate_provider()
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.store = Store(settings.database_path)
        self.store.initialize()
        self.retriever = Retriever(settings)
        self.runtime = AgentGraph(self.store, self.retriever, settings, model=model)
        self.graph = self.runtime.graph
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def close(self):
        self.runtime.close()

    def lock(self, session_id: str):
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.RLock())

    def config(self, session_id: str):
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": self.settings.max_agent_steps * 2 + 6,
        }

    def snapshot(self, user_id: str, session_id: str):
        self.store.require_session(user_id, session_id)
        return self.graph.get_state(self.config(session_id))

    def response(self, user_id: str, session_id: str) -> dict:
        state = self.snapshot(user_id, session_id)
        values = state.values
        pending = [item for task in state.tasks for item in task.interrupts]
        approval = pending[0].value if pending else None
        events = [e for e in values.get("events", []) if e["turn_id"] == values.get("turn_id")]
        replies = [
            m
            for m in values.get("messages", [])[values.get("turn_start", 0) :]
            if isinstance(m, AIMessage) and m.content and not m.tool_calls
        ]
        reply = str(replies[-1].content) if replies else ""
        if approval:
            reply = "申请已准备好，请核对信息并点击确认或取消。"
        references = {}
        for event in events:
            for hit in event.get("result", {}).get("hits", []):
                references[hit.get("chunk_id", hit["id"])] = hit
        usage = {
            key: sum(e.get("usage", {}).get(key, 0) or 0 for e in events)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        return {
            "session_id": session_id,
            "mode": self.settings.agent_mode,
            "status": "awaiting_approval"
            if approval
            else ("retry_required" if state.next else "completed"),
            "reply": reply,
            "approval": approval,
            "events": events,
            "references": list(references.values()),
            "usage": usage,
        }

    def invoke(self, session_id: str, payload):
        try:
            self.graph.invoke(payload, self.config(session_id))
        except Exception as error:
            logger.warning("session=%s run_error_type=%s", session_id, type(error).__name__)
            raise RunFailed("任务暂时失败，状态已保留。可调用重试接口或检查模型配置。") from None

    def chat(self, user_id: str, session_id: str, message: str) -> dict:
        with self.lock(session_id):
            state = self.snapshot(user_id, session_id)
            if state.next:
                raise BusinessError("task_pending", "当前任务尚未结束，请先确认、取消或重试。")
            self.invoke(
                session_id,
                {
                    "messages": [HumanMessage(content=message)],
                    "user_id": user_id,
                    "session_id": session_id,
                    "turn_id": uuid4().hex,
                    "turn_start": len(state.values.get("messages", [])),
                    "steps": 0,
                },
            )
            return self.response(user_id, session_id)

    def approve(self, user_id: str, session_id: str, approval_id: str, approved: bool) -> dict:
        with self.lock(session_id):
            state = self.snapshot(user_id, session_id)
            existing = self.store.get_approval(session_id, approval_id)
            if existing:
                if bool(existing["approved"]) != approved:
                    raise BusinessError("decision_conflict", "该申请已经记录了不同的确认决定。")
                if existing["response"]:
                    return json.loads(existing["response"])
            current = self.response(user_id, session_id)["approval"]
            if current:
                if current["approval_id"] != approval_id:
                    raise BusinessError("stale_approval", "确认编号已过期或不属于当前申请。")
                self.store.record_approval(session_id, approval_id, approved)
                self.invoke(
                    session_id, Command(resume={"approval_id": approval_id, "approved": approved})
                )
            elif existing and state.next:
                # 决定已保存但模型总结失败，从 checkpoint 继续，避免再次输入同一决定。
                self.invoke(session_id, None)
            elif not existing:
                raise BusinessError("no_pending_approval", "当前没有等待确认的申请。")
            response = self.response(user_id, session_id)
            self.store.save_approval_response(approval_id, response)
            return response

    def retry(self, user_id: str, session_id: str) -> dict:
        with self.lock(session_id):
            self.snapshot(user_id, session_id)
            current = self.response(user_id, session_id)
            if current["approval"]:
                raise BusinessError("approval_pending", "请先通过审批接口确认或取消。")
            if current["status"] == "retry_required":
                self.invoke(session_id, None)
            return self.response(user_id, session_id)
