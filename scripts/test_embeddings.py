"""真实本地接口冒烟测试：python -m scripts.test_embeddings。

使用 Ollama 的 OpenAI 兼容接口，测试数据写入独立目录，不改变现有知识库。
"""

import argparse
import json
import math
import time
from uuid import uuid4

from pydantic import SecretStr

from supportflow.config import ROOT, Settings
from supportflow.embeddings import create_embeddings
from supportflow.ingest import build_index
from supportflow.retrieval import Retriever


def cosine(left, right):
    return sum(a * b for a, b in zip(left, right)) / (
        math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="bge-m3")
    args = parser.parse_args()
    output_dir = ROOT / "runtime/embedding-checks" / uuid4().hex
    settings = Settings(
        _env_file=None,
        embedding_provider="api",
        embedding_api_key=SecretStr("ollama"),
        embedding_base_url=args.base_url,
        embedding_model=args.model,
        llm_timeout_seconds=120,
        knowledge_dir=output_dir / "knowledge",
    )
    embeddings = create_embeddings(settings)
    samples = [
        "耳机出现质量故障，签收后七天内可以申请换货。",
        "订单已发货，物流状态可以通过订单系统查询。",
        "电子发票需要提供订单号和发票抬头。",
        "今天的天气晴朗，适合去公园散步。",
    ]
    started = time.perf_counter()
    print(f"连接 {args.base_url}，模型 {args.model}；首次加载可能较慢。", flush=True)
    vectors = embeddings.embed_documents(samples)
    document_ms = round((time.perf_counter() - started) * 1000, 2)
    query = "我的耳机坏了，能换一副新的吗？"
    started = time.perf_counter()
    query_vector = embeddings.embed_query(query)
    query_ms = round((time.perf_counter() - started) * 1000, 2)
    dimension = len(query_vector)
    if len(vectors) != len(samples) or not dimension:
        raise ValueError("接口返回的向量数量或维度异常。")
    for vector in [*vectors, query_vector]:
        if len(vector) != dimension or not all(math.isfinite(v) for v in vector):
            raise ValueError("向量维度不一致，或含非有限数值。")
        if sum(v * v for v in vector) == 0:
            raise ValueError("接口返回全零向量。")
    semantic_scores = [round(cosine(query_vector, vector), 5) for vector in vectors]
    if max(range(len(vectors)), key=lambda i: semantic_scores[i]) != 0:
        raise ValueError("这组语义检索示例未将换货文本排在第一位。")
    print(f"向量有效：{dimension}维；批量 {document_ms}ms，问题 {query_ms}ms。", flush=True)
    print("示例余弦相似度：", semantic_scores, flush=True)
    print("开始 PDF -> 切分 -> 实际 Embedding -> Chroma 验证。", flush=True)
    manifest = build_index(settings, embeddings)
    retriever = Retriever(settings, embeddings)
    cases = [
        ("耳机坏了可以换新的吗？", "exchange-electronics"),
        ("电子发票怎么申请？", "invoice"),
        ("怎么查物流配送进度？", "delivery"),
        ("火星天气怎么样？", None),
        ("退款规则是什么？", None),
    ]
    rows = []
    for question, expected in cases:
        result = retriever.search(question)
        hits = result["hits"]
        if expected and (not hits or hits[0]["id"] != expected):
            raise ValueError(f"政策检索未命中预期首位来源：{question}")
        row = {
            "question": question,
            "expected_top_source": expected,
            "hits": [{k: h[k] for k in ("id", "score", "source", "page")} for h in hits],
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    # 再打开同一个索引，验证持久化与查询接口。
    assert build_index(settings, embeddings)["reused"]
    assert Retriever(settings, embeddings).search(cases[0][0])["hits"][0]["id"] == cases[0][1]
    report = {
        "model": args.model,
        "base_url": args.base_url,
        "dimension": dimension,
        "sample_count": len(samples),
        "document_embedding_ms": document_ms,
        "query_embedding_ms": query_ms,
        "semantic_query": query,
        "samples": samples,
        "semantic_scores": semantic_scores,
        "index": manifest,
        "retrieval_min_score": settings.retrieval_min_score,
        "retrieval_cases": rows,
        "persistent_reload_passed": True,
        "limitations": "少量接口与检索冒烟测试，不代表完整效果评测。无关问题用于观察阈值，不断言拒答。",
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"测试完成，报告：{report_path}", flush=True)


if __name__ == "__main__":
    main()
