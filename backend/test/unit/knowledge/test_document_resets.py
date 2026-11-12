"""文档撤销的 worker JSON 边界拒绝非法持久范围。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.services.document_resets import run_document_reset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "unknown"},
        {"action": []},
        {"file_id": ""},
        {"file_id": 123},
        {"generation": 0},
        {"generation": True},
        {"generation": "1"},
        {"params": []},
        {"action": "save_markdown", "content": [], "expected_revision": "revision"},
        {"action": "save_markdown", "content": "draft", "expected_revision": None},
        {"action": "save_markdown", "content": "draft", "expected_revision": "revision", "source_markdown": []},
    ],
)
async def test_reset_worker_rejects_invalid_scope_before_io(overrides):
    """非法 JSON 在访问投影、文件或对象存储前显式失败。"""
    payload = {"kb_id": "kb", "file_id": "file", "action": "clear_parse", "generation": 1, "params": {}, **overrides}
    with pytest.raises(ValueError):
        await run_document_reset(SimpleNamespace(payload=payload))
