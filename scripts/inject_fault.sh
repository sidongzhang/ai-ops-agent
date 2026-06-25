#!/usr/bin/env bash
# 故障注入脚本 —— 多种故障类型模拟
# 用法: ./scripts/inject_fault.sh <故障类型>
#       ./scripts/inject_fault.sh --list   查看所有故障类型

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"
source .env 2>/dev/null
export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"

# ── 工具函数 ──────────────────────────────────────────────────────────────────
kill_service() {
    local name=$1
    local pid_file="pids/$name.pid"
    if [ ! -f "$pid_file" ]; then
        echo "  ⚠️  找不到 $name 的 PID 文件（可能未在运行）"
        return 1
    fi
    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        rm -f "$pid_file"
        echo "  💥 已停止 $name (PID $pid)"
    else
        echo "  ⚠️  $name 已经不在运行 (PID $pid)"
        rm -f "$pid_file"
    fi
}

print_hint() {
    echo ""
    echo "👉 让 Agent 来检测并修复："
    echo "   飞书发消息：系统有问题，帮我检查一下"
    echo "   命令行：cd agent && python3 agent.py \"系统有问题，帮我检查一下\""
}

# ── 故障列表 ──────────────────────────────────────────────────────────────────
if [ "$1" = "--list" ] || [ -z "$1" ]; then
    echo "=========================="
    echo "  故障注入类型一览"
    echo "=========================="
    echo ""
    echo "【进程级故障】"
    echo "  producer    停止 Producer 进程（数据停止生产）"
    echo "  consumer    停止 Consumer 进程（数据停止写入 MySQL）"
    echo "  frontend    停止 Frontend 进程（面板无法访问）"
    echo "  all         同时停止全部三个 Python 服务"
    echo ""
    echo "【基础设施故障】"
    echo "  kafka       停止 Kafka 容器（消息队列不可用）"
    echo "  mysql       停止 MySQL 容器（数据库不可用）"
    echo ""
    echo "【数据层故障】"
    echo "  db-table    删除 sensor_data 表（数据丢失）"
    echo "  bad-data    向 Kafka 注入 20 条乱码消息（Consumer 解析失败）"
    echo ""
    echo "【资源故障】"
    echo "  cpu         后台启动 CPU 密集进程（CPU 飙高，持续 2 分钟）"
    echo "  log-flood   向日志写入大量垃圾（磁盘压力 / 干扰 Agent 分析）"
    echo ""
    echo "用法: ./scripts/inject_fault.sh <故障类型>"
    exit 0
fi

FAULT=$1
echo "💥 注入故障: $FAULT"
echo "================================"

case $FAULT in

# ── 进程级故障 ────────────────────────────────────────────────────────────────
producer)
    kill_service producer
    echo "📌 现象：Kafka 中不再有新消息，Consumer/Frontend 数据停止增长"
    print_hint
    ;;

consumer)
    kill_service consumer
    echo "📌 现象：MySQL 数据条数不再增长，Frontend 页面数字冻结"
    print_hint
    ;;

frontend)
    kill_service frontend
    echo "📌 现象：访问 http://localhost:5001 返回 Connection Refused"
    print_hint
    ;;

all)
    echo "☠️  同时停止所有 Python 业务服务..."
    kill_service producer
    kill_service consumer
    kill_service frontend
    echo "📌 现象：全链路中断，面板无法访问，数据完全停止写入"
    print_hint
    ;;

# ── 基础设施故障 ──────────────────────────────────────────────────────────────
kafka)
    echo "🔴 停止 Kafka 容器..."
    docker compose stop kafka
    echo "📌 现象：Producer 报 NoBrokersAvailable 并不断重试，Consumer 也失去连接"
    echo "💡 恢复：docker compose start kafka"
    print_hint
    ;;

mysql)
    echo "🔴 停止 MySQL 容器..."
    docker compose stop mysql
    echo "📌 现象：Consumer 写入报 Can't connect to MySQL，Frontend 查询失败"
    echo "💡 恢复：docker compose start mysql"
    print_hint
    ;;

# ── 数据层故障 ────────────────────────────────────────────────────────────────
db-table)
    echo "🗑️  删除 sensor_data 表..."
    python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import mysql.connector
try:
    conn = mysql.connector.connect(
        host=os.getenv('MYSQL_HOST', 'localhost'),
        user=os.getenv('MYSQL_USER', 'opsuser'),
        password=os.getenv('MYSQL_PASSWORD', 'opspass'),
        database=os.getenv('MYSQL_DB', 'opsdb'),
    )
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS sensor_data")
    conn.commit()
    cur.close()
    conn.close()
    print("  ✅ sensor_data 表已删除")
except Exception as e:
    print(f"  ❌ 操作失败: {e}")
    sys.exit(1)
PYEOF
    echo "📌 现象：Consumer 日志报 Table 'opsdb.sensor_data' doesn't exist，写入全部失败"
    print_hint
    ;;

bad-data)
    echo "🦠 向 Kafka 注入 20 条异常消息..."
    python3 - <<PYEOF
import os, sys, json, random, string
broker = os.getenv('KAFKA_BROKER', '${KAFKA_BROKER:-localhost:9092}')
try:
    from kafka import KafkaProducer
    producer = KafkaProducer(bootstrap_servers=broker)
    for i in range(20):
        t = i % 3
        if t == 0:
            # 纯乱码，无法 JSON 解析
            msg = (''.join(random.choices(string.ascii_letters + '{}[]!@#', k=80))).encode()
        elif t == 1:
            # 合法 JSON 但缺少必要字段
            msg = json.dumps({"garbage": True, "index": i}).encode()
        else:
            # 字段类型错误
            msg = json.dumps({"sensor": None, "value": "not_a_number", "unit": 9999}).encode()
        producer.send('sensor-data', msg)
    producer.flush()
    producer.close()
    print(f"  ✅ 已发送 20 条异常消息（乱码 / 缺字段 / 类型错误 各约 1/3）")
except Exception as e:
    print(f"  ❌ 发送失败: {e}")
    sys.exit(1)
PYEOF
    echo "📌 现象：Consumer 日志大量报错，部分消息被跳过，数据写入速率下降"
    print_hint
    ;;

# ── 资源故障 ──────────────────────────────────────────────────────────────────
cpu)
    echo "🔥 启动 CPU 密集进程（4 线程，持续 2 分钟）..."
    python3 - <<'PYEOF' &
import time, threading

def burn():
    deadline = time.time() + 120
    while time.time() < deadline:
        _ = sum(x * x for x in range(50000))

threads = [threading.Thread(target=burn, daemon=True) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
PYEOF
    HOG_PID=$!
    mkdir -p pids
    echo $HOG_PID > pids/cpu_hog.pid
    echo "  ✅ CPU 压力进程已启动 (PID $HOG_PID)"
    echo "📌 现象：CPU 使用率飙高，Prometheus node-exporter 的 CPU idle 指标下降"
    echo "💡 提前停止：kill \$(cat pids/cpu_hog.pid) && rm pids/cpu_hog.pid"
    print_hint
    ;;

log-flood)
    echo "📝 向 producer 日志写入大量垃圾数据..."
    mkdir -p logs
    python3 - <<'PYEOF'
line = "2026-01-01 00:00:00 [PRODUCER] ERROR " + "Connection timeout, retrying... " * 6 + "\n"
count = 50000
with open("logs/producer.log", "a") as f:
    for _ in range(count):
        f.write(line)
size_mb = (len(line) * count) / 1024 / 1024
print(f"  ✅ 已追加 {count} 行垃圾日志（约 {size_mb:.1f} MB）")
PYEOF
    echo "📌 现象：read_logs 工具返回大量 ERROR 行，Agent 需要从噪音中找到真正问题"
    echo "💡 清理：echo '' > logs/producer.log"
    print_hint
    ;;

*)
    echo "❌ 未知故障类型: $FAULT"
    echo "   运行 ./scripts/inject_fault.sh --list 查看所有类型"
    exit 1
    ;;
esac
