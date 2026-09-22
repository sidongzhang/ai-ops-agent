#!/bin/bash
# ================================================================
#  AIOps 本地开发栈 —— 一键启动 / 一键全停
#
#    up      前台启动全部服务；按 Ctrl+C 停止全部并退出
#    down    停止全部（应用 + 容器 + colima 虚拟机）
#    status  查看当前运行状态
#    logs    实时跟踪日志
# ================================================================
set -u

HOME_DIR="$HOME"
ROOT="$HOME_DIR/ai-ops-agent"

# algp / svom / hxmt 所在目录。
# 你把 Desktop/test 改名成 Desktop/school 过，这里做多路径兼容，避免再改动后失效。
detect_dir() {
  local p
  for p in "$@"; do [ -d "$p" ] && { printf '%s' "$p"; return 0; }; done
  return 1
}
ALGP_DIR="$(detect_dir "$HOME_DIR/Desktop/school/algp-master" \
                       "$HOME_DIR/Desktop/test/algp-master" \
                       "$HOME_DIR/Desktop/algp-master")" \
  || ALGP_DIR="$HOME_DIR/Desktop/school/algp-master"
SVOM_DIR="$(detect_dir "$HOME_DIR/Desktop/school/frontend" \
                       "$HOME_DIR/Desktop/test/frontend" \
                       "$HOME_DIR/Desktop/frontend")" \
  || SVOM_DIR="$HOME_DIR/Desktop/school/frontend"
HXMT_DIR="$(detect_dir "$HOME_DIR/Desktop/school/hxmt-worker" \
                       "$HOME_DIR/Desktop/test/hxmt-worker" \
                       "$HOME_DIR/Desktop/hxmt-worker")" \
  || HXMT_DIR="$HOME_DIR/Desktop/school/hxmt-worker"

RUN_DIR="$ROOT/.dev-stack"
LOG_DIR="$RUN_DIR/logs"
PID_DIR="$RUN_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

export JAVA_HOME="/opt/homebrew/opt/openjdk@17"
export PATH="$JAVA_HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_HOST="unix://$HOME_DIR/.colima/default/docker.sock"
export NO_PROXY="127.0.0.1,localhost,::1,0.0.0.0${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
# 设为 0 可跳过 ollama（省内存）
export STACK_OLLAMA="${STACK_OLLAMA:-1}"

P_BACKEND=8000
P_FRONTEND=5173
P_CADDY=80
P_ALGP=9037
P_SVOM=9084
P_HXMT=8096
APP_PORTS="$P_BACKEND $P_FRONTEND $P_CADDY $P_ALGP $P_SVOM $P_HXMT 9098 22222 20880 2019 8080"
SCREENS="aiops-backend aiops-frontend aiops-celery-worker aiops-celery-beat aiops-caddy demo-algp-backend demo-svom-frontend demo-hxmt-worker"

if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$*"; }
step() { printf '\n%s%s%s\n' "$B" "$*" "$N"; }

# ---------- 进程工具 ----------
kill_tree() { # kill_tree <pid> <SIG>
  local pid="$1" sig="${2:-TERM}" kids k
  [ -n "$pid" ] || return 0
  kids="$(pgrep -P "$pid" 2>/dev/null || true)"
  for k in $kids; do kill_tree "$k" "$sig"; done
  kill -"$sig" "$pid" 2>/dev/null || true
}

port_pids() { lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null || true; }

kill_ports() { # kill_ports <SIG>
  local sig="$1" pr p
  for pr in $APP_PORTS; do
    for p in $(port_pids "$pr"); do kill_tree "$p" "$sig"; done
  done
}

quit_screens() {
  local s
  for s in $SCREENS; do screen -S "$s" -X quit 2>/dev/null || true; done
}

wait_port() { # wait_port <port> <名称> [秒]
  local port="$1" name="$2" tries="${3:-60}" i=0
  while [ "$i" -lt "$tries" ]; do
    if nc -z 127.0.0.1 "$port" 2>/dev/null; then
      ok "$name 就绪 (:$port)"; return 0
    fi
    i=$((i+1)); sleep 1
  done
  warn "$name 等待超时 (:$port)，继续启动后续服务"
  return 1
}

wait_http() { # wait_http <url> <名称> [秒]
  local url="$1" name="$2" tries="${3:-90}" i=0
  while [ "$i" -lt "$tries" ]; do
    if curl -sf --noproxy '*' "$url" >/dev/null 2>&1; then
      ok "$name 就绪"; return 0
    fi
    i=$((i+1)); sleep 1
  done
  warn "$name 等待超时，继续启动后续服务"
  return 1
}

CHILDREN=""

start_service() { # start_service <名称> <目录> <命令> [环境变量前缀]
  local name="$1" dir="$2" cmd="$3" envs="${4:-}"
  local log="$LOG_DIR/$name.log" pid
  : > "$log"
  ( cd "$dir" && eval "$envs exec $cmd" ) >> "$log" 2>&1 &
  pid=$!
  CHILDREN="$CHILDREN $name:$pid"
  echo "$pid" > "$PID_DIR/$name.pid"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "$name 已启动"
  else
    bad "$name 启动即退出，请查看 $log"
  fi
}

# ---------- Docker ----------
ensure_algp_container() { # 仅在容器被删掉时重建
  local c="$1"
  case "$c" in
    algp-mysql)
      docker run -d --name algp-mysql --restart unless-stopped \
        -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=algp \
        -p 3307:3306 -v algp_mysql_data:/var/lib/mysql \
        mysql:8.0 --lower-case-table-names=1 \
        --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci >/dev/null 2>&1 ;;
    algp-rabbitmq)
      docker run -d --name algp-rabbitmq --restart unless-stopped \
        -e RABBITMQ_DEFAULT_USER=neu -e RABBITMQ_DEFAULT_PASS=123456 \
        -e RABBITMQ_DEFAULT_VHOST=/ \
        -p 5672:5672 -p 15672:15672 -v algp_rabbitmq_data:/var/lib/rabbitmq \
        rabbitmq:3-management-alpine >/dev/null 2>&1 ;;
  esac
}

start_docker() {
  step "[1/4] Docker 基础服务"
  if colima status >/dev/null 2>&1; then
    ok "colima 虚拟机已在运行"
  else
    say "  → 启动 colima 虚拟机 (2 CPU / 2GB)，约需 30-60 秒…"
    if colima start >/dev/null 2>&1; then ok "colima 已启动"; else bad "colima 启动失败"; return 1; fi
  fi
  if ! docker info >/dev/null 2>&1; then bad "Docker 不可用，跳过容器启动"; return 1; fi

  say "  → 启动 ai-ops-agent 中间件…"
  ( cd "$ROOT" && docker compose start >/dev/null 2>&1 ) || true
  local c
  for c in ai-ops-agent-kafka-1 ai-ops-agent-kafka-exporter-1 ai-ops-agent-mysqld-exporter-1; do
    docker start "$c" >/dev/null 2>&1 || true
  done
  ( cd "$ROOT" && docker compose up -d --no-recreate >/dev/null 2>&1 ) || true
  docker start ai-ops-agent-mysql-1 ai-ops-agent-redis-1 ai-ops-agent-prometheus-1 \
               ai-ops-agent-node-exporter-1 >/dev/null 2>&1 || true

  if [ "$STACK_OLLAMA" = "1" ]; then
    docker start ai-ops-agent-ollama-1 >/dev/null 2>&1 \
      || ( cd "$ROOT" && docker compose --profile llm up -d ollama >/dev/null 2>&1 ) || true
    ok "ollama 已启动（想省内存可设 STACK_OLLAMA=0 跳过）"
  else
    docker stop ai-ops-agent-ollama-1 >/dev/null 2>&1 || true
    say "  - 已按 STACK_OLLAMA=0 跳过 ollama"
  fi

  # 2026-09-22 起 dev 数据库统一 Postgres（backend .env 的 DATABASE_URL 指向 5432）
  say "  → 启动 Postgres 平台库…"
  if docker inspect deploy-platform-db-1 >/dev/null 2>&1; then
    docker start deploy-platform-db-1 >/dev/null 2>&1 || true
  else
    # deploy compose 对 api/worker 有必填变量，占位值仅为通过解析，只启动 platform-db
    ( cd "$ROOT" && JWT_SECRET=dev-placeholder ENCRYPTION_KEY=dev-placeholder \
      DEEPSEEK_API_KEY= FEISHU_APP_ID= FEISHU_APP_SECRET= \
      docker compose -f deploy/docker-compose.yml up -d platform-db >/dev/null 2>&1 ) || true
  fi
  ok "platform-db (pgvector) 已启动"

  say "  → 启动 algp 中间件 (mysql:3307 / rabbitmq:5672)…"
  for c in algp-mysql algp-rabbitmq; do
    if docker inspect "$c" >/dev/null 2>&1; then
      docker start "$c" >/dev/null 2>&1 || true
    else
      ensure_algp_container "$c"
    fi
  done
  ok "algp 中间件已启动"

  wait_port 3306 "aiops MySQL" 60
  wait_port 6379 "Redis" 60
  wait_port 5432 "Postgres 平台库" 90
  wait_port 3307 "algp MySQL" 90
}

stop_docker() {
  step "停止 Docker"
  ( cd "$ROOT" && docker compose stop >/dev/null 2>&1 ) \
    && ok "ai-ops-agent 容器已停止" || warn "ai-ops-agent 容器停止异常"
  docker stop deploy-platform-db-1 >/dev/null 2>&1 && ok "Postgres 平台库已停止" || true
  docker stop algp-mysql algp-rabbitmq >/dev/null 2>&1 \
    && ok "algp 容器已停止" || true
  if colima status >/dev/null 2>&1; then
    say "  → 停止 colima 虚拟机…"
    if colima stop >/dev/null 2>&1; then ok "colima 已停止，内存已释放"; else bad "colima 停止失败"; fi
  else
    say "  - colima 未运行"
  fi
}

# ---------- 应用 ----------
start_apps() {
  step "[2/4] ai-ops-agent 应用"
  local red_env="export CELERY_BROKER_URL='redis://127.0.0.1:6379/0' CELERY_RESULT_BACKEND='redis://127.0.0.1:6379/1';"

  start_service aiops-backend "$ROOT/backend" \
    ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $P_BACKEND --reload" "$red_env"
  wait_http "http://127.0.0.1:$P_BACKEND/healthz" "后端 API" 90

  start_service aiops-frontend "$ROOT/frontend" \
    "npm run dev -- --host 127.0.0.1 --port $P_FRONTEND"
  wait_port "$P_FRONTEND" "前端 Vite" 60

  start_service aiops-celery-worker "$ROOT/backend" \
    ".venv/bin/celery -A app.workers.celery.celery worker --loglevel=info --concurrency=2" "$red_env"
  start_service aiops-celery-beat "$ROOT/backend" \
    ".venv/bin/celery -A app.workers.celery.celery beat --loglevel=info" "$red_env"

  start_service aiops-caddy "$ROOT" "/opt/homebrew/bin/caddy run --config Caddyfile"
  wait_port "$P_CADDY" "Caddy 网关" 20
}

start_algp() {
  step "[3/4] algp + svom + hxmt"
  local jenv="export JAVA_HOME='$JAVA_HOME' PATH='$JAVA_HOME/bin:/opt/homebrew/bin:/usr/bin:/bin';"
  local ds="--spring.datasource.dynamic.datasource.master.url='jdbc:mysql://127.0.0.1:3307/algp?useUnicode=true&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&serverTimezone=GMT%2B8&allowPublicKeyRetrieval=true' --spring.datasource.dynamic.datasource.master.username=root --spring.datasource.dynamic.datasource.master.password=root"

  start_service algp-backend "$ALGP_DIR" \
    "java --add-opens=java.base/java.lang=ALL-UNNAMED -jar algp-web/target/algp.jar --spring.profiles.active=dev $ds" \
    "$jenv export AIOPS_BASE_URL='http://127.0.0.1:$P_BACKEND' AIOPS_SYSTEM_CODE='algp' AIOPS_TOKEN='sys_mN3FgcFf1CUbDG1GqUlQzx0ftvFHQdCdQprZM_gEiSU';"
  wait_port "$P_ALGP" "algp 后端" 150

  start_service hxmt-worker "$HXMT_DIR" \
    "java -jar target/worker.jar --spring.datasource.url='jdbc:mysql://127.0.0.1:3307/algp?useUnicode=true&characterEncoding=UTF-8&zeroDateTimeBehavior=CONVERT_TO_NULL&serverTimezone=Asia/Shanghai&autoReconnect=true&allowPublicKeyRetrieval=true' --spring.datasource.username=root --spring.datasource.password=root" \
    "$jenv export GENERAL_AIOPS_CALLBACK_URL='http://127.0.0.1:$P_ALGP/algp/system/message-center/internal/component-error';"
  wait_port "$P_HXMT" "hxmt-worker" 120

  start_service svom-frontend "$SVOM_DIR" \
    "npm run serve" "export NODE_OPTIONS='--openssl-legacy-provider';"
  wait_port "$P_SVOM" "svom 前端" 180
}

# ---------- launchd ----------
launchd_procs() {
  case "$1" in
    com.aiops.cloudflared) echo "cloudflared tunnel" ;;
    com.aiops.feishu-bot)  echo "feishu_bot/server.py" ;;
    *) echo "" ;;
  esac
}

start_launchd() {
  step "[4/4] launchd 常驻服务"
  local uid label plist pat
  uid="$(id -u)"
  for label in com.aiops.cloudflared com.aiops.feishu-bot; do
    plist="$HOME_DIR/Library/LaunchAgents/$label.plist"
    pat="$(launchd_procs "$label")"
    if [ ! -f "$plist" ]; then warn "$label.plist 不存在，跳过"; continue; fi
    if launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
      ok "$label 已加载"
    elif [ -n "$pat" ] && pgrep -f "$pat" >/dev/null 2>&1; then
      ok "$label 进程已在运行（非 launchd 托管）"
    elif launchctl bootstrap "gui/$uid" "$plist" >/dev/null 2>&1; then
      ok "$label 已启动"
    else
      warn "$label 启动失败（可稍后手动 launchctl bootstrap）"
    fi
  done
}

stop_launchd() {
  step "停止 launchd 常驻服务"
  local uid label pat
  uid="$(id -u)"
  for label in com.aiops.cloudflared com.aiops.feishu-bot; do
    pat="$(launchd_procs "$label")"
    if launchctl bootout "gui/$uid/$label" >/dev/null 2>&1; then
      ok "$label 已停止"
    else
      say "  - $label 未由 launchd 加载"
    fi
    if [ -n "$pat" ]; then pkill -f "$pat" >/dev/null 2>&1 && ok "$label 残留进程已清理" || true; fi
  done
}

# ---------- 公网域名（解析 cloudflared 隧道配置）----------
# 标签按显示宽度补齐到 13 列（中文字符占 2 列），保证域名对齐
padded_label() {
  case "$1" in
    80)   printf 'aiops 控制台 ' ;;
    8000) printf '后端 API     ' ;;
    5173) printf 'Vite 开发    ' ;;
    9037) printf 'algp 后端    ' ;;
    9084) printf 'svom 前端    ' ;;
    8096) printf 'hxmt-worker  ' ;;
    *)    printf '端口 %-6s  ' "$1" ;;
  esac
}

tunnel_links() { # 输出 "<本地端口> <域名>" 列表
  local cfg="$HOME_DIR/.cloudflared/config.yml"
  [ -f "$cfg" ] || return 0
  awk '
    /^[[:space:]]*-[[:space:]]*hostname:/ { h=$3 }
    /^[[:space:]]*service:[[:space:]]*http:\/\/localhost:/ {
      n=split($2,a,":"); if (h!="") printf "%s %s\n", a[n], h
    }
  ' "$cfg"
}

# 有些后端有 servlet context-path，根路径会 404，这里补上
link_path() {
  case "$1" in
    9037) printf '/algp' ;;   # Spring Boot: servlet.context-path=/algp
    *)    printf '' ;;
  esac
}

show_links() {
  local cad="http://localhost"
  [ "$P_CADDY" != "80" ] && cad="http://localhost:$P_CADDY"

  say ""
  say "${B}本地访问${N}"
  say "  前端控制台  $cad"
  say "  后端 API    http://127.0.0.1:$P_BACKEND/docs"
  say "  Vite 开发   http://127.0.0.1:$P_FRONTEND"

  local links; links="$(tunnel_links)"
  [ -n "$links" ] || return 0

  say ""
  say "${B}公网访问${N} ${D}(cloudflared 隧道)${N}"
  local order="80 8000 9037 9084 8096 5173" pr lp lh
  for pr in $order; do
    while read -r lp lh; do
      if [ "$lp" = "$pr" ]; then
        printf '  '; padded_label "$pr"; say "https://$lh$(link_path "$pr")"
      fi
    done <<EOF
$links
EOF
  done
  # 配置里新增、但还没登记的域名，兜底也列出来
  while read -r lp lh; do
    case " $order " in
      *" $lp "*) ;;
      *) printf '  '; padded_label "$lp"; say "https://$lh$(link_path "$lp")" ;;
    esac
  done <<EOF
$links
EOF
}

# ---------- status ----------
status() {
  say "${B}===== 服务状态 =====${N}"
  local pr name
  check() { # check <port> <名称>
    if nc -z 127.0.0.1 "$1" 2>/dev/null; then printf '  %s✓%s %-18s :%s\n' "$G" "$N" "$2" "$1"
    else printf '  %s✗%s %-18s :%s\n' "$R" "$N" "$2" "$1"; fi
  }
  check "$P_BACKEND"  "后端 API"
  check "$P_FRONTEND" "前端 Vite"
  check "$P_CADDY"    "Caddy 网关"
  check 5432          "Postgres 平台库"
  check "$P_ALGP"     "algp 后端"
  check "$P_SVOM"     "svom 前端"
  check "$P_HXMT"     "hxmt-worker"
  say ""
  say "${B}===== Docker =====${N}"
  if docker info >/dev/null 2>&1; then
    docker ps --format '  {{.Status}}  {{.Names}}' 2>/dev/null | head -20
  else
    say "  colima 未运行"
  fi
  say ""
  say "日志目录: $LOG_DIR"
}

# ---------- 停止 ----------
stop_apps() {
  step "停止应用进程"
  quit_screens
  local f p
  for f in "$PID_DIR"/*.pid; do
    [ -f "$f" ] || continue
    p="$(cat "$f" 2>/dev/null || true)"
    [ -n "$p" ] && kill_tree "$p" TERM
    rm -f "$f"
  done
  kill_ports TERM
  pkill -f "$ROOT/backend/.venv/bin/uvicorn" >/dev/null 2>&1 || true
  pkill -f "$ROOT/backend/.venv/bin/celery" >/dev/null 2>&1 || true
  pkill -f "$ROOT/frontend/node_modules/.bin/vite" >/dev/null 2>&1 || true
  pkill -f "algp-web/target/algp.jar" >/dev/null 2>&1 || true
  pkill -f "$SVOM_DIR/node_modules/@vue/cli-service" >/dev/null 2>&1 || true
  sleep 2
  kill_ports KILL
  sleep 1
  ok "应用进程已清理"
}

stop_all() {
  stop_launchd
  stop_apps
  stop_docker
}

# ---------- up ----------
preflight() {
  step "[0/4] 清理上次残留"
  quit_screens
  kill_ports TERM
  sleep 1
  kill_ports KILL
  rm -f "$PID_DIR"/*.pid 2>/dev/null || true
  ok "端口已清空"
}

CLEANUP_DONE=0
on_signal() {
  [ "$CLEANUP_DONE" = "1" ] && return
  CLEANUP_DONE=1
  trap '' INT TERM HUP
  say ""
  say "${Y}收到退出信号，正在停止全部服务（含 Docker 与 colima）…${N}"
  stop_all
  say ""
  say "${G}${B}全部已停止，内存已释放。${N}"
  exit 0
}

do_up() {
  trap on_signal INT TERM HUP
  say "${B}╔══════════════════════════════════════════╗${N}"
  say "${B}║      AIOps 本地开发栈 一键启动            ║${N}"
  say "${B}╚══════════════════════════════════════════╝${N}"
  preflight
  start_docker
  start_apps
  start_algp
  start_launchd

  status
  say ""
  say "${G}${B}全部服务已启动。${N}"
  show_links
  say ""
  say "${Y}${B}按 Ctrl+C 停止全部并退出${N}  ${D}(关闭窗口亦可)${N}"
  say ""

  local pair nm pd DEAD=""
  while true; do
    sleep 5
    for pair in $CHILDREN; do
      nm="${pair%%:*}"; pd="${pair##*:}"
      if ! kill -0 "$pd" 2>/dev/null; then
        case " $DEAD " in
          *" $nm "*) ;;
          *) DEAD="$DEAD $nm"
             say "${R}⚠ $nm 已退出${N} ${D}(日志: $LOG_DIR/$nm.log)${N}" ;;
        esac
      fi
    done
  done
}

do_down() {
  say "${B}停止 AIOps 开发栈${N}"
  stop_all
  say ""
  say "${G}${B}已全部停止。${N}"
}

do_logs() {
  local which="${1:-}"
  if [ -n "$which" ]; then
    tail -f "$LOG_DIR/$which.log"
  else
    tail -f "$LOG_DIR"/*.log
  fi
}

case "${1:-}" in
  up)     do_up ;;
  down)   do_down ;;
  status) status; show_links; say "" ;;
  logs)   shift; do_logs "${1:-}" ;;
  *)      say "用法: $(basename "$0") <up|down|status|logs [服务名]>" ; exit 1 ;;
esac
