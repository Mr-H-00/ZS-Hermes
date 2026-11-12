"""Bucket 创建的幂等结果与真实失败边界。"""

from unittest.mock import Mock

import pytest
from minio.error import S3Error

from yuxi.infrastructure.minio.client import MinIOClient, StorageError


def s3_error(code):
    """构造服务端明确返回的 S3 错误。"""
    return S3Error(None, code, "bucket operation failed", "/test-bucket", "request", "host")


@pytest.mark.parametrize("bucket", ["knowledgebases", "public"])
def test_owned_bucket_race_continues_upload_and_applies_public_policy(bucket):
    """同账号并发创建成功后仍上传字节，公开桶仍配置对象读取策略。"""
    storage = MinIOClient()
    storage._client = Mock()
    storage.client.bucket_exists.return_value = False
    storage.client.make_bucket.side_effect = s3_error("BucketAlreadyOwnedByYou")
    stored = {}

    def put_object(**kwargs):
        """记录上传边界消费的完整字节。"""
        stored[kwargs["object_name"]] = kwargs["data"].read()
        return object()

    storage.client.put_object.side_effect = put_object

    uploaded = storage.upload_file(bucket, "report.txt", b"document bytes")

    assert uploaded.bucket_name == bucket
    assert stored == {"report.txt": b"document bytes"}
    if bucket == "public":
        policy = storage.client.set_bucket_policy.call_args.kwargs["policy"]
        assert '"s3:GetObject"' in policy
        assert '"s3:ListBucket"' not in policy
    else:
        storage.client.set_bucket_policy.assert_not_called()


@pytest.mark.parametrize("code", ["AccessDenied", "BucketAlreadyExists", "InternalError"])
def test_other_bucket_creation_errors_reject_upload(code):
    """不把权限、其他所有者或服务端失败伪装为创建成功。"""
    storage = MinIOClient()
    storage._client = Mock()
    storage.client.bucket_exists.return_value = False
    storage.client.make_bucket.side_effect = s3_error(code)

    with pytest.raises(StorageError, match=code):
        storage.upload_file("knowledgebases", "report.txt", b"document bytes")

    storage.client.put_object.assert_not_called()


def test_owned_bucket_error_outside_creation_is_not_ignored():
    """只允许创建动作的幂等结果，存在检查的错误仍拒绝。"""
    storage = MinIOClient()
    storage._client = Mock()
    storage.client.bucket_exists.side_effect = s3_error("BucketAlreadyOwnedByYou")

    with pytest.raises(StorageError, match="BucketAlreadyOwnedByYou"):
        storage.upload_file("knowledgebases", "report.txt", b"document bytes")

    storage.client.put_object.assert_not_called()


def test_public_policy_failure_after_owned_bucket_race_rejects_upload():
    """创建竞态不能绕过公开策略设置失败。"""
    storage = MinIOClient()
    storage._client = Mock()
    storage.client.bucket_exists.return_value = False
    storage.client.make_bucket.side_effect = s3_error("BucketAlreadyOwnedByYou")
    storage.client.set_bucket_policy.side_effect = s3_error("AccessDenied")

    with pytest.raises(StorageError, match="AccessDenied"):
        storage.upload_file("public", "report.txt", b"document bytes")

    storage.client.put_object.assert_not_called()
