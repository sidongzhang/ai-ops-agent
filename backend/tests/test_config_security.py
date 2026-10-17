"""安全配置 fail-fast 的回归测试。

背景：`JWT_SECRET` 曾经带一个写死的开发默认值，而该值已经进入公开 git 历史。
任何人用这个值都能签出 `{"sub":"1"}` 的 token 直接成为平台 owner。
这里锁住三条不变量：
1. 开发环境绝不复用历史默认值（缺失时生成随机值）；
2. 生产环境缺失/过短/仍是默认值时拒绝启动；
3. `ENCRYPTION_KEY` 生产必填且必须是合法 Fernet 密钥（否则凭据明文落库）。
"""
import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings

LEGACY = "dev-secret-change-me-in-prod"


def _make(**overrides) -> Settings:
    base = {
        "environment": "dev",
        "jwt_secret": "",
        "encryption_key": "",
        "database_url": "sqlite://",
    }
    base.update(overrides)
    return Settings(**base)


def test_dev_generates_ephemeral_secret() -> None:
    with pytest.warns(RuntimeWarning, match="JWT_SECRET"):
        settings = _make()
    assert len(settings.jwt_secret) >= 32
    assert settings.jwt_secret != LEGACY


def test_dev_instances_get_distinct_secrets() -> None:
    with pytest.warns(RuntimeWarning):
        first = _make()
    with pytest.warns(RuntimeWarning):
        second = _make()
    assert first.jwt_secret != second.jwt_secret


def test_dev_replaces_legacy_default() -> None:
    with pytest.warns(RuntimeWarning, match="JWT_SECRET"):
        settings = _make(jwt_secret=LEGACY)
    assert settings.jwt_secret != LEGACY


def test_production_rejects_missing_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _make(environment="production", encryption_key=Fernet.generate_key().decode())


def test_production_rejects_legacy_default() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _make(
            environment="production",
            jwt_secret=LEGACY,
            encryption_key=Fernet.generate_key().decode(),
        )


def test_production_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="长度不足"):
        _make(
            environment="production",
            jwt_secret="short",
            encryption_key=Fernet.generate_key().decode(),
        )


def test_production_rejects_missing_encryption_key() -> None:
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        _make(environment="production", jwt_secret="j" * 48)


def test_invalid_encryption_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="Fernet"):
        _make(jwt_secret="j" * 48, encryption_key="definitely-not-a-fernet-key")


def test_production_accepts_strong_secret_and_key() -> None:
    settings = _make(
        environment="production",
        jwt_secret="j" * 48,
        encryption_key=Fernet.generate_key().decode(),
    )
    assert settings.is_production is True
