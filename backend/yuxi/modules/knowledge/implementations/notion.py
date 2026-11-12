"""连接 Notion Data Source 的只读知识库：检索页面并渲染正文摘要。"""

import asyncio
import os
import traceback
from typing import Any

import httpx

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.implementations.read_only_connectors import ReadOnlyConnectors
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_DEFAULT_VERSION = "2026-03-11"
NOTION_RETRY_STATUSES = {429, 500, 502, 503, 504}
NOTION_HTTP_TIMEOUT_SECONDS = 45.0
NOTION_MAX_BLOCKS = 2000
NOTION_MAX_DEPTH = 8
NOTION_DEFAULT_MAX_HYDRATE_PAGES = 20


class NotionAPIError(RuntimeError):
    """Notion API 请求失败（HTTP 错误或重试耗尽）。"""


class _NotionClient:
    """Notion REST 客户端：认证头、限流/瞬时错误重试与游标分页。"""

    def __init__(self, token: str, notion_version: str) -> None:
        self.token = token
        self.notion_version = notion_version or NOTION_DEFAULT_VERSION
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=NOTION_HTTP_TIMEOUT_SECONDS)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
        self._client = None
        return False

    # ==== 端点方法：请求/响应字段名是 Notion API 契约 ====

    async def search_pages(self, query_text: str, limit: int) -> list[dict[str, Any]]:
        """按最后编辑时间倒序全工作区搜索页面。"""
        return await self.paginate(
            "POST",
            "/search",
            body={
                "query": query_text,
                "filter": {"property": "object", "value": "page"},
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": min(max(limit, 1), 100),
            },
            limit=limit,
        )

    async def query_data_source(self, data_source_id: str, limit: int) -> list[dict[str, Any]]:
        """按最后编辑时间倒序查询 Data Source 内页面。"""
        return await self.paginate(
            "POST",
            f"/data_sources/{data_source_id}/query",
            body={
                "page_size": min(max(limit, 1), 100),
                "result_type": "page",
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            },
            limit=limit,
        )

    async def retrieve_block_children(self, block_id: str, limit: int) -> list[dict[str, Any]]:
        """读取块的直接子块，跟随游标翻页。"""
        return await self.paginate(
            "GET",
            f"/blocks/{block_id}/children",
            params={"page_size": min(max(limit, 1), 100)},
            limit=limit,
        )

    # ==== 分页与请求内核 ====

    async def paginate(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """跟随 next_cursor 游标翻页并累计 results；limit 截断总条数。

        Notion API 契约：GET 请求的游标放查询参数，POST 请求放请求体。
        """
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        method_upper = method.upper()

        while True:
            request_body = dict(body or {})
            request_params = dict(params or {})
            if cursor:
                if method_upper == "GET":
                    request_params["start_cursor"] = cursor
                else:
                    request_body["start_cursor"] = cursor

            payload = await self.request(method_upper, path, json=request_body or None, params=request_params or None)
            page_results = payload.get("results", [])
            if isinstance(page_results, list):
                results.extend(item for item in page_results if isinstance(item, dict))

            if limit is not None and len(results) >= limit:
                return results[:limit]
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                return results

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 4,
    ) -> dict[str, Any]:
        """发送单个请求；429/5xx 与网络错误按退避重试，其余错误转 NotionAPIError。"""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
            "User-Agent": "yuxi-notion-kb/1.0",
        }
        url = f"{NOTION_API_BASE}{path}"

        for attempt in range(retries + 1):
            try:
                response = await self._request(method, url, json=json, params=params, headers=headers)
            except httpx.HTTPError:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))
                continue

            # 限流/服务端错误在最后一次尝试前按 Retry-After 或指数退避重试
            if response.status_code in NOTION_RETRY_STATUSES and attempt < retries:
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body_preview = response.text[:1000] if response.text else ""
                logger.error(f"Notion HTTP error: status={response.status_code}, path={path}, body={body_preview}")
                raise NotionAPIError(str(exc)) from exc
            return response.json()

        raise NotionAPIError(f"Notion request failed: {method} {path}")

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """上下文管理器内复用连接，否则退回一次性客户端。"""
        if self._client:
            return await self._client.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=NOTION_HTTP_TIMEOUT_SECONDS) as client:
            return await client.request(method, url, **kwargs)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """优先遵循 Retry-After 响应头，无法解析时按指数退避封顶 8 秒。"""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return float(min(2**attempt, 8))


class NotionKB(ReadOnlyConnectors):
    """连接 Notion Data Source 的只读知识库实现"""

    kb_type = "notion"
    name = "Notion"
    description = "连接 Notion Data Source 的只读知识库，支持检索"

    # ==== 类型配置契约 ====

    @classmethod
    def get_create_params_config(cls) -> dict[str, Any]:
        """声明创建参数：data_source_id 必填，token 可留空回退环境变量。"""
        return {
            "options": [
                {
                    "key": "notion_token",
                    "label": "Notion Token",
                    "type": "password",
                    "required": False,
                    "placeholder": "留空则使用 NOTION_TOKEN / NOTION_API_KEY",
                    "description": "Notion integration token，需要 read content 权限",
                },
                {
                    "key": "notion_data_source_id",
                    "label": "Data Source ID",
                    "type": "text",
                    "required": True,
                    "placeholder": "请输入 Notion data_source_id",
                },
                {
                    "key": "notion_version",
                    "label": "Notion API Version",
                    "type": "text",
                    "required": False,
                    "default": NOTION_DEFAULT_VERSION,
                    "placeholder": NOTION_DEFAULT_VERSION,
                },
            ]
        }

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        """校验并规范化连接参数；token 缺失时要求环境变量存在。"""
        params = dict(additional_params or {})
        token = str(params.get("notion_token") or "").strip()
        data_source_id = str(params.get("notion_data_source_id") or "").strip()
        notion_version = str(params.get("notion_version") or NOTION_DEFAULT_VERSION).strip() or NOTION_DEFAULT_VERSION

        if not data_source_id:
            raise ValueError("Notion 参数缺失: notion_data_source_id")
        if not token and not (os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")):
            raise ValueError("Notion 参数缺失: notion_token 或环境变量 NOTION_TOKEN/NOTION_API_KEY")

        return {
            "notion_token": token,
            "notion_data_source_id": data_source_id,
            "notion_version": notion_version,
        }

    # ==== 检索 ====

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        **kwargs,
    ) -> list[dict]:
        """混合 Notion 搜索与 Data Source 扫描获取候选，本地重排后读取全文并返回片段。"""
        try:
            token, data_source_id, notion_version = self._get_connection_config(kb_id, config.additional_params)
            # 默认值与上下限和 get_query_params_config 保持一致
            merged = {**config.query_options, **kwargs}
            final_top_k = min(max(int(merged.get("final_top_k", 10)), 1), 50)
            max_scan_pages = min(max(int(merged.get("max_scan_pages", 100)), 10), 1000)
            max_hydrate_pages = min(
                max(int(merged.get("max_hydrate_pages", NOTION_DEFAULT_MAX_HYDRATE_PAGES)), final_top_k),
                100,
            )
            snippet_window_lines = min(max(int(merged.get("snippet_window_lines", 12)), 3), 40)
            search_mode = str(merged.get("search_mode", "hybrid")).lower()
            if search_mode not in {"notion_search", "data_source_scan", "hybrid"}:
                search_mode = "hybrid"

            async with _NotionClient(token, notion_version) as client:
                candidate_pages = await self._search_candidate_pages(
                    client,
                    query_text,
                    data_source_id,
                    search_mode=search_mode,
                    final_top_k=final_top_k,
                    max_scan_pages=max_scan_pages,
                )
                candidate_pages = self._rank_candidate_pages(candidate_pages, query_text)[:max_hydrate_pages]
                result_tasks = [
                    self._build_search_result(
                        client,
                        page,
                        query_text,
                        rank=rank,
                        source=source,
                        snippet_window_lines=snippet_window_lines,
                    )
                    for rank, (page, source) in enumerate(candidate_pages, start=1)
                    if page.get("id")
                ]
                results = [result for result in await asyncio.gather(*result_tasks) if result]

            return sorted(results, key=lambda item: item.get("score", 0.0), reverse=True)[:final_top_k]
        except (NotionAPIError, httpx.HTTPError, ValueError) as exc:
            logger.error(f"Notion query failed for kb_id={kb_id}: {exc}, {traceback.format_exc()}")
            raise RuntimeError(f"Notion query failed for kb_id={kb_id}") from exc

    @classmethod
    def get_query_params_config(cls) -> dict:
        """声明检索参数；默认值与 aquery 的兜底取值保持一致。"""
        return {
            "type": "notion",
            "options": [
                {
                    "key": "search_mode",
                    "label": "检索模式",
                    "type": "select",
                    "default": "hybrid",
                    "options": [
                        {
                            "value": "hybrid",
                            "label": "混合检索",
                            "description": "先 Notion 搜索，不足时扫描 Data Source",
                        },
                        {"value": "notion_search", "label": "Notion 搜索", "description": "使用 Notion search API"},
                        {
                            "value": "data_source_scan",
                            "label": "Data Source 扫描",
                            "description": "扫描 Data Source 并本地匹配",
                        },
                    ],
                    "description": "选择 Notion 检索方式",
                },
                {
                    "key": "final_top_k",
                    "label": "最终返回数量",
                    "type": "number",
                    "default": 10,
                    "min": 1,
                    "max": 50,
                    "description": "返回给前端和智能体的页面数量",
                },
                {
                    "key": "max_scan_pages",
                    "label": "最大扫描页面数",
                    "type": "number",
                    "default": 100,
                    "min": 10,
                    "max": 1000,
                    "description": "Data Source 扫描模式最多读取的页面数量",
                },
                {
                    "key": "max_hydrate_pages",
                    "label": "最大读取全文页数",
                    "type": "number",
                    "default": NOTION_DEFAULT_MAX_HYDRATE_PAGES,
                    "min": 1,
                    "max": 100,
                    "description": "候选排序后最多读取 block 全文的页面数量",
                },
                {
                    "key": "snippet_window_lines",
                    "label": "片段窗口行数",
                    "type": "number",
                    "default": 12,
                    "min": 3,
                    "max": 40,
                    "description": "搜索结果片段包含的上下文行数",
                },
            ],
        }

    # ==== 内部辅助：连接配置 ====

    @staticmethod
    def _get_connection_config(kb_id: str, additional_params: dict[str, Any]) -> tuple[str, str, str]:
        """读取 (token, data_source_id, notion_version)；token 可回退环境变量，缺失抛 ValueError。"""
        token = str(additional_params.get("notion_token") or os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY") or "").strip()
        data_source_id = str(additional_params.get("notion_data_source_id") or "").strip()
        notion_version = str(additional_params.get("notion_version") or NOTION_DEFAULT_VERSION).strip() or NOTION_DEFAULT_VERSION
        if not token or not data_source_id:
            raise ValueError(f"Notion config incomplete for kb_id={kb_id}")
        return token, data_source_id, notion_version

    # ==== 内部辅助：候选页搜索与本地重排 ====

    async def _search_candidate_pages(
        self,
        client: _NotionClient,
        query_text: str,
        data_source_id: str,
        *,
        search_mode: str,
        final_top_k: int,
        max_scan_pages: int,
    ) -> list[tuple[dict[str, Any], str]]:
        """按检索模式收集候选页，按页面 ID 去重并记录来源。"""
        candidates: list[tuple[dict[str, Any], str]] = []
        seen: set[str] = set()

        def add_page(page: dict[str, Any], source: str) -> None:
            page_id = str(page.get("id") or "")
            if page_id and page_id not in seen:
                seen.add(page_id)
                candidates.append((page, source))

        # Notion 搜索覆盖全工作区，结果可能来自其他 Data Source，需逐页复核归属
        if search_mode in {"notion_search", "hybrid"}:
            search_limit = min(max(final_top_k * 5, 20), 100)
            for page in await client.search_pages(query_text, search_limit):
                if self._page_belongs_to_data_source(page, data_source_id):
                    add_page(page, "notion_search")

        # query_data_source 的结果天然属于该 Data Source，无需再复核归属
        if search_mode in {"data_source_scan", "hybrid"} and len(candidates) < final_top_k:
            for page in await client.query_data_source(data_source_id, max_scan_pages):
                add_page(page, "data_source_scan")

        return candidates

    @classmethod
    def _rank_candidate_pages(
        cls,
        candidate_pages: list[tuple[dict[str, Any], str]],
        query_text: str,
    ) -> list[tuple[dict[str, Any], str]]:
        """本地重排候选：notion_search 来源与标题/属性命中加权。"""
        terms = cls._query_terms(query_text)

        def score_candidate(candidate: tuple[dict[str, Any], str]) -> float:
            page, source = candidate
            title = cls._extract_page_title(page).lower()
            properties = "\n".join(cls._extract_property_texts(page).values()).lower()
            score = 2.0 if source == "notion_search" else 0.0
            score += sum(5.0 for term in terms if term in title)
            score += sum(2.0 for term in terms if term in properties)
            return score

        return sorted(candidate_pages, key=score_candidate, reverse=True)

    async def _build_search_result(
        self,
        client: _NotionClient,
        page: dict[str, Any],
        query_text: str,
        *,
        rank: int,
        source: str,
        snippet_window_lines: int,
    ) -> dict[str, Any] | None:
        """读取页面全文并组装单条检索结果；本地无命中且来源非 notion_search 时丢弃。"""
        page_id = str(page.get("id") or "")
        if not page_id:
            return None

        markdown = await self._page_to_markdown(client, page_id, page)
        score, snippet, match_source, line_start, line_end = self._score_page(
            query_text,
            markdown["title"],
            markdown["properties"],
            markdown["content"],
            snippet_window_lines=snippet_window_lines,
        )
        if score <= 0 and source != "notion_search":
            return None
        # Notion 服务端命中但正文未复现关键词：保留候选，给保底分与开头预览
        if score <= 0:
            score = 0.1
            snippet = self._preview(markdown["content"], snippet_window_lines)
            match_source = "notion_search"
            line_start = 1
            line_end = min(len(markdown["content"].splitlines()), snippet_window_lines)

        return {
            "content": snippet,
            "score": score + 1.0 / (rank + 1000),
            "metadata": {
                "source": markdown["title"],
                "file_id": page_id,
                "chunk_id": f"{page_id}:{line_start}",
                "chunk_index": rank,
                "notion_url": page.get("url"),
                "created_time": page.get("created_time"),
                "last_edited_time": page.get("last_edited_time"),
                "line_start": line_start,
                "line_end": line_end,
                "match_source": match_source,
            },
        }

    # ==== 内部辅助：页面与块渲染为 Markdown ====

    async def _page_to_markdown(
        self,
        client: _NotionClient,
        page_id: str,
        page: dict[str, Any],
    ) -> dict[str, Any]:
        """渲染页面标题、属性列表与正文块，返回 title/properties/content。"""
        title = self._extract_page_title(page)
        properties = self._extract_property_texts(page)
        lines = [f"# {title}", ""]
        for name, value in properties.items():
            if value and name != title:
                lines.append(f"- {name}: {value}")
        if len(lines) > 2:
            lines.append("")

        # state 在递归间共享：块数或深度超限时标记截断并停止拉取
        block_state = {"count": 0, "truncated": False}
        lines.extend(await self._blocks_to_markdown(client, page_id, depth=0, state=block_state))
        if block_state["truncated"]:
            lines.append("")
            lines.append("[内容过长，已截断]")

        return {"title": title, "properties": properties, "content": "\n".join(lines).strip()}

    async def _blocks_to_markdown(
        self,
        client: _NotionClient,
        block_id: str,
        *,
        depth: int,
        state: dict[str, Any],
    ) -> list[str]:
        """深度优先递归渲染子块；深度与总块数超限时置 truncated 并停止。"""
        if depth > NOTION_MAX_DEPTH or state["count"] >= NOTION_MAX_BLOCKS:
            state["truncated"] = True
            return []

        # 只拉取剩余额度内的块，请求不越过全局上限
        remaining = NOTION_MAX_BLOCKS - state["count"]
        blocks = await client.retrieve_block_children(block_id, remaining)
        lines: list[str] = []
        for block in blocks:
            if state["count"] >= NOTION_MAX_BLOCKS:
                state["truncated"] = True
                break
            state["count"] += 1
            lines.extend(self._block_to_lines(block, depth))
            if block.get("has_children"):
                lines.extend(await self._blocks_to_markdown(client, block["id"], depth=depth + 1, state=state))
        return lines

    def _block_to_lines(self, block: dict[str, Any], depth: int) -> list[str]:
        """把单个块按类型映射为 Markdown 行；未知类型输出占位标记。"""
        block_type = str(block.get("type") or "")
        value = block.get(block_type) if isinstance(block.get(block_type), dict) else {}
        text = self._rich_text_plain_text(value.get("rich_text") or [])
        indent = "  " * depth

        if block_type == "paragraph":
            return [f"{indent}{text}".rstrip()] if text else []
        if block_type in {"heading_1", "heading_2", "heading_3"}:
            level = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[block_type]
            return [f"{level} {text}".rstrip()] if text else []
        if block_type == "bulleted_list_item":
            return [f"{indent}- {text}".rstrip()] if text else []
        if block_type == "numbered_list_item":
            return [f"{indent}1. {text}".rstrip()] if text else []
        if block_type == "to_do":
            marker = "x" if value.get("checked") else " "
            return [f"{indent}- [{marker}] {text}".rstrip()] if text else []
        if block_type in {"toggle", "quote", "callout"}:
            return [f"{indent}> {text}".rstrip()] if text else []
        if block_type == "code":
            language = value.get("language") or ""
            return [f"```{language}", text, "```"] if text else []
        if block_type == "child_page":
            title = value.get("title") or "Untitled"
            return [f"{indent}## {title}"]
        if block_type == "table_row":
            cells = [self._rich_text_plain_text(cell) for cell in value.get("cells") or []]
            return [" | ".join(cells)] if cells else []
        if block_type in {"bookmark", "embed", "image", "file", "pdf", "video"}:
            url = self._file_or_url(value)
            caption = self._rich_text_plain_text(value.get("caption") or [])
            label = caption or url
            return [f"{indent}[{block_type}] {label}".rstrip()] if label else []
        if text:
            return [f"{indent}{text}".rstrip()]
        return [f"{indent}[unsupported block: {block_type}]"] if block_type else []

    @staticmethod
    def _file_or_url(value: dict[str, Any]) -> str:
        """提取 bookmark/embed/附件类块的展示 URL。"""
        if value.get("url"):
            return str(value["url"])
        for key in ("external", "file"):
            nested = value.get(key)
            if isinstance(nested, dict) and nested.get("url"):
                return str(nested["url"])
        return ""

    # ==== 内部辅助：属性与富文本提取 ====

    @classmethod
    def _extract_page_title(cls, page: dict[str, Any]) -> str:
        """取第一个 title 属性的纯文本，缺省 Untitled。"""
        for prop in (page.get("properties") or {}).values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = cls._rich_text_plain_text(prop.get("title") or [])
                if title:
                    return title
        return "Untitled"

    @classmethod
    def _extract_property_texts(cls, page: dict[str, Any]) -> dict[str, str]:
        """把页面属性映射为 {属性名: 纯文本}，跳过空值。"""
        properties: dict[str, str] = {}
        for name, prop in (page.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            text = cls._property_to_text(prop)
            if text:
                properties[str(name)] = text
        return properties

    @classmethod
    def _property_to_text(cls, prop: dict[str, Any]) -> str:
        """按属性类型提取纯文本；formula 按其结果类型递归提取。"""
        prop_type = prop.get("type")
        if prop_type in {"title", "rich_text"}:
            return cls._rich_text_plain_text(prop.get(prop_type) or [])
        if prop_type == "select":
            value = prop.get("select") or {}
            return str(value.get("name") or "")
        if prop_type == "multi_select":
            return ", ".join(str(item.get("name") or "") for item in prop.get("multi_select") or [] if item.get("name"))
        if prop_type == "status":
            value = prop.get("status") or {}
            return str(value.get("name") or "")
        if prop_type == "date":
            value = prop.get("date") or {}
            return " - ".join(str(value.get(key) or "") for key in ("start", "end") if value.get(key))
        if prop_type in {"url", "email", "phone_number", "number", "checkbox", "created_time", "last_edited_time"}:
            value = prop.get(prop_type)
            return "" if value is None else str(value)
        if prop_type == "people":
            return ", ".join(str(item.get("name") or item.get("id") or "") for item in prop.get("people") or [])
        if prop_type == "files":
            return ", ".join(str(item.get("name") or "") for item in prop.get("files") or [] if item.get("name"))
        if prop_type == "formula":
            formula = prop.get("formula") or {}
            return cls._property_to_text({"type": formula.get("type"), formula.get("type"): formula.get(formula.get("type"))})
        return ""

    @staticmethod
    def _rich_text_plain_text(items: list[dict[str, Any]]) -> str:
        """拼接 rich_text 数组的纯文本，缺 plain_text 时回退 text.content。"""
        return "".join(str(item.get("plain_text") or item.get("text", {}).get("content") or "") for item in items)

    # ==== 内部辅助：Data Source 归属判断 ====

    @classmethod
    def _page_belongs_to_data_source(
        cls,
        page: dict[str, Any],
        data_source_id: str,
    ) -> bool:
        """判断页面是否属于配置的 Data Source；兼容旧版 database_id 父节点。"""
        parent = page.get("parent") or {}
        normalized_data_source_id = cls._normalize_notion_id(data_source_id)

        # 新版 API 父节点携带 data_source_id；一旦存在即只按它判断，不再回退 database_id
        parent_data_source_id = parent.get("data_source_id")
        if parent_data_source_id:
            return cls._normalize_notion_id(str(parent_data_source_id)) == normalized_data_source_id

        # 旧版 API 父节点为 database_id，允许配置值直接填 database_id
        database_id = parent.get("database_id")
        if database_id:
            return cls._normalize_notion_id(str(database_id)) == normalized_data_source_id
        return False

    @staticmethod
    def _normalize_notion_id(value: str) -> str:
        """去掉连字符并小写，用于 Notion ID 等价比较。"""
        return value.replace("-", "").lower()

    # ==== 内部辅助：本地相关度评分 ====

    @classmethod
    def _score_page(
        cls,
        query_text: str,
        title: str,
        properties: dict[str, str],
        content: str,
        *,
        snippet_window_lines: int,
    ) -> tuple[float, str, str, int, int]:
        """本地相关度评分：标题、属性与逐行命中累计分数，并截取首个命中处的上下文窗口。

        返回 (分数, 片段, 命中来源, 起始行, 结束行)；无正文命中时片段为空。
        """
        terms = cls._query_terms(query_text)
        title_lower = title.lower()
        property_text = "\n".join(properties.values())
        property_lower = property_text.lower()
        lines = content.splitlines()
        score = 0.0
        match_source = "content"

        if any(term in title_lower for term in terms):
            score += 5.0
            match_source = "title"
        if any(term in property_lower for term in terms):
            score += 3.0
            if match_source == "content":
                match_source = "properties"

        matched_indexes = []
        for index, line in enumerate(lines):
            lower_line = line.lower()
            matches = sum(1 for term in terms if term in lower_line)
            if matches:
                matched_indexes.append(index)
                score += float(matches)

        if not matched_indexes:
            return score, "", match_source, 1, 0

        first_match = matched_indexes[0]
        half_window = snippet_window_lines // 2
        start = max(first_match - half_window, 0)
        end = min(start + snippet_window_lines, len(lines))
        # 窗口右缘触底时整体左移，保证窗口仍是完整 snippet_window_lines 行
        start = max(end - snippet_window_lines, 0)
        snippet = "\n".join(lines[start:end])
        return score, snippet, match_source, start + 1, end

    @staticmethod
    def _query_terms(query_text: str) -> list[str]:
        """整条查询与分词小写去重（保序），整条优先。"""
        full_query = query_text.strip().lower()
        terms = [full_query] if full_query else []
        terms.extend(part.lower() for part in full_query.split() if part)
        return list(dict.fromkeys(terms))

    @staticmethod
    def _preview(content: str, line_count: int) -> str:
        """取正文开头若干行作为兜底片段。"""
        return "\n".join(content.splitlines()[:line_count])
