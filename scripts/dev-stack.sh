#!/usr/bin/env bash
# AIOps 本地开发栈：Docker 基础服务 + 后端 + 前端 + Celery
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.dev-stack"
LOG_DIR="$RUN_DIR/logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"

pick_celery_redis() {
  if nc -z 127.0.0.1 6380 2>/dev/null; then
    echo "redis://127.0.0.1:6380"
  elif nc -z 127.0.0.1 6379 2>/dev/null; then
    echo "redis://127.0.0.1:6379"
  else
    echo ""
  fi
}

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
    sleep 1
  fi
}

stop_pidfile() {
  local file="$1"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
}

stop_screen() {
  local name="$1"
  screen -S "$name" -X quit 2>/dev/null || true
}

start_screen() {
  local name="$1"
  local cmd="$2"
  stop_screen "$name"
  screen -dmS "$name" bash -lc "$cmd"
}

stop_app() {
  echo "→ 停止应用进程..."
  stop_screen aiops-backend
  stop_screen aiops-frontend
  stop_screen aiops-celery-worker
  stop_screen aiops-celery-beat
  stop_pidfile "$RUN_DIR/backend.pid"
  stop_pidfile "$RUN_DIR/frontend.pid"
  stop_pidfile "$RUN_DIR/celery-worker.pid"
  stop_pidfile "$RUN_DIR/celery-beat.pid"
  kill_port "$BACKEND_PORT"
  kill_port "$FRONTEND_PORT"
  pkill -f "$ROOT/backend/.venv/bin/celery -A app.workers.celery.celery" 2>/dev/null || true
  pkill -f "$ROOT/backend/.venv/bin/uvicorn app.main:app" 2>/dev/null || true
  pkill -f "$ROOT/frontend/node_modules/.bin/vite" 2>/dev/null || true
}

start_docker() {
  echo "→ 启动 Docker 基础服务 (mysql/redis/kafka/prometheus)..."
  cd "$ROOT"
  docker compose start 2>/dev/null || true
  for c in ai-ops-agent-kafka-1 ai-ops-agent-kafka-exporter-1 ai-ops-agent-mysqld-exporter-1; do
    docker start "$c" 2>/dev/null || true
  done
  # 已有容器时只 ensure up，避免 kafka 等孤儿容器名冲突导致整脚本失败
  docker compose up -d --no-recreate 2>/dev/null || true
}

stop_docker() {
  echo "→ 停止 Docker 基础服务..."
  cd "$ROOT"
  docker compose stop
}

wait_http() {
  local url="$1"
  local name="$2"
  local i
  for i in {1..30}; do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "  ✓ $name 就绪"
      return 0
    fi
    sleep 1
  done
  echo "  ✗ $name 启动超时: $url" >&2
  return 1
}

start_app() {
  if ! command -v screen >/dev/null 2>&1; then
    echo "  ✗ 需要 screen 才能持久运行（brew install screen）" >&2
    exit 1
  fi

  local redis_base
  redis_base="$(pick_celery_redis)"
  if [[ -z "$redis_base" ]]; then
    echo "  ⚠ 未检测到 Redis (6379/6380)，Celery 可能无法启动" >&2
  fi

  echo "→ 启动后端 (:$BACKEND_PORT) [screen: aiops-backend]..."
  start_screen aiops-backend \
    "cd '$ROOT/backend' && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT --reload >> '$LOG_DIR/backend.log' 2>&1"
  wait_http "http://127.0.0.1:$BACKEND_PORT/healthz" "后端"

  echo "→ 启动前端 ($FRONTEND_HOST:$FRONTEND_PORT) [screen: aiops-frontend]..."
  start_screen aiops-frontend \
    "cd '$ROOT/frontend' && exec npm run dev -- --host $FRONTEND_HOST --port $FRONTEND_PORT >> '$LOG_DIR/frontend.log' 2>&1"
  wait_http "http://$FRONTEND_HOST:$FRONTEND_PORT/" "前端"

  if [[ -n "$redis_base" ]]; then
    local broker="${CELERY_BROKER_URL:-${redis_base}/0}"
    local backend="${CELERY_RESULT_BACKEND:-${redis_base}/1}"
    echo "→ 启动 Celery (broker=$broker)..."
    start_screen aiops-celery-worker \
      "cd '$ROOT/backend' && export CELERY_BROKER_URL='$broker' CELERY_RESULT_BACKEND='$backend' && exec .venv/bin/celery -A app.workers.celery.celery worker --loglevel=info --concurrency=2 >> '$LOG_DIR/celery-worker.log' 2>&1"
    start_screen aiops-celery-beat \
      "cd '$ROOT/backend' && export CELERY_BROKER_URL='$broker' CELERY_RESULT_BACKEND='$backend' && exec .venv/bin/celery -A app.workers.celery.celery beat --loglevel=info >> '$LOG_DIR/celery-beat.log' 2>&1"
    echo "  ✓ Celery worker + beat 已启动 (screen)"
  fi
}

status() {
  echo "=== Docker ==="
  cd "$ROOT" && docker compose ps
  echo
  echo "=== Screen 会话 ==="
  screen -ls 2>&1 | grep -E 'aiops-(backend|frontend|celery)' || echo "  (无 aiops screen 会话)"
  echo
  echo "=== 应用 ==="
  lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1 && echo "  ✓ backend :$BACKEND_PORT" || echo "  ✗ backend :$BACKEND_PORT"
  lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1 && echo "  ✓ frontend :$FRONTEND_PORT" || echo "  ✗ frontend :$FRONTEND_PORT"
  pgrep -f "$ROOT/backend/.venv/bin/celery -A app.workers.celery.celery worker" >/dev/null && echo "  ✓ celery-worker" || echo "  ✗ celery-worker"
  pgrep -f "$ROOT/backend/.venv/bin/celery -A app.workers.celery.celery beat" >/dev/null && echo "  ✓ celery-beat" || echo "  ✗ celery-beat"
  echo
  curl -sf "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null && echo "  ✓ backend /healthz" || echo "  ✗ backend /healthz"
  curl -sf "http://$FRONTEND_HOST:$FRONTEND_PORT/" >/dev/null && echo "  ✓ frontend HTTP" || echo "  ✗ frontend HTTP"
  echo
  echo "日志目录: $LOG_DIR"
  echo "查看日志: screen -r aiops-backend | screen -r aiops-frontend"
}

usage() {
  cat <<EOF
用法: $(basename "$0") <start|stop|restart|status> [--docker-only|--app-only]

  start    启动 Docker + 应用（默认）
  stop     停止应用 + Docker
  restart  先 stop 再 start
  status   查看运行状态

选项:
  --docker-only  只操作 Docker 基础服务
  --app-only     只操作 backend/frontend/celery（不启停 Docker）

访问:
  前端  http://$FRONTEND_HOST:$FRONTEND_PORT
  后端  http://127.0.0.1:$BACKEND_PORT/docs
EOF
}

main() {
  local cmd="${1:-}"
  local scope="all"
  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --docker-only) scope="docker" ;;
      --app-only) scope="app" ;;
      *) usage; exit 1 ;;
    esac
    shift
  done

  case "$cmd" in
    start)
      [[ "$scope" != "app" ]] && start_docker
      [[ "$scope" != "docker" ]] && start_app
      status
      ;;
    stop)
      [[ "$scope" != "docker" ]] && stop_app
      [[ "$scope" != "app" ]] && stop_docker
      ;;
    restart)
      [[ "$scope" != "docker" ]] && stop_app
      [[ "$scope" != "app" ]] && stop_docker
      sleep 1
      [[ "$scope" != "app" ]] && start_docker
      [[ "$scope" != "docker" ]] && start_app
      status
      ;;
    status)
      status
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
