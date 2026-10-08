#!/bin/bash
# AIOps Platform — 开发环境一键启动（薄包装）
#
# 说明：真正的启动/停止编排统一由 scripts/stack-launcher.sh 负责（桌面“启动AIOps栈”
# 用的也是它）。这里保留入口名，避免历史习惯/文档失效，同时消除两套启动逻辑分叉。
#
#   ./start_dev.sh          → 等价于 stack-launcher.sh up
#   ./start_dev.sh status   → 查看状态
#   ./start_dev.sh down     → 停止全部
set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$REPO_DIR/scripts/stack-launcher.sh"

if [ ! -x "$LAUNCHER" ]; then
  echo "找不到启动脚本: $LAUNCHER" >&2
  exit 1
fi

exec "$LAUNCHER" "${@:-up}"
