"""运行：python -m scripts.demo。独立演示数据，不改变网页使用的订单。"""

import json
from uuid import uuid4

from supportflow.config import ROOT, Settings
from supportflow.ingest import build_index
from supportflow.service import AgentService


def main():
    demo_dir = ROOT / "runtime/demos" / uuid4().hex
    settings = Settings(
        _env_file=None,
        agent_mode="demo",
        embedding_provider="demo",
        knowledge_dir=demo_dir / "knowledge",
        runtime_dir=demo_dir,
    )
    build_index(settings)
    service = AgentService(settings)
    try:
        session = service.store.create_session("alice")
        print("用户：我的耳机右耳没声音，帮我申请换货。")
        print(service.chat("alice", session, "我的耳机右耳没声音，帮我申请换货。")["reply"])
        print("用户：O1001")
        pending = service.chat("alice", session, "O1001")
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        assert not service.store.list_tickets("alice"), "确认前不应有工单"
        print("用户点击确认。")
        result = service.approve("alice", session, pending["approval"]["approval_id"], True)
        print(result["reply"])
        print("工具路径：", " -> ".join(e["tool"] for e in result["events"] if "tool" in e))
    finally:
        service.close()


if __name__ == "__main__":
    main()
