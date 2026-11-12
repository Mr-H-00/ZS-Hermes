"""访问日志中间件 - 记录请求处理时间"""

import logging
import time
from collections.abc import Awaitable, Callable
from posixpath import normpath

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

access_logger = logging.getLogger("access_logger")

if not access_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s: %(message)s", datefmt="%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.INFO)
    # 避免传播到根日志记录器，防止重复日志
    access_logger.propagate = False


def extract_client_ip(request: Request) -> str:
    """提取客户端 IP；代理场景优先 X-Forwarded-For 的首个地址。"""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class AccessLogMiddleware(BaseHTTPMiddleware):
    """访问日志中间件 - 记录请求处理时间"""

    def __init__(self, app, logger: logging.Logger | None = None):
        super().__init__(app)
        self.logger = logger or access_logger

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start_time = time.perf_counter()
        client_ip = extract_client_ip(request)

        response = await call_next(request)

        process_time_ms = int((time.perf_counter() - start_time) * 1000)

        query = request.url.query
        # 代理可转发原始多斜杠 URI；分类使用归一化路径，日志仍保留请求路径。
        path = normpath("/" + request.url.path.lstrip("/"))
        if path.startswith(("/api/auth/oidc/", "/auth/oidc/", "/api/system/mcp-servers/oauth/", "/system/mcp-servers/oauth/")):
            query = ""
        self.logger.info(
            f"{client_ip}:{request.client.port if request.client else 'unknown'} - "
            f'"{request.method} {request.url.path}{"?" + query if query else ""} '
            f'HTTP/{request.scope["http_version"]}" '
            f"{response.status_code} - {process_time_ms}ms"
        )

        return response
