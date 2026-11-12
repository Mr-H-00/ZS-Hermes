"""
MinIO 存储工具函数
简化的存储操作辅助函数
"""

import os
import uuid
import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from yuxi.infrastructure.minio.client import aupload_file_to_minio


def get_file_size(file_path: str) -> int:
    """获取文件大小"""
    return os.path.getsize(file_path)


def generate_unique_filename(original_name: str) -> str:
    """生成唯一的文件名"""
    name_parts = original_name.rsplit(".", 1)
    base_name = name_parts[0] if len(name_parts) == 2 else original_name
    extension = f".{name_parts[1]}" if len(name_parts) == 2 else ""
    return f"{base_name}_{uuid.uuid4().hex[:8]}{extension}"


async def upload_image_to_minio(
    file_content: bytes,
    *,
    object_prefix: str,
    max_size_bytes: int,
    too_large_message: str,
) -> str:
    """验证图片内容并保存到公共对象存储。"""
    if len(file_content) > max_size_bytes:
        raise ValueError(too_large_message)

    invalid_image_message = "只能上传 PNG、JPEG、WebP 或 GIF 图片"
    allowed_formats = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(file_content)) as image:
                file_extension = allowed_formats.get(image.format or "")
                if file_extension is None:
                    raise ValueError(invalid_image_message)

                for frame_index in range(getattr(image, "n_frames", 1)):
                    image.seek(frame_index)
                    image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as exc:
        raise ValueError(invalid_image_message) from exc

    object_name = f"{object_prefix.strip('/')}/{uuid.uuid4()}.{file_extension}"
    return await aupload_file_to_minio("public", object_name, file_content)
