"""WPS 固定协议、地址边界和凭据加密的单元证据。"""

import pytest
from pydantic import ValidationError

from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.identity.services.cloud_drive_service import CloudDriveService, WPSConfigInput, kso_signature, wps_id_segment
from yuxi.shared.errors import InvalidOperationError, StateConflictError


# 使用已独立计算的协议向量，返回固定日期下的签名断言结果。
def test_kso_signature_matches_fixed_vectors():
    """空体与非空体必须采用官方 KSO-1 串联和十六进制 HMAC。"""
    headers = kso_signature(
        "test-app",
        "test-secret",
        "GET",
        "/v7/drives?page_size=100",
        "application/json",
        "Wed, 14 Oct 2026 08:00:00 GMT",
        b"",
    )
    assert headers == {
        "X-Kso-Date": "Wed, 14 Oct 2026 08:00:00 GMT",
        "X-Kso-Authorization": "KSO-1 test-app:e93acaf13d5ec3539719bd9e5a5c6af63a963fbe940595c64197107f4d5d3002",
    }
    assert (
        kso_signature(
            "test-app",
            "test-secret",
            "POST",
            "/v7/files",
            "application/json",
            "Wed, 14 Oct 2026 08:00:00 GMT",
            b'{"name":"test"}',
        )["X-Kso-Authorization"]
        == "KSO-1 test-app:38c86c67df2595c37247fa662271f478eb99b7d3b51b50beebc3875f4611f5a8"
    )


# 参数为非法回调 URL；所有样例均应在配置边界拒绝。
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/auth/wps/callback",
        "http://localhost:5173/auth/wps/callback",
        "https://localhost/auth/wps/callback",
        "https://app.localhost./auth/wps/callback",
        "https://app.local/auth/wps/callback",
        "https://127.0.0.1/auth/wps/callback",
        "http://[::1]:5173/auth/wps/callback",
        "https://[::1]/auth/wps/callback",
        "https://192.168.1.2/auth/wps/callback",
        "https://example.com/other",
        "https://user:password@example.com/auth/wps/callback",
        "https://example.com/auth/wps/callback?code=secret",
        "https://example.com/auth/wps/callback#fragment",
        "javascript:alert(1)",
    ],
)
def test_redirect_rejects_unowned_or_insecure_callback(url):
    """正式回调固定前端路径，拒绝本机、IP 和 HTTP。"""
    with pytest.raises(ValidationError):
        WPSConfigInput(enabled=False, app_id="", redirect_uri=url)


# 参数为公开域名的 HTTPS 回调，返回保持完整路径的配置模型。
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/auth/wps/callback",
        "https://example.com:8443/auth/wps/callback",
    ],
)
def test_redirect_accepts_registered_callback(url):
    """公开 HTTPS 回调可保存，端口保持与登记值一致。"""
    assert WPSConfigInput(enabled=False, app_id="", redirect_uri=url).redirect_uri == url


# 注入公开测试主密钥，验证密文重启可读和领域隔离的输出。
def test_credentials_are_encrypted_and_domain_separated(monkeypatch):
    """密文不包含原文，主密钥变化后明确失败。"""
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "wps-test-derivation-key-at-least-32-characters")
    encrypted = CloudDriveService._encrypt("example-credential")
    assert "example-credential" not in encrypted
    assert CloudDriveService._decrypt(encrypted) == "example-credential"
    assert AuthUtils.derive_encryption_key("wps-cloud-drive") != AuthUtils.derive_encryption_key("other")
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "rotated-wps-derivation-key-at-least-32-characters")
    with pytest.raises(StateConflictError, match="无法解密"):
        CloudDriveService._decrypt(encrypted)


# 传入路径控制符，验证外部 ID 只能成为一个编码片段。
def test_file_ids_cannot_rewrite_the_api_path():
    """斜线和查询字符被编码，相对目录标识被拒绝。"""
    assert wps_id_segment("a/b?x=1") == "a%2Fb%3Fx%3D1"
    for value in (".", ".."):
        with pytest.raises(InvalidOperationError, match="无效"):
            wps_id_segment(value)
