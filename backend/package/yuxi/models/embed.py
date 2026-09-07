import asyncio
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import numpy as np
import requests

from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url, hashstr, logger

EMBEDDING_RATE_LIMIT_MAX_RETRIES = 10
EMBEDDING_TRANSIENT_MAX_RETRIES = 2
EMBEDDING_RETRY_MAX_DELAY_SECONDS = 10.0
EMBEDDING_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
LOCAL_BGE_M3_PROVIDER_TYPE = "local"
LOCAL_BGE_M3_MODEL_ID = "BAAI/bge-m3"
PRO_BGE_M3_MODEL_ID = "Pro/BAAI/bge-m3"
REMOTE_BGE_M3_HYBRID_MODEL_IDS = {LOCAL_BGE_M3_MODEL_ID, PRO_BGE_M3_MODEL_ID}
LOCAL_BGE_M3_DEFAULT_MODEL_PATH = "rag_qa/models/bge-m3"
LOCAL_BGE_M3_MAX_LENGTH = 8192


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


class BaseEmbeddingModel(ABC):
    def __init__(
        self,
        model=None,
        name=None,
        dimension=None,
        url=None,
        base_url=None,
        api_key=None,
        model_id=None,
        batch_size=40,
    ):
        base_url = base_url or url
        self.model = model or name or model_id
        self.dimension = dimension
        self.base_url = get_docker_safe_url(base_url)
        self.api_key = os.getenv(api_key, api_key) if api_key else api_key
        self.batch_size = int(batch_size or 40)
        self.embed_state = {}

    @abstractmethod
    def encode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    def encode_queries(self, queries: list[str] | str) -> list[list[float]]:
        return self.encode(queries)

    @abstractmethod
    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    async def aencode_queries(self, queries: list[str] | str) -> list[list[float]]:
        return await self.aencode(queries)

    def batch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            response = self.encode(group_msg)
            data.extend(response)
            if task_id:
                self.embed_state[task_id]["progress"] = i + len(group_msg)

        if task_id:
            self.embed_state[task_id]["status"] = "completed"

        return data

    async def abatch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Async encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            res = await self.aencode(group_msg)
            data.extend(res)
            if task_id:
                self.embed_state[task_id]["progress"] = i + len(group_msg)

        if task_id:
            self.embed_state[task_id]["status"] = "completed"

        return data

    async def abatch_encode_with_sparse(
        self, messages: list[str], batch_size: int | None = None
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """批量返回稠密和模型原生稀疏向量；不支持时显式失败。"""
        raise ValueError("当前 embedding provider 未提供 BGE-M3 sparse 输出")

    async def test_connection(self) -> tuple[bool, str]:
        try:
            embeddings = await self.aencode(["Hello world"])
            if self.dimension not in (None, ""):
                actual_dimension = len(embeddings[0]) if embeddings else 0
                expected_dimension = int(self.dimension)
                if actual_dimension != expected_dimension:
                    return False, f"Embedding 维度不一致：配置 {expected_dimension}，实际 {actual_dimension}"
            return True, "连接正常"
        except Exception as e:
            error_msg = str(e)
            error_msg += f", maybe you can check the `{self.base_url}` end with /embeddings as examples."
            logger.error(error_msg)
            return False, error_msg


class OtherEmbedding(BaseEmbeddingModel):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, message: list[str] | str) -> dict:
        return {"model": self.model, "input": message}

    def encode(self, message: list[str] | str) -> list[list[float]]:
        payload = self.build_payload(message)
        retry_index = 0

        self._log_long_inputs(message)

        while True:
            try:
                response = requests.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                response.raise_for_status()
                return self._extract_embeddings(response.json())
            except requests.RequestException as e:
                retry = self._prepare_retry(
                    message,
                    retry_index=retry_index,
                    response=getattr(e, "response", None),
                    error=e,
                )
                if retry:
                    retry_index, delay = retry
                    time.sleep(delay)
                    continue

                logger.error(f"Embedding request failed: {e}, {payload}")
                raise ValueError(f"Embedding request failed: {e}")

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        payload = self.build_payload(message)
        self._log_long_inputs(message)
        async with httpx.AsyncClient() as client:
            retry_index = 0
            while True:
                try:
                    response = await client.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                    response.raise_for_status()
                    return self._extract_embeddings(response.json())
                except httpx.HTTPStatusError as e:
                    retry = self._prepare_retry(
                        message,
                        retry_index=retry_index,
                        response=e.response,
                        error=e,
                    )
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise
                except httpx.RequestError as e:
                    retry = self._prepare_retry(message, retry_index=retry_index, error=e)
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise ValueError(f"Embedding async request failed: {e}, {payload}, {self.base_url=}")

    async def aencode_with_sparse(self, message: list[str] | str) -> tuple[list[list[float]], list[dict[int, float]]]:
        """解析 provider 返回的 dense embedding 与 BGE-M3 lexical sparse 权重。"""
        payload = self.build_payload(message)
        messages = [message] if isinstance(message, str) else message
        self._log_long_inputs(messages)
        async with httpx.AsyncClient() as client:
            response = await client.post(self.base_url, json=payload, headers=self.headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        items = result.get("data") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) != len(messages):
            raise ValueError("BGE-M3 sparse provider response has invalid data length")
        dense: list[list[float]] = []
        sparse: list[dict[int, float]] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("BGE-M3 sparse provider response is missing embedding")
            raw_sparse = item.get("sparse_embedding", item.get("lexical_weights"))
            if not isinstance(raw_sparse, Mapping) or not raw_sparse:
                raise ValueError("BGE-M3 sparse provider response is missing sparse_embedding")
            normalized: dict[int, float] = {}
            for key, value in raw_sparse.items():
                try:
                    index = int(key)
                    numeric = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("BGE-M3 sparse provider returned invalid sparse weights") from exc
                if index < 0 or not np.isfinite(numeric):
                    raise ValueError("BGE-M3 sparse provider returned invalid sparse weights")
                normalized[index] = numeric
            dense.append(item["embedding"])
            sparse.append(normalized)
        return dense, sparse

    async def abatch_encode_with_sparse(
        self, messages: list[str], batch_size: int | None = None
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """按 provider batch_size 批量请求并保持 dense/sparse 顺序一致。"""
        batch_size = batch_size or self.batch_size
        dense: list[list[float]] = []
        sparse: list[dict[int, float]] = []
        for start in range(0, len(messages), batch_size):
            batch_dense, batch_sparse = await self.aencode_with_sparse(messages[start : start + batch_size])
            dense.extend(batch_dense)
            sparse.extend(batch_sparse)
        return dense, sparse

    @staticmethod
    def _retry_delay_seconds(retry_index: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), EMBEDDING_RETRY_MAX_DELAY_SECONDS)
            except ValueError:
                pass
        return min(float(2 ** (retry_index - 1)), EMBEDDING_RETRY_MAX_DELAY_SECONDS)

    def _prepare_retry(
        self,
        message: list[str] | str,
        *,
        retry_index: int,
        response=None,
        error: Exception | None = None,
    ) -> tuple[int, float] | None:
        status_code = getattr(response, "status_code", None)
        response_text = str(getattr(response, "text", "") or "")
        messages = [message] if isinstance(message, str) else message

        if status_code == 400 and response is not None:
            logger.warning(
                "Embedding request returned 400 Bad Request: "
                f"model={self.model}, base_url={self.base_url}, input_count={len(messages)}, "
                f"input_lengths={[len(item) for item in messages]}, body={response_text[:2000]}"
            )

        if status_code == 429:
            max_retries = EMBEDDING_RATE_LIMIT_MAX_RETRIES
        elif status_code in EMBEDDING_RETRYABLE_STATUS_CODES or status_code is None:
            max_retries = EMBEDDING_TRANSIENT_MAX_RETRIES
        else:
            max_retries = 0
        if retry_index >= max_retries:
            return None

        next_retry_index = retry_index + 1
        retry_after = response.headers.get("Retry-After") if response is not None else None
        delay = self._retry_delay_seconds(next_retry_index, retry_after)
        reason = f"status={status_code}" if status_code is not None else f"error={type(error).__name__}"
        logger.warning(
            "Retrying embedding request: "
            f"{reason}, model={self.model}, base_url={self.base_url}, "
            f"retry={next_retry_index}/{max_retries}, delay={delay:.1f}s, "
            f"input_count={len(messages)}, body={response_text[:1000]}"
        )
        return next_retry_index, delay

    @staticmethod
    def _extract_embeddings(result: dict) -> list[list[float]]:
        if not isinstance(result, dict) or "data" not in result:
            raise ValueError(f"Embedding failed: Invalid response format {result}")
        return [item["embedding"] for item in result["data"]]

    @staticmethod
    def _log_long_inputs(message: list[str] | str, threshold: int = 4000) -> None:
        """调试辅助：记录超过字符阈值的 embedding 输入位置与长度，不输出内容以免泄露用户数据。"""
        messages = [message] if isinstance(message, str) else message
        for idx, text in enumerate(messages):
            if text and len(text) > threshold:
                logger.warning(f"超长 embedding 输入 index={idx}, len={len(text)}")


def _repo_root_from_embed_module() -> Path:
    """根据当前模块位置推导仓库根目录，用于解析默认本地模型路径。"""
    return Path(__file__).resolve().parents[4]


def _strip_local_path_scheme(raw_path: str) -> str:
    """移除 local/file 前缀，保留可交给 pathlib 解析的路径文本。"""
    if raw_path.startswith("local://"):
        return raw_path.removeprefix("local://")
    if raw_path.startswith("file://"):
        path = raw_path.removeprefix("file://")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            return path[1:]
        return path
    return raw_path


def _resolve_local_bge_m3_model_dir(raw_path: str | None) -> Path:
    """把 provider 配置里的本地模型路径解析为真实目录。"""
    configured = raw_path or os.getenv("YUXI_LOCAL_BGE_M3_MODEL_PATH") or LOCAL_BGE_M3_DEFAULT_MODEL_PATH
    cleaned = _strip_local_path_scheme(str(configured).strip())
    if not cleaned:
        cleaned = LOCAL_BGE_M3_DEFAULT_MODEL_PATH

    path = Path(cleaned).expanduser()
    candidates = [path] if path.is_absolute() else [Path.cwd() / path, _repo_root_from_embed_module() / path]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise ValueError(f"本地 BGE-M3 模型目录不存在：{searched}")


def _validate_local_bge_m3_model_id(model_id: str) -> None:
    """确保 local provider 只绑定当前已实现的 BGE-M3 模型。"""
    if model_id != LOCAL_BGE_M3_MODEL_ID:
        raise ValueError(f"local embedding provider 仅支持 {LOCAL_BGE_M3_MODEL_ID}")


class LocalBGEM3Embedding(BaseEmbeddingModel):
    """本地 BGE-M3 embedding provider，返回 dense 和模型原生 lexical sparse。"""

    def __init__(self, *, model_path: str | None = None, max_length: int = LOCAL_BGE_M3_MAX_LENGTH, **kwargs) -> None:
        """初始化本地模型配置；权重在首次编码时惰性加载。"""
        configured_base_url = kwargs.pop("base_url", None)
        configured_url = kwargs.pop("url", None)
        configured_model_path = model_path or configured_base_url or configured_url
        super().__init__(base_url=configured_model_path, **kwargs)
        _validate_local_bge_m3_model_id(str(self.model or ""))
        self.model_path = configured_model_path
        self.max_length = int(max_length or LOCAL_BGE_M3_MAX_LENGTH)
        self._backend: dict[str, Any] | None = None
        self._load_lock = Lock()
        self._inference_lock = Lock()

    def encode(self, message: list[str] | str) -> list[list[float]]:
        """同步生成本地 BGE-M3 dense embedding。"""
        dense, _sparse = self.encode_with_sparse(message)
        return dense

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        """异步生成本地 BGE-M3 dense embedding，推理在线程池中执行。"""
        return await asyncio.to_thread(self.encode, message)

    def encode_with_sparse(self, message: list[str] | str) -> tuple[list[list[float]], list[dict[int, float]]]:
        """同步生成本地 BGE-M3 dense embedding 与 lexical sparse 权重。"""
        messages = self._normalize_messages(message)
        if not messages:
            return [], []
        return self._encode_batch(messages)

    def encode_sparse(self, message: list[str] | str) -> list[dict[int, float]]:
        """同步生成本地 BGE-M3 lexical sparse 权重。"""
        messages = self._normalize_messages(message)
        if not messages:
            return []
        return self._encode_sparse_batch(messages)

    async def aencode_with_sparse(self, message: list[str] | str) -> tuple[list[list[float]], list[dict[int, float]]]:
        """异步生成本地 BGE-M3 dense embedding 与 lexical sparse 权重。"""
        return await asyncio.to_thread(self.encode_with_sparse, message)

    async def aencode_sparse(self, message: list[str] | str) -> list[dict[int, float]]:
        """异步生成本地 BGE-M3 lexical sparse 权重。"""
        return await asyncio.to_thread(self.encode_sparse, message)

    async def abatch_encode_with_sparse(
        self, messages: list[str], batch_size: int | None = None
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """按 batch_size 批量生成 dense 和 sparse，并保持输入顺序。"""
        batch_size = batch_size or self.batch_size
        dense: list[list[float]] = []
        sparse: list[dict[int, float]] = []
        for start in range(0, len(messages), batch_size):
            batch_dense, batch_sparse = await self.aencode_with_sparse(messages[start : start + batch_size])
            dense.extend(batch_dense)
            sparse.extend(batch_sparse)
        return dense, sparse

    async def abatch_encode_sparse(self, messages: list[str], batch_size: int | None = None) -> list[dict[int, float]]:
        """按 batch_size 批量生成 lexical sparse 权重，并保持输入顺序。"""
        batch_size = batch_size or self.batch_size
        sparse: list[dict[int, float]] = []
        for start in range(0, len(messages), batch_size):
            sparse.extend(await self.aencode_sparse(messages[start : start + batch_size]))
        return sparse

    async def test_connection(self) -> tuple[bool, str]:
        """轻量检查本地目录、tokenizer 与 sparse head，不在状态探针中加载完整大模型。"""
        try:
            model_dir = _resolve_local_bge_m3_model_dir(self.model_path)
            self._validate_model_files(model_dir)
            state = await asyncio.to_thread(self._load_sparse_state_dict, model_dir)
            expected_dimension = int(self.dimension or 1024)
            weight = state.get("weight")
            if weight is None or tuple(weight.shape) != (1, expected_dimension):
                actual_shape = tuple(weight.shape) if weight is not None else None
                return False, f"BGE-M3 sparse head 维度不一致：配置 {expected_dimension}，实际 {actual_shape}"

            tokenizer = await asyncio.to_thread(self._load_tokenizer, model_dir)
            if int(getattr(tokenizer, "vocab_size", 0) or 0) <= 0:
                return False, "BGE-M3 tokenizer vocab_size 无效"
            return True, "本地 BGE-M3 模型文件可用"
        except Exception as e:
            logger.error(f"本地 BGE-M3 模型检查失败: {e}")
            return False, str(e)

    def _encode_batch(self, messages: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        """执行一次本地 BGE-M3 batch 推理，并抽取 dense/sparse 输出。"""
        backend = self._get_backend()
        tokenizer = backend["tokenizer"]
        encoder = backend["encoder"]
        sparse_linear = backend["sparse_linear"]
        torch = backend["torch"]
        device = backend["device"]

        with self._inference_lock:
            encoded = tokenizer(
                messages,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad():
                outputs = encoder(**encoded, return_dict=True)
                hidden_state = outputs.last_hidden_state
                dense_tensor = torch.nn.functional.normalize(hidden_state[:, 0], p=2, dim=1)
                sparse_tensor = torch.relu(sparse_linear(hidden_state)).squeeze(-1)

            dense = dense_tensor.detach().cpu().to(dtype=torch.float32).tolist()
            sparse = self._pool_sparse_weights(
                encoded["input_ids"].detach().cpu().tolist(),
                sparse_tensor.detach().cpu().to(dtype=torch.float32).tolist(),
                encoded["attention_mask"].detach().cpu().tolist(),
                backend["special_token_ids"],
            )
        return dense, sparse

    def _get_backend(self) -> dict[str, Any]:
        """惰性加载并缓存 tokenizer、encoder 和 sparse head。"""
        if self._backend is not None:
            return self._backend
        with self._load_lock:
            if self._backend is None:
                self._backend = self._load_backend()
        return self._backend

    def _load_backend(self) -> dict[str, Any]:
        """加载本地 BGE-M3 推理组件，并放到可用设备上。"""
        import torch
        from transformers import AutoModel

        model_dir = _resolve_local_bge_m3_model_dir(self.model_path)
        self._validate_model_files(model_dir)
        tokenizer = self._load_tokenizer(model_dir)
        encoder = AutoModel.from_pretrained(str(model_dir), local_files_only=True)
        hidden_size = int(getattr(encoder.config, "hidden_size", self.dimension or 1024))
        sparse_linear = torch.nn.Linear(hidden_size, 1)
        sparse_linear.load_state_dict(self._load_sparse_state_dict(model_dir))

        device_name = os.getenv("YUXI_LOCAL_BGE_M3_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
        device = torch.device(device_name)
        encoder.to(device)
        sparse_linear.to(device)
        encoder.eval()
        sparse_linear.eval()

        logger.info(f"Loaded local BGE-M3 embedding model from {model_dir} on {device}")
        return {
            "torch": torch,
            "tokenizer": tokenizer,
            "encoder": encoder,
            "sparse_linear": sparse_linear,
            "device": device,
            "special_token_ids": self._special_token_ids(tokenizer),
        }

    @staticmethod
    def _normalize_messages(message: list[str] | str) -> list[str]:
        """校验并复制 embedding 输入文本列表。"""
        messages = [message] if isinstance(message, str) else list(message)
        if any(not isinstance(text, str) for text in messages):
            raise ValueError("BGE-M3 embedding 输入必须是字符串列表")
        return messages

    @staticmethod
    def _validate_model_files(model_dir: Path) -> None:
        """校验本地 BGE-M3 推理必须文件存在。"""
        required = ["config.json", "pytorch_model.bin", "sparse_linear.pt", "tokenizer_config.json"]
        missing = [name for name in required if not (model_dir / name).is_file()]
        if not (model_dir / "tokenizer.json").is_file() and not (model_dir / "sentencepiece.bpe.model").is_file():
            missing.append("tokenizer.json 或 sentencepiece.bpe.model")
        if missing:
            raise ValueError(f"本地 BGE-M3 模型目录缺少必需文件：{', '.join(missing)}")

    @staticmethod
    def _load_sparse_state_dict(model_dir: Path) -> Mapping[str, Any]:
        """只读取 sparse_linear.pt 权重，用于轻量探针与推理加载。"""
        import torch

        try:
            return torch.load(model_dir / "sparse_linear.pt", map_location="cpu", weights_only=True)
        except TypeError:
            return torch.load(model_dir / "sparse_linear.pt", map_location="cpu")

    @staticmethod
    def _load_tokenizer(model_dir: Path) -> Any:
        """从本地目录加载 tokenizer，不访问远端模型仓库。"""
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)

    @staticmethod
    def _special_token_ids(tokenizer: Any) -> set[int]:
        """收集需要从 sparse 向量中排除的特殊 token id。"""
        return {
            int(token_id)
            for token_id in (
                getattr(tokenizer, "cls_token_id", None),
                getattr(tokenizer, "eos_token_id", None),
                getattr(tokenizer, "pad_token_id", None),
                getattr(tokenizer, "unk_token_id", None),
            )
            if token_id is not None
        }

    @staticmethod
    def _pool_sparse_weights(
        input_ids: list[list[int]],
        token_weights: list[list[float]],
        attention_mask: list[list[int]],
        special_token_ids: set[int],
    ) -> list[dict[int, float]]:
        """按 token id 聚合最大 sparse 权重，并过滤 mask 与特殊 token。"""
        sparse_rows: list[dict[int, float]] = []
        for ids, weights, mask in zip(input_ids, token_weights, attention_mask, strict=True):
            if not (len(ids) == len(weights) == len(mask)):
                raise ValueError("BGE-M3 sparse token 序列长度不一致")
            sparse: dict[int, float] = {}
            for token_id, weight, keep in zip(ids, weights, mask, strict=True):
                if not keep:
                    continue
                index = int(token_id)
                if index in special_token_ids:
                    continue
                numeric = float(weight)
                if not np.isfinite(numeric):
                    raise ValueError("BGE-M3 sparse 权重必须是有限数字")
                if numeric <= 0.0:
                    continue
                previous = sparse.get(index)
                if previous is None or numeric > previous:
                    sparse[index] = numeric
            sparse_rows.append(sparse)
        return sparse_rows

    def _encode_sparse_batch(self, messages: list[str]) -> list[dict[int, float]]:
        """执行一次本地 BGE-M3 batch 推理，只抽取 sparse 输出。"""
        backend = self._get_backend()
        tokenizer = backend["tokenizer"]
        encoder = backend["encoder"]
        sparse_linear = backend["sparse_linear"]
        torch = backend["torch"]
        device = backend["device"]

        with self._inference_lock:
            encoded = tokenizer(
                messages,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad():
                outputs = encoder(**encoded, return_dict=True)
                hidden_state = outputs.last_hidden_state
                sparse_tensor = torch.relu(sparse_linear(hidden_state)).squeeze(-1)

            return self._pool_sparse_weights(
                encoded["input_ids"].detach().cpu().tolist(),
                sparse_tensor.detach().cpu().to(dtype=torch.float32).tolist(),
                encoded["attention_mask"].detach().cpu().tolist(),
                backend["special_token_ids"],
            )


class RemoteBGEM3DenseLocalSparseEmbedding(OtherEmbedding):
    """远端 BGE-M3 dense 与本地 BGE-M3 sparse 的组合 provider。"""

    def __init__(self, *, local_model_path: str | None = None, **kwargs) -> None:
        """初始化远端 dense provider，并预置本地 sparse provider。"""
        super().__init__(**kwargs)
        self._local_sparse_model = LocalBGEM3Embedding(
            model=LOCAL_BGE_M3_MODEL_ID,
            model_path=local_model_path,
            dimension=self.dimension,
            batch_size=self.batch_size,
        )

    async def aencode_with_sparse(self, message: list[str] | str) -> tuple[list[list[float]], list[dict[int, float]]]:
        """远端生成 dense，并用本地 BGE-M3 生成 sparse。"""
        dense = await self.aencode(message)
        sparse = await self._local_sparse_model.aencode_sparse(message)
        return dense, sparse

    async def abatch_encode_with_sparse(
        self, messages: list[str], batch_size: int | None = None
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """按 batch_size 组合远端 dense 与本地 sparse，并保持顺序。"""
        batch_size = batch_size or self.batch_size
        dense: list[list[float]] = []
        sparse: list[dict[int, float]] = []
        for start in range(0, len(messages), batch_size):
            batch = messages[start : start + batch_size]
            batch_dense = await self.aencode(batch)
            batch_sparse = await self._local_sparse_model.aencode_sparse(batch)
            dense.extend(batch_dense)
            sparse.extend(batch_sparse)
        return dense, sparse

    async def test_connection(self) -> tuple[bool, str]:
        """同时验证远端 dense 连接与本地 sparse 目录可用。"""
        success, message = await super().test_connection()
        if not success:
            return success, message

        sparse_ok, sparse_message = await self._local_sparse_model.test_connection()
        if not sparse_ok:
            return False, f"远端 dense 连接正常，但本地 sparse 不可用：{sparse_message}"
        return True, "连接正常"


def get_embedding_model_info_by_id(model_id: str) -> dict:
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown embedding model spec: {model_id}")
    if info.model_type != "embedding":
        raise ValueError(f"Model {model_id} is not an embedding model (type={info.model_type})")

    logger.info(f"Loaded embedding model info for {model_id}")
    return {
        "name": info.model_id,
        "display_name": info.display_name,
        "dimension": info.dimension,
        "base_url": info.base_url,
        "api_key": info.api_key,
        "model_id": info.spec,
        "batch_size": info.batch_size,
    }


def select_embedding_model(model_id: str):
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown embedding model spec: {model_id}")

    if info.model_type != "embedding":
        raise ValueError(f"Model {model_id} is not an embedding model (type={info.model_type})")

    logger.info(f"Selecting embedding model: {model_id} (provider_type={info.provider_type})")
    if info.provider_type == LOCAL_BGE_M3_PROVIDER_TYPE:
        return LocalBGEM3Embedding(
            model=info.model_id,
            model_path=info.base_url,
            dimension=info.dimension,
            batch_size=info.batch_size,
        )
    if info.model_id in REMOTE_BGE_M3_HYBRID_MODEL_IDS:
        return RemoteBGEM3DenseLocalSparseEmbedding(
            model=info.model_id,
            base_url=info.base_url,
            api_key=info.api_key,
            dimension=info.dimension,
            batch_size=info.batch_size,
        )

    return OtherEmbedding(
        model=info.model_id,
        base_url=info.base_url,
        api_key=info.api_key,
        dimension=info.dimension,
        batch_size=info.batch_size,
    )


async def test_embedding_model_status_by_spec(spec: str) -> dict:
    try:
        model = select_embedding_model(spec)
        success, message = await model.test_connection()
        return {
            "spec": spec,
            "status": "available" if success else "unavailable",
            "message": "连接正常" if success else message,
        }
    except Exception as e:
        logger.warning(f"测试 Embedding 模型状态失败 {spec}: {e}")
        return {"spec": spec, "status": "error", "message": str(e)}
