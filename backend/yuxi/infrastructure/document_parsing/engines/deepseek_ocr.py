"""DeepSeek OCR（SiliconFlow API）解析器。"""

import base64
import io
import os
import re
import time
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import requests

from yuxi.infrastructure.document_parsing import DocumentParserException
from yuxi.infrastructure.document_parsing.engines import DocumentEngine
from yuxi.infrastructure.document_parsing.pdf_utils import PDFIUM_LOCK, render_pdf_page_image
from yuxi.infrastructure.observability.logging import logger


class DeepSeekOCRParser(DocumentEngine):
    """通过 SiliconFlow 的 DeepSeek-OCR 模型解析文档。"""

    engine_id = "deepseek_ocr"

    MIME_TYPE_MAP = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }

    def __init__(self, api_key: str | None = None, api_url: str | None = None):
        """使用配置中心传入的 SiliconFlow 凭证和固定服务端点初始化解析器。"""

        self.api_key = api_key
        if not self.api_key:
            raise DocumentParserException("DeepSeek OCR API Key 未配置", "deepseek_ocr", "missing_api_key")

        self.api_url = api_url or "https://api.siliconflow.cn/v1/chat/completions"
        self.model = "deepseek-ai/DeepSeek-OCR"

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def check_health(self) -> dict[str, Any]:
        """检查 SiliconFlow 模型列表接口是否可用。"""
        try:
            models_url = self.api_url.rsplit("/chat/completions", 1)[0] + "/models"
            response = requests.get(models_url, headers=self.headers, timeout=10)

            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "message": "DeepSeek OCR (SiliconFlow) is available",
                    "details": {"api_url": self.api_url},
                }
            if response.status_code == 401:
                return {"status": "unhealthy", "message": "Invalid API Key", "details": {"error_code": "401"}}
            return {
                "status": "unhealthy",
                "message": f"API Error: {response.status_code}",
                "details": {"status_code": response.status_code},
            }
        except Exception as e:
            return {"status": "unavailable", "message": f"Connection failed: {str(e)}", "details": {"error": str(e)}}

    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """逐页调用 DeepSeek OCR，返回拼接后的 Markdown。"""
        if not os.path.exists(file_path):
            raise DocumentParserException(f"File not found: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(f"Unsupported file type: {file_ext}", self.get_service_name(), "unsupported_file_type")

        try:
            start_time = time.time()
            logger.info(f"DeepSeek OCR starting: {os.path.basename(file_path)}")

            params = params or {}
            if file_ext == ".pdf":
                content = self._process_pdf(file_path, params)
            else:
                content = self._process_image(file_path, params)

            processing_time = time.time() - start_time
            logger.info(f"DeepSeek OCR finished: {os.path.basename(file_path)} - {len(content)} chars ({processing_time:.2f}s)")

            return content

        except DocumentParserException:
            raise
        except Exception as e:
            error_msg = f"DeepSeek OCR failed: {str(e)}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed") from e

    def _process_pdf(self, file_path: str, params: dict[str, Any]) -> str:
        """把 PDF 每页渲染为 PNG 后交给远端 OCR。"""
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(file_path)
        try:
            full_text: list[str] = []

            with PDFIUM_LOCK:
                total_pages = len(pdf)
            logger.info(f"Processing PDF with {total_pages} pages")

            # pypdfium2 的 scale 是 72dpi 的倍数，200dpi 对应 scale=200/72
            scale = int(params.get("pdf_dpi", 200)) / 72

            for i in range(total_pages):
                logger.debug(f"Processing page {i + 1}/{total_pages}")
                with render_pdf_page_image(pdf, i, scale=scale) as image, io.BytesIO() as buffer:
                    image.save(buffer, format="PNG")
                    img_bytes = buffer.getvalue()

                page_text = self._call_api(img_bytes, "image/png", params)
                full_text.append(page_text)

            return "\n\n".join(full_text)
        finally:
            with PDFIUM_LOCK:
                pdf.close()

    def _process_image(self, file_path: str, params: dict[str, Any]) -> str:
        """读取单张图片并调用远端 OCR。"""
        mime_type = self._get_mime_type(file_path)
        with open(file_path, "rb") as stream:
            file_content = stream.read()
        return self._call_api(file_content, mime_type, params)

    def _call_api(self, data_bytes: bytes, mime_type: str, params: dict[str, Any]) -> str:
        """提交 data URL 并清洗模型返回的引用标记。"""
        encoded_string = base64.b64encode(data_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{encoded_string}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "<image>\n<|grounding|>Convert the document to markdown. "},
                ],
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(params.get("max_tokens", 4096)),
            "temperature": float(params.get("temperature", 0.1)),
        }

        response = requests.post(
            self.api_url,
            headers=self.headers,
            json=payload,
            timeout=int(params.get("timeout_seconds", 120)),
        )

        if response.status_code != 200:
            error_msg = f"API Error {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), f"http_{response.status_code}")

        result = response.json()
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise DocumentParserException("OCR 响应未完整结束", self.get_service_name(), "incomplete_result")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise DocumentParserException("OCR 结果正文必须是字符串", self.get_service_name(), "invalid_result")

        content = re.sub(r"<\|ref\|>.*?<\|/ref\|>", "", content)
        content = re.sub(r"<\|det\|>.*?<\|/det\|>", "", content)
        return content.strip()

    def _get_mime_type(self, file_path: str) -> str:
        """按扩展名返回图片 MIME，未知扩展名按 JPEG 处理。"""
        file_ext = Path(file_path).suffix.lower()
        return self.MIME_TYPE_MAP.get(file_ext, "image/jpeg")
