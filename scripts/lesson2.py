"""逐步观察 PDF -> Document -> Chunk -> Embedding -> Chroma -> 检索。"""

from supportflow.config import Settings
from supportflow.embeddings import create_embeddings
from supportflow.ingest import build_index, load_pdf_documents, split_documents
from supportflow.retrieval import Retriever


def main():
    settings = Settings()
    pages = load_pdf_documents(settings)
    print(f"1. Loader 读取 {len(pages)} 个页面 Document。")
    print("第一页元数据：", pages[0].metadata)
    chunks = split_documents(pages, settings)
    print(f"2. Splitter 生成 {len(chunks)} 个文本块。")
    print("第一个块：", chunks[0].page_content)
    embeddings = create_embeddings(settings)
    vector = embeddings.embed_query("耳机质量问题换货")
    print(f"3. 问题向量维度：{len(vector)}；前8项：{vector[:8]}")
    if settings.embedding_provider == "demo":
        print("   当前使用哈希编码器，不具备预训练模型的语义能力。")
    else:
        print(f"   当前使用真实嵌入模型：{settings.embedding_model}。")
    result = build_index(settings, embeddings)
    print(f"4. Chroma 保存 {result['chunk_count']} 个块；复用：{result['reused']}")
    hits = Retriever(settings, embeddings).search("耳机质量问题换货")["hits"]
    print("5. 问题向量检索到的文本：")
    for hit in hits:
        print(f"   {hit['source']} 第{hit['page']}页，相似度 {hit['score']}")
        print(hit["text"])


if __name__ == "__main__":
    main()
