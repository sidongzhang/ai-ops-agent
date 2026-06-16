#!/usr/bin/env bash
set -e
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

echo "🚀 启动 AI Ops Demo 系统"
echo "================================"

# 1. 启动 Docker 基础设施
echo "▶ 启动 Docker 服务（Kafka / MySQL / Redis / Prometheus）..."
docker compose up -d

# 2. 等待服务就绪
echo "⏳ 等待服务初始化（30秒）..."
sleep 30

# 3. 准备目录
mkdir -p logs pids

# 4. 安装依赖
echo "📦 安装 Python 依赖..."
pip3 install -r business/requirements.txt -q
pip3 install -r agent/requirements.txt -q

# 5. 启动业务服务
echo ""
echo "▶ 启动 Producer..."
PYTHONUNBUFFERED=1 python3 business/producer.py >> logs/producer.log 2>&1 &
echo $! > pids/producer.pid
echo "   PID: $(cat pids/producer.pid)"

echo "▶ 启动 Consumer..."
PYTHONUNBUFFERED=1 python3 business/consumer.py >> logs/consumer.log 2>&1 &
echo $! > pids/consumer.pid
echo "   PID: $(cat pids/consumer.pid)"

echo "▶ 启动 Frontend..."
PYTHONUNBUFFERED=1 python3 business/frontend/app.py >> logs/frontend.log 2>&1 &
echo $! > pids/frontend.pid
echo "   PID: $(cat pids/frontend.pid)"

sleep 3

echo ""
echo "✅ 全部启动完成！"
echo "================================"
echo "  数据面板:   http://localhost:5001"
echo "  Prometheus: http://localhost:9090"
echo ""
echo "💡 Demo 操作："
echo "  注入故障: ./scripts/inject_fault.sh producer"
echo "  AI 修复:  cd agent && python3 agent.py \"系统好像有问题，帮我检查一下\""
echo "  查看状态: ./scripts/status.sh"
echo "  停止系统: ./scripts/stop.sh"
