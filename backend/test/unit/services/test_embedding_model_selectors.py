from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from yuxi.models.embed import (
    LocalBGEM3Embedding,
    OtherEmbedding,
    RemoteBGEM3DenseLocalSparseEmbedding,
    select_embedding_model,
)
from yuxi.models.providers.cache import ModelInfo


def _local_bge_m3_info(model_id: str = "BAAI/bge-m3") -> ModelInfo:
    """构造本地 BGE-M3 embedding 模型缓存记录。"""
    return ModelInfo(
        provider_id="local-bge-m3",
        model_id=model_id,
        model_type="embedding",
        display_name="BAAI/bge-m3 (Local)",
        api_key="",
        base_url="rag_qa/models/bge-m3",
        provider_type="local",
        dimension=1024,
        batch_size=4,
    )


def _remote_bge_m3_info(model_id: str = "BAAI/bge-m3") -> ModelInfo:
    """构造远端 BGE-M3 embedding 模型缓存记录。"""
    return ModelInfo(
        provider_id="siliconflow",
        model_id=model_id,
        model_type="embedding",
        display_name=model_id,
        api_key="test-key",
        base_url="https://api.example.com/v1/embeddings",
        provider_type="openai",
        dimension=1024,
        batch_size=2,
    )


def _remote_embedding_info(model_id: str = "text-embedding-3-large") -> ModelInfo:
    """构造普通远端 embedding 模型缓存记录。"""
    return ModelInfo(
        provider_id="openai",
        model_id=model_id,
        model_type="embedding",
        display_name=model_id,
        api_key="test-key",
        base_url="https://api.example.com/v1/embeddings",
        provider_type="openai",
        dimension=1536,
        batch_size=2,
    )


def _fake_remote_embedding_response(input_value, dimension: int = 1024) -> dict:
    """按输入长度构造伪造的远端 embedding 响应。"""
    messages = [input_value] if isinstance(input_value, str) else list(input_value)
    return {
        "data": [
            {
                "embedding": [float(len(text))] * dimension,
            }
            for text in messages
        ]
    }


class _FakeHTTPResponse:
    """最小化的 HTTP 响应替身。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


async def _fake_httpx_post(*args, **kwargs):
    del args
    payload = kwargs["json"]
    return _FakeHTTPResponse(_fake_remote_embedding_response(payload["input"]))


def test_select_embedding_model_uses_local_bge_m3_provider(monkeypatch):
    """provider_type=local 时选择本地 BGE-M3 provider。"""
    monkeypatch.setattr(
        "yuxi.models.embed.model_cache.get_model_info",
        lambda spec: _local_bge_m3_info() if spec == "local-bge-m3:BAAI/bge-m3" else None,
    )

    model = select_embedding_model("local-bge-m3:BAAI/bge-m3")

    assert isinstance(model, LocalBGEM3Embedding)
    assert model.model == "BAAI/bge-m3"
    assert model.model_path == "rag_qa/models/bge-m3"
    assert model.batch_size == 4


@pytest.mark.parametrize("model_id", ["BAAI/bge-m3", "Pro/BAAI/bge-m3"])
def test_select_embedding_model_uses_remote_bge_m3_hybrid_provider(monkeypatch, model_id):
    """远端 BGE-M3 spec 应选择 dense 远端、sparse 本地的组合 provider。"""
    spec = f"siliconflow:{model_id}"
    monkeypatch.setattr(
        "yuxi.models.embed.model_cache.get_model_info",
        lambda current_spec: _remote_bge_m3_info(model_id) if current_spec == spec else None,
    )

    model = select_embedding_model(spec)

    assert isinstance(model, RemoteBGEM3DenseLocalSparseEmbedding)
    assert model.model == model_id
    assert model.base_url == "https://api.example.com/v1/embeddings"
    assert model.batch_size == 2
    assert isinstance(model._local_sparse_model, LocalBGEM3Embedding)
    assert model._local_sparse_model.model == "BAAI/bge-m3"


def test_select_embedding_model_keeps_other_remote_embeddings_unchanged(monkeypatch):
    """非 BGE-M3 的远端 embedding 仍走原有 HTTP provider。"""
    spec = "openai:text-embedding-3-large"
    monkeypatch.setattr(
        "yuxi.models.embed.model_cache.get_model_info",
        lambda current_spec: _remote_embedding_info() if current_spec == spec else None,
    )

    model = select_embedding_model(spec)

    assert isinstance(model, OtherEmbedding)
    assert not isinstance(model, RemoteBGEM3DenseLocalSparseEmbedding)


def test_select_embedding_model_rejects_local_non_bge_m3(monkeypatch):
    """local provider 不允许绑定非 BGE-M3 embedding 模型。"""
    monkeypatch.setattr(
        "yuxi.models.embed.model_cache.get_model_info",
        lambda spec: _local_bge_m3_info("BAAI/other") if spec == "local-bge-m3:BAAI/other" else None,
    )

    with pytest.raises(ValueError, match="仅支持 BAAI/bge-m3"):
        select_embedding_model("local-bge-m3:BAAI/other")


def test_local_bge_m3_sparse_weights_filter_special_tokens_and_pool_repeated_ids():
    """本地 sparse 权重聚合会过滤特殊 token，并对重复 token 取最大值。"""
    sparse = LocalBGEM3Embedding._pool_sparse_weights(
        input_ids=[[0, 10, 10, 3, 11, 2]],
        token_weights=[[0.1, 0.2, 0.5, 0.7, float("nan"), 0.9]],
        attention_mask=[[1, 1, 1, 1, 0, 1]],
        special_token_ids={0, 1, 2, 3},
    )

    assert sparse == [{10: 0.5}]


def test_local_bge_m3_sparse_weights_reject_non_finite_active_values():
    """活跃 token 的 sparse 权重不是有限数字时必须失败。"""
    with pytest.raises(ValueError, match="有限数字"):
        LocalBGEM3Embedding._pool_sparse_weights(
            input_ids=[[10]],
            token_weights=[[float("inf")]],
            attention_mask=[[1]],
            special_token_ids=set(),
        )


@pytest.mark.asyncio
async def test_local_bge_m3_sparse_only_batch_encoder_preserves_order(monkeypatch):
    """本地 sparse-only 批量编码保持输入顺序与 batch 切分。"""
    model = LocalBGEM3Embedding(model="BAAI/bge-m3", model_path="unused", batch_size=2)
    calls = []

    def fake_encode_sparse(messages):
        """模拟 sparse-only 输出。"""
        calls.append(list(messages))
        return [{len(text): 1.0} for text in messages]

    monkeypatch.setattr(model, "encode_sparse", fake_encode_sparse)

    sparse = await model.abatch_encode_sparse(["a", "bb", "ccc"])

    assert calls == [["a", "bb"], ["ccc"]]
    assert sparse == [{1: 1.0}, {2: 1.0}, {3: 1.0}]


@pytest.mark.asyncio
async def test_local_bge_m3_batch_sparse_encoder_preserves_order(monkeypatch):
    """本地批量 sparse 编码保持输入顺序与 batch 切分。"""
    model = LocalBGEM3Embedding(model="BAAI/bge-m3", model_path="unused", batch_size=2)
    calls = []

    def fake_encode_with_sparse(messages):
        """模拟本地 provider 返回与输入一一对应的 dense/sparse。"""
        calls.append(list(messages))
        return [[float(len(text))] for text in messages], [{len(text): 1.0} for text in messages]

    monkeypatch.setattr(model, "encode_with_sparse", fake_encode_with_sparse)

    dense, sparse = await model.abatch_encode_with_sparse(["a", "bb", "ccc"])

    assert calls == [["a", "bb"], ["ccc"]]
    assert dense == [[1.0], [2.0], [3.0]]
    assert sparse == [{1: 1.0}, {2: 1.0}, {3: 1.0}]


@pytest.mark.asyncio
async def test_local_bge_m3_connection_probe_does_not_load_full_encoder(monkeypatch):
    """连接探针只检查轻量文件、tokenizer 与 sparse head，不加载完整 encoder。"""
    model = LocalBGEM3Embedding(model="BAAI/bge-m3", model_path="unused", dimension=1024)
    model_dir = Path("rag_qa/models/bge-m3")
    checked_dirs = []

    monkeypatch.setattr("yuxi.models.embed._resolve_local_bge_m3_model_dir", lambda raw_path: model_dir)
    monkeypatch.setattr(
        LocalBGEM3Embedding,
        "_validate_model_files",
        staticmethod(lambda path: checked_dirs.append(path)),
    )
    monkeypatch.setattr(
        LocalBGEM3Embedding,
        "_load_sparse_state_dict",
        staticmethod(lambda path: {"weight": SimpleNamespace(shape=(1, 1024))}),
    )
    monkeypatch.setattr(
        LocalBGEM3Embedding,
        "_load_tokenizer",
        staticmethod(lambda path: SimpleNamespace(vocab_size=250002)),
    )

    def fail_load_backend(self):
        """防止轻量连接探针误加载完整模型。"""
        pytest.fail("连接探针不应加载完整 BGE-M3 encoder")

    monkeypatch.setattr(LocalBGEM3Embedding, "_load_backend", fail_load_backend)

    ok, message = await model.test_connection()

    assert ok is True
    assert message == "本地 BGE-M3 模型文件可用"
    assert checked_dirs == [model_dir]
    assert model._backend is None


@pytest.mark.asyncio
async def test_remote_bge_m3_dense_only_aencode_does_not_load_local_sparse_backend(monkeypatch):
    """远端 dense-only 请求不应触发本地 sparse 模型加载。"""
    model = RemoteBGEM3DenseLocalSparseEmbedding(
        model="BAAI/bge-m3",
        base_url="https://api.example.com/v1/embeddings",
        api_key="test-key",
        dimension=1024,
    )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_httpx_post)

    def fail_load_backend():
        pytest.fail("dense-only 请求不应加载本地 sparse backend")

    monkeypatch.setattr(model._local_sparse_model, "_get_backend", fail_load_backend)

    dense = await model.aencode(["hello"])

    assert dense == [[5.0] * 1024]
    assert model._local_sparse_model._backend is None


@pytest.mark.asyncio
async def test_remote_bge_m3_sparse_combo_uses_remote_dense_and_local_sparse(monkeypatch):
    """远端 BGE-M3 sparse 组合会使用远端 dense 与本地 sparse。"""
    model = RemoteBGEM3DenseLocalSparseEmbedding(
        model="BAAI/bge-m3",
        base_url="https://api.example.com/v1/embeddings",
        api_key="test-key",
        dimension=1024,
        batch_size=2,
    )
    sparse_calls = []

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_httpx_post)

    async def fake_local_sparse(message):
        texts = [message] if isinstance(message, str) else list(message)
        sparse_calls.append(texts)
        return [{len(text): 1.0} for text in texts]

    monkeypatch.setattr(model._local_sparse_model, "aencode_sparse", fake_local_sparse)

    dense, sparse = await model.aencode_with_sparse(["hello", "world!"])

    assert dense == [[5.0] * 1024, [6.0] * 1024]
    assert sparse == [{5: 1.0}, {6: 1.0}]
    assert sparse_calls == [["hello", "world!"]]


@pytest.mark.asyncio
async def test_remote_bge_m3_sparse_batch_combo_preserves_order(monkeypatch):
    """远端 BGE-M3 sparse 批量组合保持顺序与 batch 切分。"""
    model = RemoteBGEM3DenseLocalSparseEmbedding(
        model="BAAI/bge-m3",
        base_url="https://api.example.com/v1/embeddings",
        api_key="test-key",
        dimension=1024,
        batch_size=2,
    )
    dense_calls = []
    sparse_calls = []

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_httpx_post)

    async def fake_remote_dense(message):
        texts = [message] if isinstance(message, str) else list(message)
        dense_calls.append(texts)
        return [[float(len(text))] * 1024 for text in texts]

    async def fake_local_sparse(message):
        texts = [message] if isinstance(message, str) else list(message)
        sparse_calls.append(texts)
        return [{len(text): 1.0} for text in texts]

    monkeypatch.setattr(model, "aencode", fake_remote_dense)
    monkeypatch.setattr(model._local_sparse_model, "aencode_sparse", fake_local_sparse)

    dense, sparse = await model.abatch_encode_with_sparse(["a", "bb", "ccc"])

    assert dense_calls == [["a", "bb"], ["ccc"]]
    assert sparse_calls == [["a", "bb"], ["ccc"]]
    assert dense == [[1.0] * 1024, [2.0] * 1024, [3.0] * 1024]
    assert sparse == [{1: 1.0}, {2: 1.0}, {3: 1.0}]


@pytest.mark.asyncio
async def test_remote_bge_m3_test_connection_checks_local_sparse_backend(monkeypatch):
    """远端 BGE-M3 连接检查要同时校验本地 sparse 目录。"""
    model = RemoteBGEM3DenseLocalSparseEmbedding(
        model="BAAI/bge-m3",
        base_url="https://api.example.com/v1/embeddings",
        api_key="test-key",
        dimension=1024,
    )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_httpx_post)

    async def fake_local_test_connection():
        return True, "本地 BGE-M3 模型文件可用"

    monkeypatch.setattr(model._local_sparse_model, "test_connection", fake_local_test_connection)

    ok, message = await model.test_connection()

    assert ok is True
    assert message == "连接正常"


@pytest.mark.asyncio
async def test_remote_bge_m3_test_connection_fails_when_local_sparse_missing(monkeypatch):
    """远端 BGE-M3 sparse 目录缺失时，连接检查必须失败。"""
    model = RemoteBGEM3DenseLocalSparseEmbedding(
        model="BAAI/bge-m3",
        base_url="https://api.example.com/v1/embeddings",
        api_key="test-key",
        dimension=1024,
    )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_httpx_post)

    async def fake_local_test_connection():
        return False, "本地 BGE-M3 模型目录缺失"

    monkeypatch.setattr(model._local_sparse_model, "test_connection", fake_local_test_connection)

    ok, message = await model.test_connection()

    assert ok is False
    assert "本地 sparse 不可用" in message
