#!/usr/bin/env bash
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

echo "🛑 停止所有服务..."

# 停止 Python 业务服务
for SERVICE in producer consumer frontend; do
    PID_FILE="pids/$SERVICE.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && echo "  已停止 $SERVICE (PID $PID)"
        else
            echo "  $SERVICE 已经停止"
        fi
        rm -f "$PID_FILE"
    else
        echo "  $SERVICE 未在运行"
    fi
done

# 停止 Docker 服务
echo "▶ 停止 Docker 服务..."
docker compose down

echo "✅ 全部停止完成"
