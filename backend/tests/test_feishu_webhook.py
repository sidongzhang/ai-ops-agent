"""飞书 Webhook 验签回归测试。

这个端点历史上是**零验签**的：匿名请求即可驱动平台用租户自己的凭据跑一次带工具的诊断。
测试锁住四条不变量：
1. 配置了 Verification Token 时，v2（header.token）与 v1（顶层 token）都必须校验；
2. token 不匹配 → 403；服务端未配置 → 503（fail-closed，不放行）；
3. 加密事件必须按飞书规范解密后再校验 token；
4. 收到加密事件但没配 Encrypt Key → 拒绝，而不是把密文当明文处理。
"""
import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import settings
from app.services.notifications.webhook import (
    WebhookAuthError,
    decrypt_feishu_payload,
    verify_webhook,
)


def _encrypt(payload: dict, encrypt_key: str) -> str:
    """按飞书规范加密：AES-256-CBC + PKCS7，IV 随机且置于密文前 16 字节。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    pad = 16 - len(raw) % 16
    padded = raw + bytes([pad]) * pad
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    body = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + body).decode()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "feishu_verification_token", "vt-secret")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")
    return "vt-secret"


def test_accepts_v2_header_token(configured) -> None:
    payload = {"header": {"token": configured, "event_type": "im.message.receive_v1"}, "event": {}}
    assert verify_webhook(payload) is payload


def test_accepts_v1_top_level_token(configured) -> None:
    payload = {"token": configured, "challenge": "abc", "type": "url_verification"}
    assert verify_webhook(payload)["challenge"] == "abc"


def test_rejects_wrong_token(configured) -> None:
    with pytest.raises(WebhookAuthError) as excinfo:
        verify_webhook({"header": {"token": "wrong"}})
    assert excinfo.value.status_code == 403


def test_rejects_missing_token(configured) -> None:
    with pytest.raises(WebhookAuthError) as excinfo:
        verify_webhook({"header": {"event_type": "im.message.receive_v1"}})
    assert excinfo.value.status_code == 403


def test_fail_closed_when_server_token_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "")
    with pytest.raises(WebhookAuthError) as excinfo:
        verify_webhook({"header": {"token": "anything"}})
    assert excinfo.value.status_code == 503


def test_encrypted_payload_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "vt-secret")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "enc-key")
    plain = {"header": {"token": "vt-secret", "event_type": "im.message.receive_v1"}, "event": {}}
    decrypted = verify_webhook({"encrypt": _encrypt(plain, "enc-key")})
    assert decrypted == plain


def test_encrypted_payload_still_checks_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "vt-secret")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "enc-key")
    plain = {"header": {"token": "wrong"}}
    with pytest.raises(WebhookAuthError) as excinfo:
        verify_webhook({"encrypt": _encrypt(plain, "enc-key")})
    assert excinfo.value.status_code == 403


def test_encrypted_payload_without_key_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "vt-secret")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")
    with pytest.raises(WebhookAuthError) as excinfo:
        verify_webhook({"encrypt": "whatever"})
    assert excinfo.value.status_code == 503


def test_corrupted_ciphertext_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_encrypt_key", "enc-key")
    with pytest.raises(WebhookAuthError):
        decrypt_feishu_payload(base64.b64encode(b"too-short").decode(), "enc-key")
