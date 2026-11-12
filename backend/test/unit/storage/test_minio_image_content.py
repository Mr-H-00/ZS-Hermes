"""图片存储只消费中立字节，并保留内容及大小校验。"""

from io import BytesIO

import pytest
from PIL import Image

from yuxi.infrastructure.minio import utils


@pytest.mark.asyncio
@pytest.mark.parametrize("image_format,extension", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("GIF", ".gif")])
async def test_image_content_is_stored_unchanged(monkeypatch, image_format, extension):
    """所有允许的图片格式都按实际格式生成对象名，并保存原始内容。"""
    source = BytesIO()
    Image.new("RGB", (2, 2), "red").save(source, format=image_format)
    content = source.getvalue()
    objects = {}

    async def store(bucket, name, data):
        """使用内容 oracle 记录对象，而非仅检查调用次数。"""
        objects[(bucket, name)] = data
        return f"/minio/{bucket}/{name}"

    monkeypatch.setattr(utils, "aupload_file_to_minio", store)
    url = await utils.upload_image_to_minio(
        content, object_prefix="images/user-1", max_size_bytes=len(content), too_large_message="too large"
    )
    [(bucket, name)] = objects
    assert bucket == "public"
    assert name.startswith("images/user-1/")
    assert name.endswith(extension)
    assert objects[(bucket, name)] == content
    assert url == f"/minio/{bucket}/{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("content,limit,message", [(b"not an image", 100, "只能上传"), (b"123456", 5, "too large")])
async def test_image_rejection_creates_no_object(monkeypatch, content, limit, message):
    """无效内容或直接调用时的超限都在对象写入前拒绝。"""
    objects = {}

    async def store(bucket, name, data):
        """保留写入事实以验证失败没有副作用。"""
        objects[(bucket, name)] = data
        return "unused"

    monkeypatch.setattr(utils, "aupload_file_to_minio", store)
    with pytest.raises(ValueError, match=message):
        await utils.upload_image_to_minio(content, object_prefix="images/user-1", max_size_bytes=limit, too_large_message="too large")
    assert objects == {}
