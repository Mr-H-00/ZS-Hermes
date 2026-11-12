"""将 HTTP 上传转换为业务层可消费的文件内容。"""

import asyncio
import os

from fastapi import UploadFile

from yuxi.infrastructure.filesystem import await_io
from yuxi.shared.files import FileInput


async def read_upload_with_limit(
    upload: UploadFile,
    *,
    max_size_bytes: int,
    too_large_message: str,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    """限量读取小文件，不信任请求提供的文件大小。"""
    await upload.seek(0)
    written = 0
    chunks: list[bytes] = []

    while chunk := await upload.read(chunk_size):
        written += len(chunk)
        if written > max_size_bytes:
            raise ValueError(too_large_message)
        chunks.append(chunk)

    return b"".join(chunks)


async def prepare_upload_files(
    uploads: list[UploadFile],
    *,
    max_size_bytes: int,
    too_large_message: str,
) -> list[FileInput]:
    """校验暂存文件的真实长度，借用输入流直到请求处理结束。"""
    files = []
    for upload in uploads:
        size = await await_io(asyncio.to_thread(upload.file.seek, 0, os.SEEK_END))
        await await_io(asyncio.to_thread(upload.file.seek, 0))
        if size > max_size_bytes:
            raise ValueError(too_large_message)
        files.append(FileInput(filename=upload.filename or "", source=upload.file))
    return files
