#!/usr/bin/env bash
# 每日数据库备份入口（launchd / cron 均可调用）。
#
#   scripts/backup.sh         执行一次备份（默认保留 14 份）
#   scripts/backup.sh check   检查最近一次备份是否新鲜（>26h 视为过期，退出码 1）
#
# 日志：logs/backup.log；失败时退出码非 0，便于监控告警。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEEP="${BACKUP_KEEP:-14}"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/backup.log"
mkdir -p "$LOG_DIR"

MODE="${1:-run}"

if [ "$MODE" = "check" ]; then
  latest="$(ls -1d "$ROOT"/backups/*/ 2>/dev/null | grep -E '/[0-9]{8}-[0-9]{6}/$' | sort | tail -1 || true)"
  if [ -z "$latest" ]; then
    echo "NO_BACKUP" >&2
    exit 1
  fi
  stamp="$(basename "$latest")"
  # macOS(BSD date) 与 Linux(GNU date) 各有一套解析写法
  if ts=$(date -j -f "%Y%m%d-%H%M%S" "$stamp" +%s 2>/dev/null); then
    :
  else
    ts=$(date -d "${stamp:0:8} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}" +%s)
  fi
  age_h=$(( ( $(date +%s) - ts ) / 3600 ))
  if [ "$age_h" -gt 26 ]; then
    echo "STALE ${age_h}h $latest" >&2
    exit 1
  fi
  echo "OK ${age_h}h $latest"
  exit 0
fi

cd "$ROOT"
{
  echo "[$(date '+%F %T')] backup start (keep=$KEEP)"
  "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/backup_db.py" --keep "$KEEP"
  echo "[$(date '+%F %T')] backup ok"
} >>"$LOG" 2>&1
