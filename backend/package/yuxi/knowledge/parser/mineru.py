"""
MinerU 文档解析器

使用 MinerU 服务进行文档版面分析和内容提取
"""

import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from yuxi.knowledge.parser.base import BaseDocumentProcessor, DocumentParserException
from yuxi.knowledge.parser.capabilities import get_parser_capability, is_mineru_official_url
from yuxi.knowledge.parser.zip_utils import process_zip_file_sync
from yuxi.utils import logger

_CAPABILITY = get_parser_capability("mineru_ocr")


class MinerUParser(BaseDocumentProcessor):
    """MinerU 文档解析器 - 使用 HTTP API 进行文档理解和解析"""

    service_name = _CAPABILITY.service_name
    display_name = _CAPABILITY.display_name
    supported_extensions = list(_CAPABILITY.supported_extensions)

    def __init__(self, server_url: str | None = None, api_key: str | None = None):
        self.server_url = (server_url or os.getenv("MINERU_API_URI") or "http://localhost:30001").rstrip("/")
        self.official_parser = None
        if is_mineru_official_url(self.server_url):
            from yuxi.knowledge.parser.mineru_official import MinerUOfficialParser

            self.official_parser = MinerUOfficialParser(
                api_key=api_key,
                api_base=self._normalize_official_api_base(self.server_url),
            )

        self.parse_endpoint = f"{self.server_url}/file_parse"
        self.task_endpoint = f"{self.server_url}/tasks"

    @staticmethod
    def _normalize_official_api_base(server_url: str) -> str:
        """将官方云端的任务路径归一化为 API 根地址。"""

        parts = urlsplit(server_url)
        path = parts.path.rstrip("/")
        for suffix in ("/extract/task/tasks", "/extract/task", "/tasks"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))

    def check_health(self) -> dict:
        """检查 MinerU 服务健康状态"""
        if self.official_parser is not None:
            result = self.official_parser.check_health()
            result["details"] = {
                **result.get("details", {}),
                "configured_engine": self.service_name,
                "recommended_engine": "mineru_official",
            }
            return result

        try:
            # 尝试访问 OpenAPI JSON 端点来检查服务是否可用
            health_url = f"{self.server_url}/openapi.json"
            response = requests.get(health_url, timeout=5)

            if response.status_code == 200:
                try:
                    openapi_data = response.json()
                    # 只把 POST /file_parse 视为可用；有些 MinerU 变体会暴露同名路径但不接受该方法。
                    paths = openapi_data.get("paths", {})
                    file_parse_methods = paths.get("/file_parse", {})
                    task_methods = paths.get("/tasks", {})
                    has_file_parse = "post" in file_parse_methods
                    has_task_api = "post" in task_methods

                    if has_file_parse:
                        return {
                            "status": "healthy",
                            "message": "MinerU 服务运行正常",
                            "details": {
                                "server_url": self.server_url,
                                "api_version": openapi_data.get("info", {}).get("version", "unknown"),
                            },
                        }
                    if has_task_api:
                        return {
                            "status": "healthy",
                            "message": "MinerU 服务未暴露同步接口，但可通过任务接口解析",
                            "details": {
                                "server_url": self.server_url,
                                "api_version": openapi_data.get("info", {}).get("version", "unknown"),
                                "mode": "task_fallback",
                                "allowed_methods": {
                                    "file_parse": sorted(file_parse_methods.keys()),
                                    "tasks": sorted(task_methods.keys()),
                                },
                            },
                        }
                    else:
                        return {
                            "status": "unhealthy",
                            "message": "MinerU 服务缺少必要的 POST /file_parse 或 POST /tasks 端点",
                            "details": {
                                "server_url": self.server_url,
                                "allowed_methods": {
                                    "file_parse": sorted(file_parse_methods.keys()),
                                    "tasks": sorted(task_methods.keys()),
                                },
                            },
                        }
                except Exception as e:
                    return {
                        "status": "unhealthy",
                        "message": f"MinerU 响应格式错误: {str(e)}",
                        "details": {"server_url": self.server_url},
                    }
            else:
                return {
                    "status": "unhealthy",
                    "message": f"MinerU 服务响应异常: {response.status_code}",
                    "details": {"server_url": self.server_url},
                }

        except requests.exceptions.ConnectionError:
            return {
                "status": "unavailable",
                "message": "MinerU 服务无法连接,请检查服务是否启动",
                "details": {"server_url": self.server_url},
            }
        except requests.exceptions.Timeout:
            return {
                "status": "timeout",
                "message": "MinerU 服务连接超时",
                "details": {"server_url": self.server_url},
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"MinerU 健康检查失败: {str(e)}",
                "details": {"server_url": self.server_url, "error": str(e)},
            }

    def process_file(self, file_path: str, params: dict | None = None) -> str:
        """
        使用 MinerU 处理文档

        Args:
            file_path: 文件路径
            params: 处理参数
                - lang_list: 语言列表 (默认: ["ch"])
                - backend: 后端类型 (默认: "hybrid-auto-engine")
                - parse_method: 解析方法 (默认: "auto")
                - start_page_id: 起始页码 (默认: 0)
                - end_page_id: 结束页码 (默认: 99999)
                - formula_enable: 启用公式解析 (默认: True)
                - table_enable: 启用表格解析 (默认: True)
                - image_analysis: 启用图像/图表解析 (默认: True)
                - server_url: OpenAI 兼容服务地址 (*-http-client 后端时可选)

        Returns:
            str: 提取的 Markdown 文本
        """
        if not os.path.exists(file_path):
            raise DocumentParserException(f"文件不存在: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(
                f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type"
            )

        # 解析参数
        params = params or {}
        if self.official_parser is not None:
            logger.warning("mineru_ocr 配置使用 MinerU 官方地址，将兼容转发到 mineru_official；建议迁移到独立官方引擎")
            return self.official_parser.process_file(file_path, self._build_official_params(params))

        data = {
            "lang_list": params.get("lang_list", ["ch"]),
            "backend": params.get("backend", "hybrid-auto-engine"),
            "parse_method": params.get("parse_method", "auto"),
            "formula_enable": params.get("formula_enable", True),
            "table_enable": params.get("table_enable", True),
            "image_analysis": params.get("image_analysis", True),
            "start_page_id": params.get("start_page_id", 0),
            "end_page_id": params.get("end_page_id", 99999),
            "return_md": True,
            "response_format_zip": True,
            "return_images": True,
        }

        server_url = params.get("server_url")
        if server_url:
            data["server_url"] = server_url

        try:
            start_time = time.time()
            timeout_seconds = int(params.get("timeout_seconds") or os.environ.get("MINERU_TIMEOUT", 1800))

            logger.info(
                f"MinerU 开始处理: {os.path.basename(file_path)} (backend={data['backend']}, lang={data['lang_list']})"
            )

            response = self._post_parse_request(file_path, data, timeout_seconds)
            if response.status_code == 405:
                logger.warning(
                    "MinerU 同步接口返回 405，切换到任务接口: {}",
                    self.task_endpoint,
                )
                return self._process_via_task_endpoint(file_path, params, data, timeout_seconds, start_time)

            # 检查响应状态
            logger.debug(
                f"MinerU 响应状态: {response.status_code}, Content-Type: {response.headers.get('content-type')}"
            )

            if response.status_code != 200:
                error_detail = "未知错误"
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", str(error_data))
                except Exception:
                    error_detail = response.text or f"HTTP {response.status_code}"

                logger.error(f"MinerU HTTP错误 {response.status_code}: {error_detail}")
                raise DocumentParserException(
                    f"MinerU 处理失败: {error_detail}",
                    self.get_service_name(),
                    f"http_{response.status_code}",
                )

            return self._extract_markdown_from_zip(response.content, params, start_time, file_path)

        except DocumentParserException:
            raise
        except requests.exceptions.Timeout:
            error_msg = f"MinerU 处理超时 ({time.time() - start_time:.2f}s), 可以配置 MINERU_TIMEOUT 环境变量。"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), "timeout")
        except requests.exceptions.ConnectionError:
            error_msg = "MinerU 连接失败,请检查服务是否运行"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), "connection_error")
        except Exception as e:
            error_msg = f"MinerU 处理失败: {str(e)}"
            logger.error(f"{error_msg} ({time.time() - start_time:.2f}s)")
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed")

    @staticmethod
    def _build_official_params(params: dict) -> dict:
        """将自托管解析参数转换为官方云解析参数。"""

        official_params = dict(params)
        official_params.setdefault("is_ocr", True)
        official_params.setdefault("enable_formula", params.get("formula_enable", True))
        official_params.setdefault("enable_table", params.get("table_enable", True))
        official_params.setdefault("language", (params.get("lang_list") or ["ch"])[0])
        return official_params

    def _post_parse_request(self, file_path: str, data: dict, timeout_seconds: int) -> requests.Response:
        """向同步解析接口发起请求。"""

        with open(file_path, "rb") as f:
            files = {"files": (os.path.basename(file_path), f, "application/octet-stream")}
            return requests.post(self.parse_endpoint, files=files, data=data, timeout=timeout_seconds)

    def _post_task_request(self, file_path: str, data: dict, timeout_seconds: int) -> requests.Response:
        """向任务提交接口发起请求。"""

        with open(file_path, "rb") as f:
            files = {"files": (os.path.basename(file_path), f, "application/octet-stream")}
            return requests.post(self.task_endpoint, files=files, data=data, timeout=min(timeout_seconds, 30))

    def _process_via_task_endpoint(
        self,
        file_path: str,
        params: dict,
        data: dict,
        timeout_seconds: int,
        start_time: float,
    ) -> str:
        """通过任务接口提交并轮询结果。"""

        submit_response = self._post_task_request(file_path, data, timeout_seconds)
        if submit_response.status_code != 200:
            error_detail = self._response_error_detail(submit_response)
            logger.error(f"MinerU 任务提交失败 {submit_response.status_code}: {error_detail}")
            raise DocumentParserException(
                f"MinerU 处理失败: {error_detail}",
                self.get_service_name(),
                f"http_{submit_response.status_code}",
            )

        try:
            submit_data = submit_response.json()
        except Exception as exc:  # noqa: BLE001
            raise DocumentParserException(
                f"MinerU 任务提交响应格式错误: {str(exc)}",
                self.get_service_name(),
                "response_parse_error",
            ) from exc

        task_id = submit_data.get("task_id") or submit_data.get("data", {}).get("task_id")
        if not task_id:
            raise DocumentParserException(
                "MinerU 任务提交未返回 task_id",
                self.get_service_name(),
                "missing_task_id",
            )

        result_url = f"{self.task_endpoint}/{task_id}/result"
        deadline = time.time() + timeout_seconds
        while True:
            result_response = requests.get(result_url, timeout=30)
            if result_response.status_code == 200:
                return self._extract_markdown_from_zip(result_response.content, params, start_time, file_path)
            if result_response.status_code == 202:
                if time.time() >= deadline:
                    raise DocumentParserException(
                        f"MinerU 任务超时 ({time.time() - start_time:.2f}s), 可以配置 MINERU_TIMEOUT 环境变量。",
                        self.get_service_name(),
                        "timeout",
                    )
                time.sleep(3)
                continue

            error_detail = self._response_error_detail(result_response)
            logger.error(f"MinerU 任务结果失败 {result_response.status_code}: {error_detail}")
            raise DocumentParserException(
                f"MinerU 处理失败: {error_detail}",
                self.get_service_name(),
                f"http_{result_response.status_code}",
            )

    def _extract_markdown_from_zip(
        self,
        zip_data: bytes,
        params: dict,
        start_time: float,
        file_path: str,
    ) -> str:
        """从 MinerU 返回的 zip 中提取 Markdown。"""

        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
                tmp_zip.write(zip_data)
                tmp_zip.flush()
                tmp_zip_path = tmp_zip.name

            try:
                from yuxi.storage.minio import get_minio_client

                image_bucket = params.get("image_bucket") or get_minio_client().KB_BUCKETS["images"]
                image_prefix = params.get("image_prefix") or "unknown/kb-images"

                processed = process_zip_file_sync(
                    tmp_zip_path,
                    image_bucket=image_bucket,
                    image_prefix=image_prefix,
                )
                # 旧版测试/第三方适配器可能返回带字段的结果，新版共享处理器直接返回字符串。
                text = processed if isinstance(processed, str) else processed.get("markdown_content", "")
            finally:
                os.unlink(tmp_zip_path)

            if not text:
                logger.error("MinerU 未返回任何文本内容")
                raise DocumentParserException(
                    "MinerU 未返回任何文本内容",
                    self.get_service_name(),
                    "no_content",
                )

            processing_time = time.time() - start_time
            logger.info(f"MinerU 处理成功: {os.path.basename(file_path)} - {len(text)} 字符 ({processing_time:.2f}s)")
            return text
        except DocumentParserException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DocumentParserException(
                f"MinerU 响应解析失败: {str(exc)}",
                self.get_service_name(),
                "response_parse_error",
            ) from exc

    def _response_error_detail(self, response: requests.Response) -> str:
        """提取 MinerU 错误响应的可读信息。"""

        error_detail = "未知错误"
        try:
            error_data = response.json()
            error_detail = error_data.get("detail", str(error_data))
        except Exception:
            error_detail = response.text or f"HTTP {response.status_code}"
        return error_detail
