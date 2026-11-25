"""通过可选 BabelDOC Stream 和当前翻译模型生成保版式 PDF。"""

import asyncio
import hmac
import json
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from aiohttp import web
from langchain.chat_models import BaseChatModel
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.models.utils import strip_chat_reasoning_tags
from yuxi.modules.translation.services import LANGUAGES, load_translation_model
from yuxi.shared.errors import ContentTooLargeError, InvalidInputError, OperationTimeoutError, ServiceUnavailableError, UpstreamError

MAX_PDF_UPLOAD_BYTES = 20 * 1024 * 1024
_MAX_RESULT_BYTES = 40 * 1024 * 1024
_TIMEOUT_SECONDS = 240
_LAYOUT_SCHEMA = "babeldoc-stream/v2"
_GATEWAY_MODEL = "selected-translation-model"
_CAPACITY = asyncio.Semaphore(1)


def pdf_translation_available() -> bool:
    """报告此部署是否安装了 PDF 版式执行器。"""
    return _babeldoc_executable() is not None


async def translate_pdf(
    db: AsyncSession, *, uid: str, model_spec: str, source_language: str, target_language: str, content: bytes
) -> bytes:
    """以当前用户授权的模型执行独立 PDF 任务，返回译文文件。"""
    executable = _babeldoc_executable()
    if executable is None:
        raise ServiceUnavailableError("当前部署未安装 BabelDOC Stream，暂不可用版式翻译", code="translation_pdf_unavailable")
    if (source_language != "auto" and source_language not in LANGUAGES) or target_language not in LANGUAGES:
        raise InvalidInputError("版式翻译请选择内置语言", code="translation_pdf_language")
    if not content.startswith(b"%PDF-"):
        raise InvalidInputError("请选择有效的 PDF 文件", code="translation_pdf_invalid")
    if len(content) > MAX_PDF_UPLOAD_BYTES:
        raise ContentTooLargeError("PDF 文件不能超过 20 MiB", code="translation_pdf_too_large")

    model = await load_translation_model(db, model_spec=model_spec, uid=uid)
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS), _CAPACITY:
            with tempfile.TemporaryDirectory(prefix="yuxi-pdf-translation-") as scratch:
                directory = Path(scratch)
                source = directory / "source.pdf"
                await await_io(asyncio.to_thread(source.write_bytes, content))
                await await_io(asyncio.to_thread(_validate_pdf, source))
                token = secrets.token_urlsafe(32)
                config = directory / "engine.toml"
                fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as stream:
                    stream.write(f'[babeldoc]\nopenai-api-key = "{token}"\n')
                async with _model_bridge(model, token) as address:
                    output = await _run_engine(executable, directory, config, source, address, source_language, target_language)
                return await await_io(asyncio.to_thread(_read_pdf_result, directory, output))
    except TimeoutError as exc:
        raise OperationTimeoutError("PDF 版式翻译超时，可缩小文件或提取正文后重试", code="translation_pdf_timeout") from exc


def _babeldoc_executable() -> str | None:
    """仅从受信任的服务配置查找可执行文件。"""
    if os.name != "posix":
        return None
    found = shutil.which(os.getenv("YUXI_BABELDOC_COMMAND") or "babeldoc-stream")
    return os.path.abspath(found) if found else None


@asynccontextmanager
async def _model_bridge(model: BaseChatModel, token: str) -> AsyncIterator[str]:
    """为单次子进程开放仅绑定当前模型的本地接口。"""
    gateway = web.Application(client_max_size=256 * 1024)

    async def completion(request: web.Request) -> web.Response:
        """校验临时凭据和消息后通过当前模型返回 Chat Completions 结果。"""
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            raise web.HTTPUnauthorized()
        try:
            payload = await request.json()
        except ValueError as exc:
            raise web.HTTPBadRequest() from exc
        if not isinstance(payload, dict) or payload.get("model") != _GATEWAY_MODEL or payload.get("stream"):
            raise web.HTTPBadRequest()
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= 3:
            raise web.HTTPBadRequest()
        messages = []
        for message in raw_messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("system", "user")
                or not isinstance(message.get("content"), str)
            ):
                raise web.HTTPBadRequest()
            messages.append((message["role"], message["content"]))
        try:
            async with asyncio.timeout(45):
                answer = strip_chat_reasoning_tags((await model.ainvoke(messages)).text)
        except Exception as exc:
            logger.info("PDF 翻译模型调用失败，类型={}", type(exc).__name__)
            raise web.HTTPBadGateway() from None
        if not answer.strip():
            raise web.HTTPBadGateway()
        return web.json_response(
            {
                "id": "pdf-translation",
                "object": "chat.completion",
                "created": 0,
                "model": _GATEWAY_MODEL,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
            }
        )

    gateway.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(gateway, access_log=None, shutdown_timeout=0.1, handler_cancellation=True)
    await runner.setup()
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        site = web.SockSite(runner, listener)
        await site.start()
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
    finally:
        await runner.cleanup()
        listener.close()


async def _run_engine(
    executable: str, directory: Path, config: Path, source: Path, address: str, source_language: str, target_language: str
) -> str:
    """运行独立进程组，超时或取消时终止执行器和它的子进程。"""
    aliases = {"zh": "zh-CN"}
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if key in os.environ}
    env.update(
        {
            "HOME": os.getenv("YUXI_BABELDOC_HOME") or str(Path.home()),
            "TMPDIR": str(directory),
            "PYTHONUTF8": "1",
        }
    )
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--config",
            str(config),
            "--files",
            str(source),
            "--output",
            str(directory),
            "--openai",
            "--openai-model",
            _GATEWAY_MODEL,
            "--openai-base-url",
            address,
            "--lang-in",
            aliases.get(source_language, source_language),
            "--lang-out",
            aliases.get(target_language, target_language),
            "--qps",
            "2",
            "--ignore-cache",
            "--progress-json",
            "--progress-json-version",
            "2",
            "--watermark-output-mode",
            "no_watermark",
            "--no-dual",
            cwd=directory,
            env=env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=256 * 1024,
        )
    except OSError as exc:
        raise ServiceUnavailableError("无法启动 BabelDOC Stream，请检查服务端安装", code="translation_pdf_unavailable") from exc
    assert process.stdout is not None
    outcome = None
    try:
        async for line in process.stdout:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict) or event.get("schema") != _LAYOUT_SCHEMA:
                continue
            if event.get("type") == "error":
                if event.get("name") == "ScannedPDFError":
                    raise InvalidInputError("扫描版 PDF 请使用 OCR 提取正文后翻译", code="translation_pdf_scanned")
                raise UpstreamError("PDF 版式翻译失败，请检查模型和文件后重试", code="translation_pdf_failed")
            if event.get("type") == "finish":
                result = event.get("result")
                outcome = result.get("mono_pdf_path") if isinstance(result, dict) else None
        if await process.wait() == 0 and isinstance(outcome, str) and outcome:
            return outcome
    except ValueError as exc:
        raise UpstreamError("BabelDOC Stream 返回了无效结果", code="translation_pdf_failed") from exc
    finally:
        if sys.platform != "win32":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        # 出错时剩余 stdout 可能阻塞 Process.wait，先排空并等待传输关闭。
        await process.communicate()
    raise UpstreamError("BabelDOC Stream 未完成译文文件", code="translation_pdf_failed")


def _validate_pdf(path: Path) -> None:
    """检查可打开的 PDF 页面和同步翻译页数预算。"""
    try:
        with path.open("rb") as stream:
            doc = PdfReader(stream)
            pages = 0 if doc.is_encrypted else len(doc.pages)
    except Exception as exc:
        raise InvalidInputError("无法读取此 PDF，请检查文件格式或密码", code="translation_pdf_invalid") from exc
    if not pages:
        raise InvalidInputError("PDF 无法解锁或没有页面", code="translation_pdf_invalid")
    if pages > 30:
        raise ContentTooLargeError("PDF 版式翻译单次最多 30 页，请拆分文件", code="translation_pdf_too_large")


def _read_pdf_result(directory: Path, output: str) -> bytes:
    """只读此任务目录内的成功译文并限制返回字节。"""
    try:
        path = (directory / output).resolve(strict=True)
        if not path.is_relative_to(directory.resolve()) or path.name == "source.pdf" or path.suffix.lower() != ".pdf":
            raise ValueError
        with path.open("rb") as stream:
            content = stream.read(_MAX_RESULT_BYTES + 1)
        if not content.startswith(b"%PDF-"):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise UpstreamError("无法读取 PDF 译文", code="translation_pdf_failed") from exc
    if len(content) > _MAX_RESULT_BYTES:
        raise ContentTooLargeError("PDF 译文超过 40 MiB，请拆分原文件", code="translation_pdf_too_large")
    return content
