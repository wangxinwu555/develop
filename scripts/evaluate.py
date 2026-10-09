"""运行：python -m scripts.evaluate [--mode llm]。

每个案例使用独立数据库；默认 demo 分数只能说明规则流程工作，不能说明 LLM 效果。
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from uuid import uuid4

from supportflow.config import ROOT, Settings
from supportflow.ingest import build_index
from supportflow.service import AgentService


def evaluate_case(case: dict, settings: Settings) -> dict:
    started = time.perf_counter()
    service = AgentService(settings)
    errors = []
    unsafe = False
    result = {}
    try:
        user_id = case["user"]
        session = service.store.create_session(user_id)
        for message in case["messages"]:
            result = service.chat(user_id, session, message)
            unsafe = unsafe or bool(service.store.list_tickets(user_id))
        if "approve" in case:
            if result.get("approval"):
                approval_id = result["approval"]["approval_id"]
                result = service.approve(user_id, session, approval_id, case["approve"])
                if case.get("replay"):
                    repeated = service.approve(user_id, session, approval_id, case["approve"])
                    if repeated != result:
                        errors.append("重复审批的响应不一致")
            else:
                errors.append("未产生预期的确认请求")
        expected = case["expected"]
        if result.get("status") != expected["status"]:
            errors.append(f"状态不符：{result.get('status')}")
        for word in expected.get("contains", []):
            if word not in result.get("reply", ""):
                errors.append(f"回答缺少：{word}")
        tools = [e["tool"] for e in result.get("events", []) if "tool" in e]
        for name in expected.get("tools", []):
            if name not in tools:
                errors.append(f"未调用工具：{name}")
        sources = [s["id"] for s in result.get("references", [])]
        for source in expected.get("sources", []):
            if source not in sources:
                errors.append(f"未检索到来源：{source}")
        count = len(service.store.list_tickets(user_id))
        if count != expected["tickets"]:
            errors.append(f"工单数量不符：{count}")
        if unsafe:
            errors.append("确认前出现工单写入")
    except Exception as error:
        # 输出异常类型，不把 provider 异常中可能包含的密钥写进报告。
        errors.append(f"执行异常：{type(error).__name__}")
    finally:
        service.close()
    return {
        "id": case["id"],
        "passed": not errors,
        "errors": errors,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "unapproved_write": unsafe,
        "reply": result.get("reply", ""),
        "status": result.get("status"),
        "usage": result.get("usage", {}),
        "tools": [e["tool"] for e in result.get("events", []) if "tool" in e],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "llm"], default="demo")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/results.local.json")
    args = parser.parse_args()
    run_id = uuid4().hex
    # 所有案例共享本次知识库，业务数据仍然逐案例隔离。
    overrides = {
        "agent_mode": args.mode,
        "knowledge_dir": ROOT / "runtime/evaluations" / run_id / "knowledge",
    }
    if args.mode == "demo":
        overrides["embedding_provider"] = "demo"
    base = Settings(**overrides)
    base.validate_provider()
    build_index(base)
    cases = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        settings = base.model_copy(
            update={"runtime_dir": ROOT / "runtime/evaluations" / run_id / case["id"]}
        )
        row = evaluate_case(case, settings)
        rows.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['id']} {row['errors']}")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "retrieval_mode": base.retrieval_mode,
        "embedding_provider": base.embedding_provider,
        "model": base.llm_model if args.mode == "llm" else None,
        "dataset": "evals/cases.json",
        "case_count": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "pass_rate": sum(r["passed"] for r in rows) / len(rows),
        "unapproved_writes": sum(r["unapproved_write"] for r in rows),
        "median_duration_ms": median(r["duration_ms"] for r in rows),
        "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in rows),
        "limitations": "固定小样本的行为回归。demo 是规则加哈希向量，不证明语义模型准确率。"
        "关键词断言不等于语义正确性；暂无真实用户评测和价格换算。",
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"通过 {report['passed']}/{len(rows)}；未确认写入 {report['unapproved_writes']}。")
    print(f"报告：{args.output}")
    raise SystemExit(0 if report["passed"] == len(rows) else 1)


if __name__ == "__main__":
    main()
