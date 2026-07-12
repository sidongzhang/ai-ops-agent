#!/usr/bin/env bash
# 安装 / 重装 Cloudflare Tunnel 的 macOS LaunchAgent（登录后自启 + 崩溃自动拉起）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$ROOT/scripts/com.aiops.cloudflared.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aiops.cloudflared.plist"
LABEL="com.aiops.cloudflared"
DOMAIN="gui/$(id -u)"

mkdir -p "$ROOT/.dev-stack/logs"
cp "$PLIST_SRC" "$PLIST_DST"

# 停掉手工启动的 tunnel，避免与 launchd 重复
screen -S aiops-cloudflared -X quit 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

sleep 3
if cloudflared tunnel info 99d1242d-5d03-4f87-a056-4eaaff54a372 2>/dev/null | grep -q CONNECTOR; then
  echo "✓ Cloudflare Tunnel 已通过 launchd 启动"
else
  echo "✗ Tunnel 未建立连接，请查看 $ROOT/.dev-stack/logs/cloudflared.log"
  exit 1
fi

curl -sf --max-time 10 https://aiops.tiancaizhaozhao.dpdns.org/healthz >/dev/null \
  && echo "✓ 公网健康检查通过: https://aiops.tiancaizhaozhao.dpdns.org/healthz" \
  || echo "⚠ 公网健康检查失败，tunnel 可能仍在握手"

echo
echo "管理命令:"
echo "  查看状态  launchctl print $DOMAIN/$LABEL"
echo "  重启隧道  launchctl kickstart -k $DOMAIN/$LABEL"
echo "  停止自启  launchctl bootout $DOMAIN/$LABEL"
echo "  查看日志  tail -f $ROOT/.dev-stack/logs/cloudflared.log"
