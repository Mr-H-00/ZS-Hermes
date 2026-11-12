"""真实 MinIO 首次并发上传，回读两个对象的完整字节。"""

import asyncio
from threading import Barrier, Event
from uuid import uuid4

import pytest
from minio.error import S3Error

from yuxi.infrastructure.minio.client import MinIOClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_concurrent_first_uploads_persist_both_objects(monkeypatch):
    """两个客户端同时发现空桶，后创建者复用先创建者的真实 Bucket。"""
    bucket = f"pytest-bucket-race-{uuid4().hex}"
    storage_clients = [MinIOClient(), MinIOClient()]
    checked = Barrier(2, timeout=10)
    first_created = Event()
    errors = []
    contents = {"first.txt": b"first upload bytes", "second.txt": b"second upload bytes"}

    for index, storage in enumerate(storage_clients):
        client = storage.client
        original_exists = client.bucket_exists
        original_create = client.make_bucket

        def bucket_exists(bucket_name, *, original=original_exists):
            """只同步存在检查，不伪造 MinIO 的查询结果。"""
            exists = original(bucket_name)
            assert not exists
            checked.wait()
            return exists

        def make_bucket(bucket_name, *, original=original_create, first=index == 0):
            """固定竞态顺序：两者都检查完后，第二个创建晚于第一个。"""
            if not first:
                assert first_created.wait(timeout=10)
            try:
                return original(bucket_name)
            except S3Error as exc:
                errors.append(exc.code)
                raise
            finally:
                if first:
                    first_created.set()

        monkeypatch.setattr(client, "bucket_exists", bucket_exists)
        monkeypatch.setattr(client, "make_bucket", make_bucket)

    try:
        results = await asyncio.gather(
            *(storage.aupload_file(bucket, name, data) for storage, (name, data) in zip(storage_clients, contents.items(), strict=True)),
            return_exceptions=True,
        )
        assert not any(isinstance(result, BaseException) for result in results), results
        assert errors == ["BucketAlreadyOwnedByYou"]
        for name, data in contents.items():
            assert await storage_clients[0].adownload_file(bucket, name) == data
    finally:
        client = MinIOClient().client
        if client.bucket_exists(bucket):
            for name in contents:
                client.remove_object(bucket, name)
            client.remove_bucket(bucket)
