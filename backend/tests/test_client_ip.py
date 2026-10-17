"""客户端 IP 解析：反向代理后仍能拿到真实来源，白名单支持 CIDR。"""
from starlette.requests import Request

from app.core.config import settings
from app.core.net import client_ip, ip_in_allowlist


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request({"type": "http", "client": (peer, 12345), "headers": headers})


def test_direct_peer_is_used_without_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,::1")
    # 直连（非受信代理）时忽略伪造的 XFF
    assert client_ip(_request("198.51.100.9", "1.2.3.4")) == "198.51.100.9"


def test_forwarded_for_is_used_from_trusted_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,::1")
    assert client_ip(_request("127.0.0.1", "203.0.113.7")) == "203.0.113.7"


def test_trusted_hops_are_skipped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,::1,10.0.0.0/8")
    # 右起第一个不受信地址才是客户端
    assert client_ip(_request("127.0.0.1", "203.0.113.7, 10.1.2.3")) == "203.0.113.7"


def test_all_hops_trusted_falls_back_to_leftmost(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,::1,10.0.0.0/8")
    assert client_ip(_request("127.0.0.1", "10.0.0.5, 10.0.0.6")) == "10.0.0.5"


def test_missing_forwarded_header_returns_peer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    assert client_ip(_request("127.0.0.1")) == "127.0.0.1"


def test_allowlist_supports_exact_ip_and_cidr() -> None:
    assert ip_in_allowlist("203.0.113.7", ["203.0.113.7"]) is True
    assert ip_in_allowlist("10.1.2.3", ["10.0.0.0/8"]) is True
    assert ip_in_allowlist("192.0.2.1", ["10.0.0.0/8", "203.0.113.7"]) is False
    assert ip_in_allowlist("not-an-ip", ["10.0.0.0/8"]) is False
    assert ip_in_allowlist("10.1.2.3", ["bad-entry"]) is False
