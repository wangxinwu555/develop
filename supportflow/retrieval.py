"""提问阶段：问题 Embedding -> Chroma 相似度搜索 -> 返回文本块与出处。"""

from langchain_core.embeddings import Embeddings

from supportflow.config import Settings
from supportflow.embeddings import create_embeddings
from supportflow.ingest import KnowledgeError, open_vector_store, read_manifest


class Retriever:
    def __init__(self, settings: Settings, embeddings: Embeddings | None = None):
        self.settings = settings
        self.manifest = read_manifest(settings)
        self.embeddings = embeddings or create_embeddings(settings)
        # 这里只打开已有索引，不读取/切分 PDF，不重新生成政策向量。
        self.vector_store = open_vector_store(
            settings.index_dir / self.manifest["snapshot"], self.embeddings
        )
        if len(self.vector_store.get(include=[])["ids"]) != self.manifest["chunk_count"]:
            raise KnowledgeError("向量索引不完整，请重新运行 ingest --force。")

    def search(self, query: str, top_k: int = 2) -> dict:
        if not query.strip() or not 1 <= top_k <= 10:
            raise ValueError("问题不能为空，top_k 必须在1至10之间。")
        # Chroma 内部调用 embed_query，将问题与已保存的文档向量比较。
        matches = self.vector_store.similarity_search_with_score(query, k=top_k)
        hits = []
        for document, distance in matches:
            score = max(-1.0, min(1.0, 1.0 - distance))
            if score < self.settings.retrieval_min_score:
                continue
            metadata = document.metadata
            hits.append(
                {
                    "id": metadata["policy_id"],
                    "title": metadata["title"],
                    "text": document.page_content,
                    "score": round(score, 5),
                    "source": metadata["source"],
                    "page": metadata["page"],
                    "chunk_id": metadata["chunk_id"],
                }
            )
        return {
            "query": query,
            "mode": "vector",
            "embedding_provider": self.settings.embedding_provider,
            "hits": hits,
            "note": "向量相似不保证证据充分；回答需核对文本内容。"
            + (
                "当前使用演示哈希向量，非预训练语义模型。"
                if self.settings.embedding_provider == "demo"
                else ""
            ),
        }
