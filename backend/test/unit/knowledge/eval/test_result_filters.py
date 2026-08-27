from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_eval_router
from server.routers.knowledge_eval_router import get_evaluation_run_results
from yuxi.knowledge.eval.service import EvaluationService


def make_item(item_index: int, *, score: float = 1.0, recall: float = 1.0):
    """构造最小逐题评估结果。"""
    return SimpleNamespace(
        item_index=item_index,
        query_text=f"问题 {item_index}",
        gold_chunk_ids=[],
        gold_answer="标准答案",
        generated_answer="生成答案",
        retrieved_chunks=[],
        metrics={"score": score, "recall@10": recall},
    )


class FakeEvaluationRepository:
    """提供筛选分页测试所需的内存仓储。"""

    def __init__(self, items):
        """保存测试结果列表。"""
        self.items = items

    async def get_run(self, _run_id):
        """返回固定的评估运行。"""
        return SimpleNamespace(
            run_id="run_1234abcd",
            kb_id="kb_test",
            name="筛选测试",
            status="completed",
            started_at=None,
            completed_at=None,
            total_items=len(self.items),
            completed_items=len(self.items),
            overall_score=1.0,
            retrieval_config={},
        )

    async def list_run_items(self, _run_id, offset=0, limit=100):
        """按偏移读取逐题结果。"""
        return self.items[offset : offset + limit]

    async def count_run_items(self, _run_id):
        """返回逐题结果总数。"""
        return len(self.items)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_filter", "expected_indexes"),
    [
        ("all", [0, 1, 2, 3]),
        ("answer_errors", [1]),
        ("errors_or_low_recall", [1, 2]),
        ("legacy_errors", [1, 3]),
    ],
)
async def test_get_run_results_filters_before_pagination(result_filter, expected_indexes):
    """筛选必须先于分页并返回筛选后的总数。"""
    service = EvaluationService.__new__(EvaluationService)
    legacy_recall_item = make_item(3)
    legacy_recall_item.metrics["recall@1"] = 0.2
    service.eval_repo = FakeEvaluationRepository(
        [make_item(0), make_item(1, score=0.5), make_item(2, recall=0.99), legacy_recall_item]
    )

    result = await service.get_run_results("kb_test", "run_1234abcd", page=1, page_size=2, result_filter=result_filter)

    assert [item["item_index"] for item in result["items"]] == expected_indexes[:2]
    assert result["pagination"]["total"] == len(expected_indexes)
    assert result["pagination"]["result_filter"] == result_filter


@pytest.mark.asyncio
async def test_router_rejects_unknown_result_filter_before_service_call():
    """路由在调用服务前拒绝未知筛选值。"""
    with pytest.raises(HTTPException) as exc_info:
        await get_evaluation_run_results(
            "kb_test",
            "run_1234abcd",
            result_filter="unknown",
            current_user=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "无效的评估结果筛选条件"


@pytest.mark.asyncio
async def test_router_preserves_legacy_error_only_filter(monkeypatch):
    """旧 error_only 参数继续使用原错误筛选语义。"""
    captured = {}

    class ServiceStub:
        """记录路由传给服务的筛选值。"""

        async def get_run_results(self, _kb_id, _run_id, **kwargs):
            """返回最小成功结果。"""
            captured.update(kwargs)
            return {"items": []}

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", ServiceStub)

    response = await get_evaluation_run_results(
        "kb_test",
        "run_1234abcd",
        error_only=True,
        current_user=SimpleNamespace(),
    )

    assert response["message"] == "success"
    assert captured["result_filter"] == "legacy_errors"


@pytest.mark.asyncio
async def test_router_rejects_mixed_result_filter_contracts():
    """新旧筛选参数同时出现时拒绝歧义请求。"""
    with pytest.raises(HTTPException) as exc_info:
        await get_evaluation_run_results(
            "kb_test",
            "run_1234abcd",
            result_filter="all",
            error_only=True,
            current_user=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "不能同时使用 result_filter 和 error_only"
