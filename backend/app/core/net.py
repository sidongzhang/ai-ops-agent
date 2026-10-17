"""客户端 IP 解析与白名单匹配。

背景：系统 token 的 `allowed_ips` 之前用 `request.client.host` 判断，而平台部署在
Caddy / cloudflared 之后——这个值永远是 `127.0.0.1`，白名单要么全拒、要么形同虚设。

规则：
- 只有来自 `TRUSTED_PROXIES` 的请求才采信 `X-Forwarded-For`；
- 从右往左取第一个「不在受信代理集合里」的地址（左侧可被客户端伪造）；
- 白名单支持单 IP 与 CIDR（`10.0.0.0/8`）。
"""
from __future__ import annotations

import ipaddress

from fastapi import Request

from app.core.config import settings


def _trusted_networks() -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for raw in (settings.trusted_proxies or "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _parse_ip(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def _is_trusted(ip: ipaddress._BaseAddress | None, networks: list[ipaddress._BaseNetwork]) -> bool:
    return ip is not None and any(ip in network for network in networks)


def client_ip(request: Request) -> str:
    """返回真实客户端 IP（无法确定时回退到直连 peer）。"""
    peer_raw = request.client.host if request.client else ""
    peer = _parse_ip(peer_raw)
    networks = _trusted_networks()
    if not _is_trusted(peer, networks):
        return peer_raw

    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [item.strip() for item in forwarded.split(",") if item.strip()]
    if not hops:
        return peer_raw

    # 右侧是最近一跳。跳过受信代理，返回第一个不受信的地址。
    for hop in reversed(hops):
        candidate = _parse_ip(hop)
        if candidate is None:
            continue
        if not _is_trusted(candidate, networks):
            return str(candidate)
    # 全部都是受信代理（例如本机 curl）：用最左侧
    return str(_parse_ip(hops[0]) or peer_raw)


def ip_in_allowlist(ip: str, allowlist: list[str]) -> bool:
    """allowlist 支持精确 IP 与 CIDR；无法解析的条目忽略。"""
    address = _parse_ip(ip)
    if address is None:
        return False
    for raw in allowlist or []:
        try:
            if address in ipaddress.ip_network(str(raw).strip(), strict=False):
                return True
        except ValueError:
            continue
    return False
