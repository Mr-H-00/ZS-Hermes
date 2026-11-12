"""MinIO URL 识别与 bucket/object 解析。"""

from urllib.parse import unquote, urlparse

from yuxi.infrastructure.minio.client import MinIOClient
from yuxi.infrastructure.observability.logging import logger

_KNOWN_BUCKETS = frozenset(MinIOClient.KB_BUCKETS.values()) | MinIOClient.PUBLIC_READ_BUCKETS


def is_minio_url(file_path: str) -> bool:
    """检测是否是本系统生成的 MinIO 存储 URL。"""
    parsed_url = urlparse(file_path)
    if parsed_url.scheme == "minio":
        return bool(parsed_url.netloc and parsed_url.path.lstrip("/"))

    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return False

    path_parts = parsed_url.path.lstrip("/").split("/", 1)
    if len(path_parts) != 2:
        return False
    return path_parts[0] in _KNOWN_BUCKETS


def parse_minio_url(file_path: str) -> tuple[str, str]:
    """解析 minio:// 或 http(s):// 形式的对象 URL，返回 bucket 与 object。"""
    try:
        parsed_url = urlparse(file_path)
        if parsed_url.scheme == "minio":
            bucket_name = parsed_url.netloc
            object_name = unquote(parsed_url.path.lstrip("/"))
        else:
            path_parts = parsed_url.path.lstrip("/").split("/", 1)
            if len(path_parts) < 2:
                raise ValueError(f"无法解析MinIO URL中的bucket名称: {file_path}")
            bucket_name = path_parts[0]
            object_name = unquote(path_parts[1])
    except (TypeError, ValueError) as exc:
        logger.error(f"Failed to parse MinIO URL {file_path}: {exc}")
        raise ValueError(f"无法解析MinIO URL: {file_path}") from exc

    logger.debug(f"Parsed MinIO URL: bucket_name={bucket_name}, object_name={object_name}")
    return bucket_name, object_name
