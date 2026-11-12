"""知识解析、评估与图谱读取的独立回归案例。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.infrastructure.document_parsing.parser import parse
from yuxi.modules.knowledge.chunking.dispatcher import chunk_markdown
from yuxi.modules.knowledge.evaluation.evaluator import aggregate_metrics
from yuxi.modules.knowledge.evaluation.metrics import EvaluationMetricsCalculator
from yuxi.modules.knowledge.evaluation.service import EvaluationService
from yuxi.modules.knowledge.graphs import service as graph_module
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.shared.datetime import utc_now


@pytest.mark.parametrize("miss_first", [True, False])
def test_zero_retrieval_counts_as_zero_independently_of_question_order(miss_first):
    """有标准片段的零命中题与命中题共同拥有固定指标口径。"""
    miss = EvaluationMetricsCalculator.calculate_retrieval_metrics([], ["gold"])
    hit = EvaluationMetricsCalculator.calculate_retrieval_metrics([{"chunk_id": "gold"}], ["gold"])
    metrics, score = aggregate_metrics([miss, hit] if miss_first else [hit, miss], [], include_overall_score=True)
    assert metrics["recall@10"] == 0.5
    assert score == metrics["overall_score"] == 0.5
    assert set(miss) == set(hit)
    assert all(value == 0 for value in miss.values())


async def test_parsed_csv_qa_keeps_only_data_rows_and_source_lines(tmp_path):
    """真实 CSV 转换后只从 Markdown 表体构建问答，不再次按逗号拆分。"""
    source = tmp_path / "faq.csv"
    source.write_text('question,answer\n"How, exactly?","With ""care"""\n', encoding="utf-8")
    parsed = await parse(source, tmp_path / "parsed")
    markdown = parsed.markdown_path.read_text(encoding="utf-8")
    records = chunk_markdown(markdown, "file", source.name, {"chunk_preset_id": "qa"})
    assert [record["content"] for record in records] == ['问题：How, exactly?\t回答：With "care"']
    lines = markdown.splitlines()
    assert "How, exactly?" in lines[records[0]["start_line"] - 1]
    assert records[0]["start_line"] == records[0]["end_line"]


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("query_nodes", {}),
        ("query_seed_subgraph", {"entity_ids": ["entity"], "max_nodes": 10}),
        ("get_labels", {}),
        ("get_stats", {}),
    ],
)
async def test_graph_storage_failure_is_not_an_empty_success(monkeypatch, method, kwargs):
    """查询存储不可用时保留失败语义。"""
    monkeypatch.setattr(graph_module, "run_neo4j_query_io", AsyncMock(side_effect=ConnectionError("graph unavailable")))
    service = KnowledgeGraphService()
    with pytest.raises(ConnectionError, match="graph unavailable"):
        await getattr(service, method)("kb", **kwargs)


async def test_shared_entity_removes_every_display_name_from_invisible_source():
    """共享实体保留归一身份，但不能残留不可见来源的原始名称副本。"""
    repository = SimpleNamespace(visible_graph_ids=AsyncMock(return_value=(set(), {"shared"})))
    service = KnowledgeGraphService(graph_repo=repository)
    node = service._normalize_node(
        {
            "id": "node",
            "labels": ["Entity"],
            "properties": {"entity_id": "shared", "normalized_name": "acme corp", "name": "Acme    CORP", "source_chunk_id": "deleted"},
        },
        "kb",
    )
    result = await service._filter_visible_subgraph("kb", {"nodes": [node], "edges": []})
    assert result["nodes"][0]["name"] == "acme corp"
    assert result["nodes"][0]["normalized"]["name"] == "acme corp"
    assert "source_chunk_id" not in result["nodes"][0]["properties"]


async def test_dataset_read_does_not_overwrite_concurrent_completion():
    """读取旧作业快照时完成事务已经提交，读取不能把状态回退为 running。"""
    now = utc_now()
    saved = {"source": "generated", "status": "running", "progress": 10, "job_id": "job"}
    row = SimpleNamespace(
        dataset_id="dataset",
        name="test",
        description="",
        kb_id="kb",
        item_count=1,
        has_gold_chunks=True,
        has_gold_answers=False,
        created_by="test",
        created_at=now,
        updated_at=now,
        build_metadata=deepcopy(saved),
    )

    async def read_job(job_id):
        """在读取作业边界模拟已经提交的完成事务。"""
        saved.update(status="completed", progress=100)
        return SimpleNamespace(id="job", status="running", progress=95, message="running")

    async def update_dataset(dataset_id, data):
        """捕获真实旧读路径对业务事实的覆盖。"""
        saved.update(data["build_metadata"])

    service = EvaluationService()
    service.job_repo = SimpleNamespace(get_by_id=read_job)
    service.eval_repo = SimpleNamespace(list_datasets=AsyncMock(return_value=[row]), update_dataset=update_dataset)
    await service.list_datasets("kb")
    assert saved["status"] == "completed"
    assert saved["progress"] == 100
