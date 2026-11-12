import traceback
from typing import Any

import httpx

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.implementations.read_only_connectors import ReadOnlyConnectors
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig

DIFY_REQUIRED_PARAMS = ("dify_api_url", "dify_token", "dify_dataset_id")
DIFY_DEFAULT_TOP_K = 10
DIFY_MAX_TOP_K = 100


class DifyKB(ReadOnlyConnectors):
    """基于 Dify Dataset Retrieve API 的只读检索知识库实现"""

    kb_type = "dify"
    name = "Dify"
    description = "连接 Dify Dataset 的只读检索知识库"

    def __init__(self, work_dir: str, **kwargs):
        del kwargs
        super().__init__(work_dir)

    @classmethod
    def get_create_params_config(cls) -> dict[str, Any]:
        """声明创建知识库时必填的连接参数。"""
        return {
            "options": [
                {
                    "key": "dify_api_url",
                    "label": "Dify API URL",
                    "type": "text",
                    "required": True,
                    "placeholder": "例如: https://api.dify.ai/v1",
                    "description": "Dify API 地址，必须以 /v1 结尾",
                },
                {
                    "key": "dify_token",
                    "label": "Dify Token",
                    "type": "password",
                    "required": True,
                    "placeholder": "请输入 Dify API Token",
                },
                {
                    "key": "dify_dataset_id",
                    "label": "Dataset ID",
                    "type": "text",
                    "required": True,
                    "placeholder": "请输入 Dify dataset_id",
                },
            ]
        }

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        """校验连接参数完整性并规范化为去除首尾空白后的取值。"""
        params = dict(additional_params or {})
        missing_fields = [field for field in DIFY_REQUIRED_PARAMS if not str(params.get(field) or "").strip()]
        if missing_fields:
            raise ValueError(f"Dify 参数缺失: {', '.join(missing_fields)}")

        for field in DIFY_REQUIRED_PARAMS:
            params[field] = str(params.get(field) or "").strip()
        if not params["dify_api_url"].endswith("/v1"):
            raise ValueError("Dify api_url 必须以 /v1 结尾")
        return params

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        **kwargs,
    ) -> list[dict]:
        """调用 Dify 检索 API 并映射结果；失败时降级为仅 query 请求重试一次。"""
        api_url, token, dataset_id = self._get_connection_config(kb_id, config.additional_params)

        merged = {**config.query_options, **kwargs}
        search_mode = str(merged.get("search_mode", "vector")).lower()
        search_method_map = {
            "vector": "semantic_search",
            "keyword": "keyword_search",
            "hybrid": "hybrid_search",
        }
        search_method = search_method_map.get(search_mode, "semantic_search")

        top_k = min(max(int(merged.get("final_top_k", DIFY_DEFAULT_TOP_K)), 1), DIFY_MAX_TOP_K)
        score_threshold_enabled = bool(merged.get("score_threshold_enabled", False))
        score_threshold = float(merged.get("similarity_threshold", 0.0))

        payload: dict[str, Any] = {
            "query": query_text,
            "retrieval_model": {
                "search_method": search_method,
                "top_k": top_k,
                # 某些 Dify 部署版本会直接读取该字段，缺失时抛 KeyError
                "reranking_enable": False,
                "score_threshold_enabled": score_threshold_enabled,
            },
        }
        if score_threshold_enabled:
            payload["retrieval_model"]["score_threshold"] = score_threshold

        request_url = f"{api_url.rstrip('/')}/datasets/{dataset_id}/retrieve"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response_json = await self._request_dify(client_payload=payload, request_url=request_url, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Dify query failed for kb_id={kb_id}: {e}, {traceback.format_exc()}")
            # 一些 Dify 部署版本对 retrieval_model 兼容性较差，失败时降级为仅 query 请求重试一次
            try:
                response_json = await self._request_dify(
                    client_payload={"query": query_text},
                    request_url=request_url,
                    headers=headers,
                )
                logger.warning(f"Dify query fallback to query-only succeeded for kb_id={kb_id}")
            except Exception as fallback_error:  # noqa: BLE001
                logger.error(f"Dify query fallback failed for kb_id={kb_id}: {fallback_error}, {traceback.format_exc()}")
                raise RuntimeError(f"Dify query failed for kb_id={kb_id}") from fallback_error

        return self._map_records(response_json)[:top_k]

    @classmethod
    def get_query_params_config(cls) -> dict:
        """声明检索参数配置，键与 Dify retrieval_model 字段一一映射。"""
        options = [
            {
                "key": "search_mode",
                "label": "检索模式",
                "type": "select",
                "default": "vector",
                "options": [
                    {"value": "vector", "label": "向量检索", "description": "映射为 semantic_search"},
                    {"value": "keyword", "label": "关键词检索", "description": "映射为 keyword_search"},
                    {"value": "hybrid", "label": "混合检索", "description": "映射为 hybrid_search"},
                ],
                "description": "Dify 检索方法映射",
            },
            {
                "key": "final_top_k",
                "label": "最终返回 Chunk 数",
                "type": "number",
                "default": DIFY_DEFAULT_TOP_K,
                "min": 1,
                "max": DIFY_MAX_TOP_K,
                "description": "映射为 Dify retrieval_model.top_k",
            },
            {
                "key": "score_threshold_enabled",
                "label": "启用分数阈值",
                "type": "boolean",
                "default": False,
                "description": "映射为 Dify retrieval_model.score_threshold_enabled",
            },
            {
                "key": "similarity_threshold",
                "label": "分数阈值（0-1）",
                "type": "number",
                "default": 0.0,
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "description": "映射为 Dify retrieval_model.score_threshold",
            },
        ]
        return {"type": "dify", "options": options}

    # ==== 内部辅助：连接配置、HTTP 请求与响应映射 ====

    @staticmethod
    def _get_connection_config(kb_id: str, additional_params: dict[str, Any]) -> tuple[str, str, str]:
        """读取并复核持久化连接配置，不完整时在请求远端前显式失败。"""
        api_url = str(additional_params.get("dify_api_url") or "").strip()
        token = str(additional_params.get("dify_token") or "").strip()
        dataset_id = str(additional_params.get("dify_dataset_id") or "").strip()
        if not api_url or not token or not dataset_id:
            raise ValueError(f"Dify config incomplete for kb_id={kb_id}")
        return api_url, token, dataset_id

    async def _request_dify(self, client_payload: dict[str, Any], request_url: str, headers: dict[str, str]) -> dict:
        """发起 Dify HTTP 请求；非 2xx 时记录诊断上下文后原样抛出。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(request_url, json=client_payload, headers=headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                body_preview = response.text[:1000] if response.text else ""
                logger.error(
                    f"Dify HTTP error: status={response.status_code}, url={request_url}, "
                    f"payload_keys={list(client_payload.keys())}, body={body_preview}"
                )
                raise
            return response.json()

    @staticmethod
    def _map_records(response_json: Any) -> list[dict]:
        """把 Dify records 响应映射为统一 chunk 结构，结构异常的记录跳过。"""
        records = response_json.get("records", []) if isinstance(response_json, dict) else []
        if not isinstance(records, list):
            return []

        results = []
        for record in records:
            if not isinstance(record, dict):
                continue
            segment = record.get("segment")
            if not isinstance(segment, dict):
                continue
            content = segment.get("content")
            if not content:
                continue

            document = segment.get("document")
            if not isinstance(document, dict):
                document = {}
            results.append(
                {
                    "content": content,
                    "score": float(record.get("score") or 0.0),
                    "metadata": {
                        "source": document.get("name") or "Dify",
                        "file_id": document.get("id"),
                        "chunk_id": segment.get("id"),
                        "chunk_index": segment.get("position"),
                    },
                }
            )
        return results
