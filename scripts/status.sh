#!/usr/bin/env bash
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

echo "=== 服务状态检查 ==="
echo ""
echo "Python 业务服务："
for SERVICE in producer consumer frontend; do
    PID_FILE="pids/$SERVICE.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "  ✅ $SERVICE (PID $PID)"
        else
            echo "  ❌ $SERVICE (PID 文件存在但进程已退出)"
        fi
    else
        echo "  ❌ $SERVICE (未运行)"
    fi
done

echo ""
echo "Docker 服务："
docker compose ps 2>/dev/null || echo "  (Docker 未运行)"

echo ""
echo "访问地址："
echo "  http://localhost:5001  —— 数据面板"
echo "  http://localhost:9090  —— Prometheus"
