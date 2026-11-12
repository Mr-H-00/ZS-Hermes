"""将完整解析资源发布到调用方指定的对象存储位置。"""

import asyncio
from collections.abc import Callable

from yuxi.infrastructure.document_parsing import ParseResult
from yuxi.infrastructure.document_parsing.artifacts import replace_image_links
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client


async def upload_resources(
    result: ParseResult,
    bucket: str,
    prefix: str,
    url_builder: Callable[[str], str],
) -> str:
    """上传资源并改写完整相对链接；失败清理本次独占前缀。"""
    if not prefix or not prefix.endswith("/") or prefix.startswith("/") or ".." in prefix.split("/"):
        raise ValueError("资源发布必须指定独占的相对目录前缀")
    client = get_minio_client()
    mapping = {}
    try:
        if result.resources:
            await asyncio.to_thread(client.ensure_bucket_exists, bucket)
        for relative in result.resources:
            object_name = f"{prefix}{relative}"
            data = await asyncio.to_thread((result.directory / relative).read_bytes)
            await await_io(client.aupload_file(bucket_name=bucket, object_name=object_name, data=data))
            mapping[relative] = url_builder(object_name)
        markdown = await asyncio.to_thread(result.markdown_path.read_text, encoding="utf-8")
        return replace_image_links(markdown, mapping)
    except BaseException:
        await client.adelete_objects_by_prefix(bucket, prefix)
        raise
