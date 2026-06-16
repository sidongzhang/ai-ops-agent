#!/usr/bin/env bash
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

echo "🤖 启动飞书机器人服务..."

# 检查配置
source .env 2>/dev/null
if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ]; then
    echo "❌ 请先在 .env 中填入 FEISHU_APP_ID 和 FEISHU_APP_SECRET"
    exit 1
fi

PORT=${FEISHU_BOT_PORT:-8080}

# 启动 Webhook 服务
python3 feishu_bot/server.py &
BOT_PID=$!
echo "✅ 飞书 Bot 已启动 (PID: $BOT_PID)，端口 $PORT"
echo ""

# 用 cloudflared 快速隧道暴露公网地址（免注册）
if command -v cloudflared &> /dev/null; then
    echo "🌐 正在用 Cloudflare Tunnel 创建公网隧道..."
    cloudflared tunnel --url http://localhost:$PORT 2>&1 &
    CF_PID=$!
    sleep 5
    # 从 cloudflared 日志提取 URL
    CF_URL=$(cloudflared tunnel --url http://localhost:$PORT 2>&1 &
             sleep 4
             curl -s http://localhost:20241/metrics 2>/dev/null | grep trycloudflare | head -1 || true)
    echo ""
    echo "📋 Cloudflare 隧道已创建，请查看上方输出中形如："
    echo "   https://xxxx-xxxx.trycloudflare.com"
    echo ""
    echo "   将该地址 + /webhook 填入飞书「事件订阅」→「请求地址」"
    echo "   例：https://xxxx-xxxx.trycloudflare.com/webhook"
    wait $BOT_PID
else
    echo "❌ 未找到 cloudflared，请先运行："
    echo "   brew install cloudflare/cloudflare/cloudflared"
    kill $BOT_PID
    exit 1
fi
