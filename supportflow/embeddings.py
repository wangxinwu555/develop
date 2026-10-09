"""Embedding 接口：演示编码器与真实 API 具有同一个 LangChain 接口。

DemoEmbeddings 用确定性哈希生成向量，只验证数据流程，不具备预训练语义能力。
"""

import hashlib
import math
import re

from langchain_core.embeddings import Embeddings

from supportflow.config import Settings


class DemoEmbeddings(Embeddings):
    dimension = 1024

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = re.findall(r"[a-z0-9]+", text.lower())
        for part in re.findall(r"[\u4e00-\u9fff]+", text):
            words.extend(part[i : i + 2] for i in range(len(part) - 1))
        for word in words:
            index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            vector[index % self.dimension] += 1
        length = math.sqrt(sum(value * value for value in vector))
        return [value / length for value in vector] if length else vector


def embedding_identity(settings: Settings) -> dict:
    """只包含非敏感配置，用于防止不同模型共用一个向量索引。"""
    if settings.embedding_provider == "demo":
        return {"provider": "demo", "model": "sha256-bigram-v1", "dimension": 1024}
    return {
        "provider": "api",
        "model": settings.embedding_model,
        "base_url": settings.embedding_base_url,
    }


def create_embeddings(settings: Settings) -> Embeddings:
    settings.validate_embedding()
    if settings.embedding_provider == "demo":
        return DemoEmbeddings()
    from langchain_openai import OpenAIEmbeddings

    options = {
        "model": settings.embedding_model,
        "api_key": settings.embedding_api_key.get_secret_value(),
        "request_timeout": settings.llm_timeout_seconds,
        "max_retries": 1,
        "check_embedding_ctx_length": False,
    }
    if settings.embedding_base_url:
        options["base_url"] = settings.embedding_base_url
    return OpenAIEmbeddings(**options)
