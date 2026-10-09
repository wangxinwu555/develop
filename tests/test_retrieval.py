import json
import shutil

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

from supportflow.embeddings import DemoEmbeddings
from supportflow.ingest import (
    KnowledgeError,
    build_index,
    load_pdf_documents,
    read_manifest,
    split_documents,
)
from supportflow.retrieval import Retriever


def test_pdf_loader_preserves_chinese_source_and_page(settings):
    pages = load_pdf_documents(settings)
    assert len(pages) == 3
    assert pages[0].metadata["page"] == 1
    assert pages[0].metadata["source"] == "售后政策.pdf"
    assert pages[0].metadata["policy_id"] == "exchange-electronics"
    assert "7个自然日" in pages[0].page_content
    assert "POLICY_ID" not in pages[0].page_content
    assert "Fictional" not in pages[0].page_content


def test_long_text_splits_with_overlap_and_source(settings):
    settings = settings.model_copy(update={"chunk_size": 64, "chunk_overlap": 16})
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10
    chunks = split_documents([Document(page_content=text, metadata={"page": 9})], settings)
    assert len(chunks) > 1
    assert all(len(c.page_content) <= 64 and c.metadata["page"] == 9 for c in chunks)
    assert chunks[0].page_content[-16:] == chunks[1].page_content[:16]
    assert chunks[1].metadata["start_index"] == 48
    assert len({c.metadata["chunk_id"] for c in chunks}) == len(chunks)


def test_vector_retrieval_returns_pdf_evidence(service):
    result = service.retriever.search("耳机质量问题换货")
    assert result["mode"] == "vector"
    assert result["embedding_provider"] == "demo"
    hit = result["hits"][0]
    assert hit["id"] == "exchange-electronics"
    assert "7个自然日" in hit["text"]
    assert hit["source"] == "售后政策.pdf" and hit["page"] == 1


def test_empty_evidence_and_invalid_query(service):
    assert service.retriever.search("火星天气")["hits"] == []
    with pytest.raises(ValueError):
        service.retriever.search("  ")
    with pytest.raises(ValueError):
        service.retriever.search("换货", top_k=0)


def test_missing_index_is_actionable(settings, tmp_path):
    with pytest.raises(KnowledgeError, match="scripts.ingest"):
        Retriever(settings.model_copy(update={"knowledge_dir": tmp_path / "missing"}))


def test_api_adapter_persists_without_reembedding(settings, tmp_path, monkeypatch):
    import langchain_openai

    calls = {"documents": 0, "query": 0}

    class FakeEmbeddings(Embeddings):
        def __init__(self, **kwargs):
            assert kwargs["model"] == "test-embedding"
            assert kwargs["base_url"] == "https://embedding.example/v1"
            assert kwargs["check_embedding_ctx_length"] is False
            self.delegate = DemoEmbeddings()

        def embed_documents(self, documents):
            calls["documents"] += 1
            return self.delegate.embed_documents(documents)

        def embed_query(self, query):
            calls["query"] += 1
            return self.delegate.embed_query(query)

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", FakeEmbeddings)
    configured = settings.model_copy(
        update={
            "knowledge_dir": tmp_path / "api",
            "embedding_provider": "api",
            "embedding_model": "test-embedding",
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_api_key": SecretStr("test-not-a-real-key"),
        }
    )
    assert build_index(configured)["reused"] is False
    assert build_index(configured)["reused"] is True
    for _ in range(2):
        retriever = Retriever(configured)
        assert retriever.search("耳机质量问题换货")["hits"][0]["id"] == "exchange-electronics"
    assert calls == {"documents": 1, "query": 2}
    manifest_text = (configured.index_dir / "manifest.json").read_text(encoding="utf-8")
    assert "test-not-a-real-key" not in manifest_text
    with pytest.raises(KnowledgeError, match="模型已变化"):
        Retriever(configured.model_copy(update={"embedding_model": "another-model"}))


def test_changed_pdf_requires_reindex(settings, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    shutil.copytree(settings.pdf_dir, pdf_dir)
    configured = settings.model_copy(
        update={"pdf_dir": pdf_dir, "knowledge_dir": tmp_path / "index"}
    )
    build_index(configured)
    shutil.copyfile(next(pdf_dir.glob("*.pdf")), pdf_dir / "new.pdf")
    with pytest.raises(KnowledgeError, match="已变化"):
        read_manifest(configured)
    assert build_index(configured)["page_count"] == 6


def test_failed_rebuild_keeps_previous_snapshot(settings, tmp_path):
    configured = settings.model_copy(update={"knowledge_dir": tmp_path / "index"})
    before = build_index(configured)

    class BrokenEmbeddings(DemoEmbeddings):
        def embed_documents(self, texts):
            raise RuntimeError("模拟 provider 故障")

    with pytest.raises(RuntimeError):
        build_index(configured, BrokenEmbeddings(), force=True)
    assert read_manifest(configured)["snapshot"] == before["snapshot"]
    assert Retriever(configured).search("换货政策")["hits"]


def test_corrupt_manifest_can_be_rebuilt(settings, tmp_path):
    configured = settings.model_copy(update={"knowledge_dir": tmp_path / "index"})
    build_index(configured)
    (configured.index_dir / "manifest.json").write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="损坏"):
        read_manifest(configured)
    assert build_index(configured)["reused"] is False
