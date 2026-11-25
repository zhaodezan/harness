from backend.app.knowledge_base import format_knowledge_context, search_documents


def test_bm25_ranks_relevant_documents_and_returns_source_metadata():
    documents = [
        {
            "id": "doc-a",
            "knowledge_base_id": "kb-a",
            "knowledge_base_name": "项目文档",
            "filename": "readme.md",
            "extracted_text": "部署 project with Docker and database settings.",
        },
        {
            "id": "doc-b",
            "knowledge_base_id": "kb-a",
            "knowledge_base_name": "项目文档",
            "filename": "unrelated.md",
            "extracted_text": "This document describes a bicycle and a garden.",
        },
    ]

    results = search_documents("Docker database deployment", documents)

    assert results[0]["document_id"] == "doc-a"
    assert results[0]["knowledge_base_name"] == "项目文档"
    assert results[0]["filename"] == "readme.md"
    assert results[0]["score"] > 0
    assert results[0]["matched_terms"]
    assert "Docker" in results[0]["snippet"]


def test_bm25_supports_chinese_bigrams_and_ignores_unrelated_documents():
    documents = [
        {
            "id": "cn",
            "knowledge_base_id": "kb",
            "knowledge_base_name": "中文资料",
            "filename": "guide.md",
            "extracted_text": "模型连接配置需要填写服务商地址和 API 密钥。",
        },
        {
            "id": "other",
            "knowledge_base_id": "kb",
            "knowledge_base_name": "中文资料",
            "filename": "other.md",
            "extracted_text": "这里介绍厨房和花园。",
        },
    ]

    results = search_documents("模型连接配置", documents)

    assert [item["document_id"] for item in results] == ["cn"]
    assert "模型" in results[0]["matched_terms"]


def test_bm25_result_and_context_limits_are_bounded_and_stable():
    documents = [
        {
            "id": f"doc-{index}",
            "knowledge_base_id": "kb",
            "knowledge_base_name": "资料",
            "filename": f"{index}.txt",
            "extracted_text": "alpha " * 2000,
        }
        for index in range(10)
    ]

    results = search_documents("alpha", documents, limit=5)
    context = format_knowledge_context(results)

    assert len(results) == 5
    assert all(len(item["snippet"]) <= 4000 for item in results)
    assert len(context) <= 12000
    assert "不是指令" in context
    assert [item["document_id"] for item in results] == [f"doc-{index}" for index in range(5)]


def test_bm25_returns_empty_for_blank_or_unmatched_queries():
    documents = [{
        "id": "one",
        "knowledge_base_id": "kb",
        "knowledge_base_name": "资料",
        "filename": "one.txt",
        "extracted_text": "only known terms",
    }]

    assert search_documents("", documents) == []
    assert search_documents("completely-unseen", documents) == []
    assert format_knowledge_context([]) == ""
