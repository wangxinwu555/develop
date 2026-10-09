"""知识库建立阶段：Loader -> Splitter -> Embedding -> Chroma。

本模块由 ingest 命令调用。正常提问不会重新读取 PDF 或生成文档向量。
"""

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from supportflow.config import Settings
from supportflow.embeddings import create_embeddings, embedding_identity

COLLECTION = "supportflow_policies"
INDEX_VERSION = 1


class KnowledgeError(ValueError):
    pass


def pdf_files(settings: Settings) -> list[Path]:
    files = sorted(settings.pdf_dir.glob("*.pdf"))
    if not files:
        raise KnowledgeError(f"没有找到 PDF。请将 PDF 放入 {settings.pdf_dir}。")
    return files


def index_signature(settings: Settings) -> str:
    contents = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in pdf_files(settings)
    ]
    spec = {
        "version": INDEX_VERSION,
        "files": contents,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "embedding": embedding_identity(settings),
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def load_pdf_documents(settings: Settings) -> list[Document]:
    from langchain_community.document_loaders import PyPDFLoader

    documents = []
    for path in pdf_files(settings):
        # 一个 PDF 页面先形成一个 Document，再由 splitter 切成更小的块。
        pages = PyPDFLoader(str(path), mode="page").load()
        for page in pages:
            text = page.page_content.strip()
            if not text:
                continue
            policy = re.search(r"^POLICY_ID:\s*([A-Za-z0-9_-]+)\s*$", text, re.MULTILINE)
            text = re.sub(r"^POLICY_ID:.*$", "", text, flags=re.MULTILINE).strip()
            text = re.sub(r"^SupportFlow \|.*$", "", text, flags=re.MULTILINE).strip()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            page_number = int(page.metadata.get("page", 0)) + 1
            documents.append(
                Document(
                    page_content="\n".join(lines),
                    metadata={
                        "source": path.name,
                        "page": page_number,
                        "policy_id": policy.group(1) if policy else f"{path.stem}-p{page_number}",
                        "title": lines[0] if lines else path.stem,
                    },
                )
            )
    if not documents:
        raise KnowledgeError("PDF 没有可提取的文本。扫描版 PDF 需要先做 OCR。")
    return documents


def split_documents(documents: list[Document], settings: Settings) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    for chunk in chunks:
        identity = json.dumps(chunk.metadata, sort_keys=True, ensure_ascii=False)
        chunk.metadata["chunk_id"] = hashlib.sha256(
            (identity + chunk.page_content).encode()
        ).hexdigest()
    return chunks


def open_vector_store(path: Path, embeddings: Embeddings):
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from langchain_chroma import Chroma

    client = chromadb.PersistentClient(
        path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
    )
    return Chroma(
        client=client,
        collection_name=COLLECTION,
        embedding_function=embeddings,
        collection_metadata={"hnsw:space": "cosine"},
    )


def read_manifest(settings: Settings) -> dict:
    manifest = settings.index_dir / "manifest.json"
    if not manifest.exists():
        raise KnowledgeError("知识库尚未建立，请先运行 python -m scripts.ingest。")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("snapshot"), str):
            raise ValueError("缺少快照信息")
        if not isinstance(data.get("chunk_count"), int) or data["chunk_count"] < 1:
            raise ValueError("缺少文档计数")
    except (ValueError, TypeError) as error:
        raise KnowledgeError("索引清单损坏，请重新运行 ingest --force。") from error
    if data.get("signature") != index_signature(settings):
        raise KnowledgeError("PDF、切分设置或 Embedding 模型已变化，请重新运行 ingest。")
    if not re.fullmatch(r"[a-f0-9]{32}", data["snapshot"]):
        raise KnowledgeError("知识库快照编号不合法，请重新建立索引。")
    path = settings.index_dir / data["snapshot"]
    if not path.is_dir():
        raise KnowledgeError("知识库快照丢失，请重新运行 ingest。")
    return data


def build_index(settings: Settings, embeddings: Embeddings | None = None, *, force=False) -> dict:
    settings.validate_embedding()
    settings.index_dir.mkdir(parents=True, exist_ok=True)
    signature = index_signature(settings)
    manifest_path = settings.index_dir / "manifest.json"
    if not force and manifest_path.exists():
        try:
            existing = read_manifest(settings)
        except KnowledgeError:
            existing = None
        if existing:
            return {**existing, "reused": True}
    documents = load_pdf_documents(settings)
    chunks = split_documents(documents, settings)
    embeddings = embeddings or create_embeddings(settings)
    snapshot = uuid4().hex
    vector_store = open_vector_store(settings.index_dir / snapshot, embeddings)
    # add_documents 会调用 embed_documents，然后把文本、元数据和向量写入 Chroma。
    vector_store.add_documents(chunks, ids=[c.metadata["chunk_id"] for c in chunks])
    if len(vector_store.get(include=[])["ids"]) != len(chunks):
        raise KnowledgeError("向量写入数量不一致，本次索引未启用。")
    manifest = {
        "signature": signature,
        "snapshot": snapshot,
        "embedding": embedding_identity(settings),
        "pdf_files": [p.name for p in pdf_files(settings)],
        "page_count": len(documents),
        "chunk_count": len(chunks),
    }
    temp = settings.index_dir / f"manifest-{snapshot}.tmp"
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(manifest_path)
    return {**manifest, "reused": False}
