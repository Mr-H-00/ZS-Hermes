"""把中立 Workspace preview 结果装配为 HTTP 响应。"""

import io
import os
from urllib.parse import quote

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from yuxi.shared.files import PreparedFile, PreviewResult


def render_file_result(result: PreparedFile | PreviewResult | dict) -> FileResponse | StreamingResponse | dict:
    """把已准备好的业务文件结果装配为 HTTP 响应。"""
    if isinstance(result, PreparedFile):
        headers = None
        if result.explicit_disposition and result.filename:
            headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename)}"}
        try:
            return FileResponse(
                result.path,
                media_type=result.media_type,
                filename=result.filename if not result.explicit_disposition else None,
                headers=headers,
                content_disposition_type="attachment",
                background=BackgroundTask(os.unlink, result.path),
            )
        except Exception:
            os.unlink(result.path)
            raise
    if isinstance(result, PreviewResult):
        return _preview_response(result)
    return result


def _preview_response(result: PreviewResult) -> dict | StreamingResponse:
    if not isinstance(result.content, bytes):
        return result.payload()
    filename = result.filename or "preview"
    return StreamingResponse(
        io.BytesIO(result.content),
        media_type=result.media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Yuxi-Preview-Type": result.preview_type,
            "X-Yuxi-Preview-Filename": quote(filename),
        },
    )
