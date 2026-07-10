#!/bin/bash
# AIOps Platform — 开发环境一键启动
set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== AIOps Platform 开发服务器 ==="

# 1. 数据库迁移
echo "→ 运行数据库迁移..."
cd "$REPO_DIR/backend"
.venv/bin/alembic upgrade head

# 2. 启动后端（screen 会话，持久运行）
echo "→ 启动后端 :8000..."
screen -dmS aiops-backend .venv/bin/uvicorn app.main:app --port 8000 --reload
sleep 2
if curl -s http://localhost:8000/healthz > /dev/null 2>&1; then
  echo "   ✅ 后端运行中"
else
  echo "   ❌ 后端启动失败！"
  exit 1
fi

# 3. 启动前端（screen 会话，持久运行）
echo "→ 启动前端 :5173..."
cd "$REPO_DIR/frontend"
if lsof -i :5173 > /dev/null 2>&1; then
  echo "   ⏩ 前端已在运行"
else
  screen -dmS aiops-frontend npx vite --port 5173
  sleep 3
  if curl -sI http://localhost:5173/ > /dev/null 2>&1; then
    echo "   ✅ 前端运行中"
  else
    echo "   ❌ 前端启动失败！"
    exit 1
  fi
fi

echo ""
echo "=== 服务已启动 ==="
echo "后端 API:   http://localhost:8000"
echo "API 文档:   http://localhost:8000/docs"
echo "前端控制台: http://localhost:5173"
echo ""
echo "停止: screen -X -S aiops-backend quit; screen -X -S aiops-frontend quit"
