"""从 seed_cases.jsonl 生成参数化变体，扩充数据集到 100+ 条。

原则：
  * 变体复用同故障机制的 ground_truth（根因机制一致，仅措辞/参数/诱饵轮换）
  * inject 参数轮换（maxmemory 值 / topic 名 / max_connections / 大表名 / 停掉的容器）
  * question 措辞轮换，难度 easy/medium/hard 循环
  * 约 25% 变体带 forbidden_conclusion 诱饵（保持抗幻觉考核）
  * id 全局唯一：{类型前缀}-v{seq}

用法: python3 evals/make_variants.py            # 追加变体到 seed_cases.jsonl
      python3 evals/make_variants.py --dry-run  # 只统计不写入
"""
import json
import sys
from pathlib import Path

DATASET = Path(__file__).resolve().parent / "datasets" / "seed_cases.jsonl"
CASES = {c["id"]: c for c in (json.loads(l) for l in open(DATASET, encoding="utf-8"))}
DIFF = ["easy", "medium", "hard"]

DECOY_POOL = {
    "service_unreachable": ["Redis 内存打满导致", "网络抖动导致", "DNS 解析失败导致", "客户端连接池耗尽"],
    "redis_memory": ["网络抖动导致", "客户端连接池耗尽", "大 key 集合导致", "持久化 AOF 刷盘阻塞"],
    "kafka_lag": ["Kafka broker 磁盘写满", "网络分区导致 broker 失联", "消息体过大被拒绝"],
    "mysql_connections": ["慢 SQL 占用连接", "Redis 缓存击穿导致", "数据库磁盘写满"],
    "slow_sql": ["连接数打满导致排队", "CPU 被其他进程抢占", "主从延迟导致"],
    "container_oom": ["Kafka 消费积压导致", "日志文件打满磁盘导致", "宿主机 CPU 不足"],
    "log_error_storm": ["磁盘快满导致 IO 阻塞", "MySQL 慢查询阻塞", "Redis 连接池耗尽"],
}


def build_variants():
    """返回变体列表（dict，字段与 seed 用例一致）。"""
    out = []
    n = [0]

    def mk(pid, question, difficulty, *, decoys=None, keys=None, root_cause=None,
           inject=None, teardown=None, prefix):
        parent = CASES[pid]
        gt = json.loads(json.dumps(parent["ground_truth"]))
        if keys is not None:
            gt["evidence_keys"] = keys
        if root_cause is not None:
            gt["root_cause"] = root_cause
        gt["forbidden_conclusion"] = decoys or []
        n[0] += 1
        out.append({
            "id": f"{prefix}-v{n[0]:03d}",
            "fault_type": parent["fault_type"],
            "difficulty": DIFF[len(out) % 3],
            "system_id": parent["system_id"],
            "question": question,
            "ground_truth": gt,
            "inject": inject or json.loads(json.dumps(parent["inject"])),
            "setup": parent.get("setup", []),
            "teardown": parent.get("teardown", []) if teardown is None else teardown,
        })

    def decoys_of(kind, want):
        return (DECOY_POOL[kind][:2] if want else None)

    # ============ service_unreachable：docker_stop × {mysql, redis, prometheus} × 3 问法 ============
    svc_targets = [
        ("ai-ops-agent-mysql-1", "svc-unreachable-002", None, None,
         "MySQL 容器被停止（正常退出，ExitCode=0，非数据文件损坏），服务不可达",
         ["3306", {"any_of": ["正常退出", "ExitCode=0", "收到关闭信号", "SIGTERM"]},
          {"any_of": ["连接被拒绝", "connection refused", "ECONNREFUSED", "不可达", "无法连接"]}]),
        ("ai-ops-agent-redis-1", "svc-unreachable-003", None, None,
         "Redis 容器被停止，6379 不再监听，依赖 Redis 的会话/缓存全部失效",
         ["6379", {"any_of": ["连接被拒绝", "connection refused", "ECONNREFUSED", "不可达", "exited", "已停止"]}]),
        ("ai-ops-agent-prometheus-1", "svc-unreachable-005", None, None,
         "Prometheus 容器被停止，9090 不再服务，监控面板无数据、告警链路中断",
         ["9090", {"any_of": ["未运行", "不可达", "未启动", "down", "无法连接", "挂", "connection refused"]}]),
    ]
    svc_questions = [
        "这个服务突然连不上了，一堆请求超时，快帮我看看怎么回事",
        "监控面板一直在报目标 down，是不是有人动了我的配置？",
        "客户反馈接口全挂了，平台也没收到告警，帮我查一下",
    ]
    for ti, (target, parent, _slot1, _slot2, root_cause, keys) in enumerate(svc_targets):
        for qi, question in enumerate(svc_questions):
            mk(parent, question,
               difficulty=DIFF[qi % 3],
               decoys=DECOY_POOL["service_unreachable"][:2] if (ti * 3 + qi) % 5 == 0 else None,
               keys=keys, root_cause=root_cause,
               inject={"action": "docker_stop", "target": target, "command": ""},
               teardown=[f"docker start {target}"],
               prefix=f"svc-unreachable-{['mysql','redis','prom'][ti]}")

    # ============ redis_memory：maxmemory 值 × 5 问法 ============
    redis_questions = [
        "读写接口开始报 Redis 相关错误，是不是缓存出问题了？",
        "缓存命中率明显下滑，你们查查 Redis 是不是撑不住了",
        "大量 key 好像凭空消失，帮我确认是不是被异常清除了",
        "用户会话频繁失效，怀疑 Redis 出了状况",
        "大批量写入突然失败，报警提到 command not allowed",
    ]
    qi = 0
    for mb in [6, 12, 16, 20, 4]:
        for question in redis_questions[:3]:
            decoys = DECOY_POOL["redis_memory"][:2] if qi % 5 == 0 else None
            mk("redis-memory-001", question,
               difficulty=DIFF[qi % 3],
               decoys=decoys,
               inject={"action": "docker_exec", "target": "ai-ops-agent-redis-1",
                       "command": f"redis-cli CONFIG SET maxmemory {mb}mb"},
               teardown=["redis-cli CONFIG SET maxmemory 0"],
               prefix="redis-memory")
            qi += 1

    # ============ kafka_lag：topic 名 × 5 问法 ============
    kafka_questions = [
        "支付回调消息迟迟没处理，是不是消费那边卡住了？",
        "消息链路延迟越来越大，看下 Kafka 是不是跟不上生产速度",
        "数据同步任务一直空转，确认下上游消息到底消费了没有",
        "报表数字和实时库对不上，怀疑有消息没被消费就丢了",
        "数仓半天没新数据进来，检查一下是不是管道堵了",
    ]
    qi = 0
    for topic in ["payment-events", "user-tracks", "inventory-sync", "click-stream", "sensor-data"]:
        for question in kafka_questions[:3]:
            decoys = DECOY_POOL["kafka_lag"][:2] if qi % 5 == 1 else None
            inject_cmd = (
                "/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists "
                f"--topic {topic} --partitions 3 --replication-factor 1; "
                f"seq 1 5000 | sed 's/^/evt-/' | /opt/kafka/bin/kafka-console-producer.sh "
                f"--bootstrap-server localhost:9092 --topic {topic}"
            )
            mk("kafka-lag-001", question,
               difficulty=DIFF[qi % 3],
               decoys=decoys,
               inject={"action": "docker_exec", "target": "ai-ops-agent-kafka-1", "command": inject_cmd},
               teardown=[f"/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --delete --topic {topic}"],
               prefix="kafka-lag")
            qi += 1

    # ============ mysql_connections：max_connections 值 × 4 问法 ============
    mysql_questions = [
        "高峰期系统开始拒绝服务，后端日志显示拿不到数据库连接",
        "用户反馈登录总失败，连接池一直报获取超时",
        "巡检发现数据库连接数异常，帮我看下是不是要炸了",
    ]
    qi = 0
    for limit in [8, 12, 6, 5]:
        for question in mysql_questions[:3]:
            decoys = DECOY_POOL["mysql_connections"][:2] if qi % 5 == 0 else None
            mk("mysql-connections-001", question,
               difficulty=DIFF[qi % 3],
               decoys=decoys,
               inject={"action": "docker_exec", "target": "ai-ops-agent-mysql-1",
                       "command": f'mysql -uroot -prootpass -e "SET GLOBAL max_connections={limit};"'},
               teardown=['mysql -uroot -prootpass -e "SET GLOBAL max_connections=151;"'],
               prefix="mysql-connections")
            qi += 1

    # ============ slow_sql：大表名 × 4 问法 ============
    slow_sql_questions = [
        "订单相关页面现在 10 秒才出结果，以前秒开，查查为什么",
        "报表接口时不时超时，DBA 说没有死锁，那问题在哪",
        "最近 API 的 p99 涨了 10 倍，怀疑数据库拖慢的，帮忙确认",
    ]
    qi = 0
    for table in ["eval_payments_big", "eval_users_big", "eval_inventory_big", "eval_logs_big"]:
        for question in slow_sql_questions[:3]:
            decoys = DECOY_POOL["slow_sql"][:2] if qi % 5 == 0 else None
            mk("slow-sql-001", question,
               difficulty=DIFF[qi % 3],
               decoys=decoys,
               inject={"action": "docker_exec", "target": "ai-ops-agent-mysql-1",
                       "command": slow_sql_cmd(table)},
               teardown=[
                   'mysql -uroot -prootpass -e "SET GLOBAL slow_query_log=OFF;"',
                   f'mysql -uroot -prootpass opsdb -e "DROP TABLE IF EXISTS {table};"',
               ],
               prefix="slow-sql")
            qi += 1

    # ============ container_oom（manual）：3 问法 × 参数叙述变体 ============
    oom_questions = [
        "有个容器老是自己重启，是不是内存不够被系统杀了？",
        "服务半夜挂了一次，没人操作过，帮我查查死因",
        "报警说服务重启了，我怀疑是 OOM，帮我确认下",
    ]
    for i, question in enumerate(oom_questions * 4):
        mk("container-oom-001" if i % 3 == 0 else ("container-oom-002" if i % 3 == 1 else "container-oom-003"),
           question,
           difficulty=DIFF[i % 3],
           decoys=DECOY_POOL["container_oom"][:2] if i % 5 == 0 else None,
           prefix="container-oom")

    # ============ log_error_storm（manual）：3 问法 × 参数叙述变体 ============
    storm_questions = [
        "日志里全是同一个报错刷屏，服务快要被拖垮了，帮忙看看源头",
        "接口错误率突然飙升，日志量暴涨，同事怀疑是下游故障",
        "客户端一直在重试失败，日志中出现大量超时堆栈，查一下根因",
    ]
    for i, question in enumerate(storm_questions * 4):
        mk("log-error-storm-001" if i % 3 == 0 else ("log-error-storm-002" if i % 3 == 1 else "log-error-storm-003"),
           question,
           difficulty=DIFF[i % 3],
           decoys=DECOY_POOL["log_error_storm"][:2] if i % 5 == 1 else None,
           prefix="log-error-storm")

    return out


def slow_sql_cmd(table):
    return (
        f'mysql -uroot -prootpass opsdb -e "CREATE TABLE IF NOT EXISTS {table} '
        '(id INT PRIMARY KEY AUTO_INCREMENT, user_id INT, amount DECIMAL(10,2), created_at DATETIME, remark VARCHAR(255));" ; '
        f'mysql -uroot -prootpass opsdb -e "INSERT INTO {table} (user_id, amount, created_at, remark) '
        "SELECT (a.n+b.n*10+c.n*100) % 5000, a.n*0.5, NOW(), CONCAT('eval-', a.n, b.n, c.n) "
        "FROM (SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 "
        "UNION SELECT 7 UNION SELECT 8 UNION SELECT 9 UNION SELECT 10) a, "
        "(SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 "
        "UNION SELECT 7 UNION SELECT 8 UNION SELECT 9 UNION SELECT 10) b, "
        "(SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 "
        "UNION SELECT 7 UNION SELECT 8 UNION SELECT 9 UNION SELECT 10) c;\" ; "
        'mysql -uroot -prootpass -e "SET GLOBAL slow_query_log=ON; SET GLOBAL long_query_time=1;"'
    )


def main():
    dry = "--dry-run" in sys.argv
    existing = [json.loads(l) for l in open(DATASET, encoding="utf-8") if l.strip()]
    existing_ids = {c["id"] for c in existing}
    variants = build_variants()
    # 去重 id
    deduped, seen = [], set(existing_ids)
    for v in variants:
        if v["id"] in seen:
            continue
        seen.add(v["id"])
        deduped.append(v)
    print(f"现有 {len(existing)} 条，将追加 {len(deduped)} 条变体，共 {len(existing) + len(deduped)} 条")
    from collections import Counter
    print("追加分布:", dict(Counter(v["fault_type"] for v in deduped)))
    print("诱饵占比: ", f"{100 * sum(1 for v in deduped if v['ground_truth']['forbidden_conclusion']) / max(1, len(deduped)):.0f}%")
    if dry:
        for v in deduped[:6]:
            print("  样例:", v["id"], "|", v["question"][:40])
        return
    with open(DATASET, "a", encoding="utf-8") as f:
        for v in deduped:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print("已写入", DATASET)


if __name__ == "__main__":
    main()
