"""评测运行时：sys.path 引导、RAG/剧本消融、本地 remote_command 适配器、dry-run mock。

这里不 import 任何 `app.*`，所有业务模块都在函数内部按需 import：
  1. `--validate` 在缺第三方依赖的机器上也能跑；
  2. `--dry-run` 不应为了「离线冒烟」而把 pydantic-ai 的运行时副作用带进来（除非确实要跑真实 Agent）。
"""
from __future__ import annotations

import os
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(EVALS_DIR) not in sys.path:  # 让 `import harness` 在没调 bootstrap() 时也能工作
    sys.path.insert(0, str(EVALS_DIR))

from dataset import DEFAULT_DESCRIPTOR_DIR, DEFAULT_DOCS_ROOT, EVAL_SYSTEM_ID  # noqa: E402

ABLATION_GROUPS = ("baseline", "playbook", "rag", "full")
ABLATION_DESCRIPTIONS = {
    "baseline": "裸 Agent：skill_steps=\"\"、knowledge_context=\"\"、data_catalog=\"\"",
    "playbook": "仅 YAML 剧本：skill_steps=match_skill(question).steps",
    "rag": "仅知识库：knowledge_context=get_relevant_context(question, system_id)",
    "full": "线上默认行为：剧本 + RAG",
}

_BOOTSTRAPPED = False
_PROXY_NOTES: list[str] = []


def bootstrap() -> dict:
    """把 backend/ 与 evals/ 加进 sys.path，并修掉会炸 httpx 的代理环境变量。

    必须是任何 `app.*` import 之前的第一个调用。
    """
    global _BOOTSTRAPPED
    for path in (str(BACKEND_DIR), str(EVALS_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    notes = sanitize_proxy_env()
    _BOOTSTRAPPED = True
    return {"backend_dir": str(BACKEND_DIR), "evals_dir": str(EVALS_DIR), "proxy_notes": notes}


def sanitize_proxy_env() -> list[str]:
    """规范化 NO_PROXY / no_proxy 里 `[::1]` 这种带方括号的 IPv6 写法。

    背景（实测于本仓库开发机）：外层 shell 常导出
      NO_PROXY=localhost,127.0.0.1,::1,[::1]
    而 httpx 会把每个条目交给 `URLPattern()` 解析，`[::1]` 直接抛
      `httpx.InvalidURL: Invalid port: ':1]'`。
    由于 `app/agent/diagnostics/__init__.py` 在 import 时就会 `from .runner import diagnose`
    并构造 pydantic-ai 客户端，这个异常会让**任何** `--dry-run/--run` 直接崩溃。
    我们只在评测进程里把 `[::1]` 归一成 `::1`（backend/app/core/config.py 自己也是写 `::1`），
    不改任何业务代码，也不影响 HTTP_PROXY 的可用性。
    """
    notes: list[str] = []
    for var in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(var)
        if not current:
            continue
        parts = [part.strip() for part in current.split(",") if part.strip()]
        cleaned = [part[1:-1] if part.startswith("[") and part.endswith("]") else part for part in parts]
        if cleaned != parts:
            os.environ[var] = ",".join(cleaned)
            notes.append(f"{var}: 去除 IPv6 方括号写法 -> {os.environ[var]}")
    if notes and not _PROXY_NOTES:
        _PROXY_NOTES.extend(notes)
    return notes


def proxy_notes() -> list[str]:
    """本次进程内做过的代理环境修正（写进报告 meta）。"""
    return list(_PROXY_NOTES)


# ── 知识库 docs 根目录重定向 ────────────────────────────────────────────────

def patch_knowledge_docs_root(docs_root: str | Path | None = None, *, keyword_only: bool = False):
    """把 store.DOCS_ROOT 指到 evals/knowledge/docs，并清空索引缓存。

    `keyword_only=True` 时同时把 `store._embed` 打桩为返回 None，
    强制走纯关键词检索——dry-run 必须完全不碰网络。
    """
    from app.agent.diagnostics.knowledge import store

    root = Path(docs_root) if docs_root else DEFAULT_DOCS_ROOT
    store.DOCS_ROOT = root
    # pgvector 版 store：指纹缓存；keyword_only 时还会把 _embed 打桩为 None（自动走关键词降级）
    store._fingerprint_cache.clear()
    if keyword_only:
        store._embed = lambda texts: None  # type: ignore[assignment]
    return store


def docs_root_info(docs_root: str | Path | None = None) -> dict:
    root = Path(docs_root) if docs_root else DEFAULT_DOCS_ROOT
    system_dir = root / EVAL_SYSTEM_ID
    docs = sorted(path.name for path in system_dir.glob("*.md")) if system_dir.exists() else []
    return {"docs_root": str(root), "system_id": EVAL_SYSTEM_ID, "docs": docs, "doc_count": len(docs)}


# ── 消融组参数解析 ─────────────────────────────────────────────────────────

def resolve_ablation(
    case: dict,
    group: str,
    *,
    descriptor: dict | None = None,
    docs_root: str | Path | None = None,
    keyword_only_rag: bool = True,
) -> dict:
    """把消融组翻译成 `diagnose_with_details` 的入参（不改业务代码）。

    注意：`skill_steps=""` 而不是 None —— runner 里只有 None 才会自动走 get_skill_steps，
    所以空串才代表「裸 Agent」。
    """
    if group not in ABLATION_GROUPS:
        raise ValueError(f"未知消融组 {group!r}，可选 {ABLATION_GROUPS}")
    question = case.get("question", "")
    system_id = (descriptor or {}).get("id") or case.get("system_id") or EVAL_SYSTEM_ID

    skill_steps = ""
    skill_name = ""
    knowledge_context = ""

    if group in ("playbook", "full"):
        from app.agent.diagnostics.skill_router import match_skill

        skill = match_skill(question)
        if skill:
            skill_steps = skill.get("steps", "") or ""
            skill_name = skill.get("name", "")

    if group in ("rag", "full"):
        patch_knowledge_docs_root(docs_root, keyword_only=keyword_only_rag)
        from app.agent.diagnostics.knowledge.store import get_relevant_context

        try:
            knowledge_context = get_relevant_context(question, system_id) or ""
        except Exception as exc:  # noqa: BLE001 - RAG 失败不应中断评测
            knowledge_context = ""
            skill_name = skill_name or ""
            print(f"[warn] RAG 检索失败，按空上下文继续: {exc}", file=sys.stderr)

    return {
        "ablation": group,
        "skill_steps": skill_steps,
        "skill_name": skill_name,
        "knowledge_context": knowledge_context,
        "data_catalog": "",
        "context_chars": len(knowledge_context),
        "skill_steps_chars": len(skill_steps),
    }


# ── 本地 remote_command 适配器 ─────────────────────────────────────────────

def _find_service(descriptor: dict, name: str) -> dict | None:
    if not name:
        return None
    return next((svc for svc in descriptor.get("services", []) if svc.get("name") == name), None)


def _container_of(service: dict) -> str:
    config = service.get("config") or {}
    runtime = service.get("runtime") or {}
    return str(
        service.get("container") or config.get("container") or runtime.get("container") or ""
    ).strip()


#: fixture 里这些字段的相对路径按「仓库根」解析。
#: 背景：shared/connectors/local.py 的 `_abspath()` 以 `shared/` 目录为基准，
#: 而评测 fixture 写的是 `evals/fixtures/logs/xxx.log`（仓库根相对），
#: 所以评测侧先把它们规范成绝对路径，避免日志被解析到 `shared/evals/...`。
PATH_FIELDS = ("log_file", "pid_file", "log_dir")


def _resolve_repo_path(value: str) -> str:
    if not value or os.path.isabs(value):
        return value
    return str((REPO_ROOT / value).resolve())


def prepare_descriptor(descriptor: dict) -> dict:
    """把 fixture 里的仓库根相对路径改写为绝对路径（就地修改并返回）。"""
    for service in descriptor.get("services", []) or []:
        config = service.setdefault("config", {})
        for field_name in PATH_FIELDS:
            for holder in (service, config):
                value = holder.get(field_name)
                if isinstance(value, str) and value:
                    holder[field_name] = _resolve_repo_path(value)
    return descriptor


def _docker_ok() -> bool:
    import importlib

    module = importlib.import_module("faults")
    return bool(module.docker_available())


def capability_blocker(service: dict) -> str:
    connector = (service.get("connector") or "").strip()
    kind = (service.get("kind") or "").strip()
    container = _container_of(service)
    name = service.get("name", "")
    if connector == "ssh":
        return f"离线评测不支持：服务 {name} 走 ssh 连接器，评测进程无法登录远端主机，请人工确认"
    if connector == "k8s":
        return f"离线评测不支持：服务 {name} 走 k8s 连接器，需要集群凭据"
    if connector == "local" and (container or kind == "docker"):
        if not _docker_ok():
            return (
                f"离线评测不支持：服务 {name} 的日志/状态来自 Docker 容器 "
                f"{container or '(未命名)'}，当前 Docker/Colima 未运行"
            )
    return ""


def make_remote_command(descriptor: dict, *, echo: Callable[[str], None] | None = None) -> Callable[[str, dict], dict]:
    """构造符合 `AgentDeps.remote_command` 契约的本机执行适配器。

    语义与 `collector/ws_client.py::_handle_command` 对齐，但执行体换成本机：
      * health_check / fetch_logs / search_logs → shared/connectors 的真实探活与日志读取
      * query_prometheus → 直连 fixture 里配置的 Prometheus
      * run_redis_command → 裸 socket 发 RESP（只读命令白名单）
      * run_kafka_command → docker exec 进 Kafka 容器（需要 Docker）
    做不到的一律返回 `{"ok": False, "result": "离线评测不支持：..."}`，
    让 Agent 自己降级——降级行为本身也是评测对象（见 metrics.tool_degraded_rate）。
    """
    from app.services.descriptors.runtime import get_connector

    prepare_descriptor(descriptor)

    def _note(message: str) -> None:
        if echo:
            echo(f"[remote_command] {message}")

    def execute(command: str, args: dict) -> dict:
        args = args or {}
        try:
            if command == "health_check":
                service_name = str(args.get("service", "") or "")
                results = []
                for service in descriptor.get("services", []):
                    if service_name and service.get("name") != service_name:
                        continue
                    try:
                        ok, detail = get_connector(service, descriptor).health()
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, f"探活异常: {exc}"
                    results.append({"name": service.get("name", ""), "ok": ok, "detail": detail})
                return {"ok": True, "result": results}

            if command in ("fetch_logs", "search_logs"):
                service_name = str(args.get("service", "") or "")
                service = _find_service(descriptor, service_name)
                if not service:
                    return {"ok": False, "result": f"服务「{service_name}」未注册"}
                blocker = capability_blocker(service)
                if blocker:
                    return {"ok": False, "result": blocker}
                connector = get_connector(service, descriptor)
                if command == "fetch_logs":
                    lines = int(args.get("lines", 50) or 50)
                    return {"ok": True, "result": connector.read_logs(lines)}
                keyword = str(args.get("keyword", "ERROR") or "ERROR")
                lines = int(args.get("lines", 200) or 200)
                return {"ok": True, "result": connector.search_logs(keyword, lines)}

            if command == "query_prometheus":
                promql = str(args.get("query", "up") or "up")[:500]
                service = _find_service(descriptor, str(args.get("service", "") or ""))
                if not service or (service.get("connector") != "prometheus"):
                    return {"ok": False, "result": "未找到 Prometheus 服务（connector=prometheus）"}
                url = str(
                    service.get("url")
                    or (service.get("config") or {}).get("url")
                    or (descriptor.get("infra") or {}).get("prometheus_url")
                    or ""
                ).rstrip("/")
                if not url:
                    return {"ok": False, "result": "Prometheus 未配置地址"}
                import httpx

                try:
                    response = httpx.get(f"{url}/api/v1/query", params={"query": promql}, timeout=10)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "result": f"Prometheus 查询失败: {exc}"}
                if payload.get("status") != "success":
                    return {"ok": False, "result": payload.get("error", "Prometheus 查询失败")}
                return {"ok": True, "result": payload.get("data", {}).get("result", [])[:50]}

            if command == "run_redis_command":
                raw = str(args.get("command", "") or "").strip()
                parts = raw.split()
                first = parts[0].upper() if parts else ""
                allowed = {
                    "INFO", "DBSIZE", "CLIENT", "CONFIG", "SLOWLOG", "KEYS", "TTL",
                    "TYPE", "LLEN", "SCARD", "ZCARD", "HLEN", "STRLEN", "OBJECT",
                }
                if first not in allowed:
                    return {"ok": False, "result": f"安全限制：拒绝执行 Redis 命令「{first}」"}
                second = parts[1].upper() if len(parts) > 1 else ""
                readonly_subcommands = {
                    "CLIENT": {"LIST", "INFO"},
                    "CONFIG": {"GET"},
                    "SLOWLOG": {"GET", "LEN"},
                }
                if first in readonly_subcommands and second not in readonly_subcommands[first]:
                    return {"ok": False, "result": f"安全限制：Redis {first} 仅允许只读子命令"}
                service = _find_service(descriptor, str(args.get("service", "") or ""))
                if not service:
                    service = next(
                        (
                            item
                            for item in descriptor.get("services", [])
                            if item.get("connector") == "tcp"
                            and int((item.get("config") or {}).get("port", item.get("port", 0)) or 0) == 6379
                        ),
                        None,
                    )
                if not service:
                    return {"ok": False, "result": "未找到已注册 Redis 服务"}
                config = service.get("config") or {}
                host = service.get("host") or config.get("host", "127.0.0.1")
                port = int(service.get("port") or config.get("port", 6379))
                import socket

                payload = f"*{len(parts)}\r\n" + "".join(f"${len(part)}\r\n{part}\r\n" for part in parts)
                try:
                    with socket.create_connection((host, port), timeout=5) as conn:
                        conn.sendall(payload.encode())
                        chunks = []
                        while True:
                            chunk = conn.recv(65536)
                            if not chunk:
                                break
                            chunks.append(chunk)
                            if len(chunk) < 65536:
                                break
                    text = b"".join(chunks).decode("utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "result": f"Redis 命令执行失败: {exc}"}
                return {"ok": True, "result": text[:5000] or "(无响应)"}

            if command == "run_kafka_command":
                import shlex
                import subprocess

                raw = str(args.get("command", "") or "")
                parts = shlex.split(raw)
                if not parts or parts[0] not in ("topics", "consumer-groups"):
                    return {"ok": False, "result": "安全限制：只允许 topics 或 consumer-groups 子命令"}
                dangerous = {"--create", "--delete", "--alter", "--reset-offsets", "--execute"}
                if any(part in dangerous for part in parts):
                    return {"ok": False, "result": "安全限制：拒绝 Kafka 写操作"}
                service = _find_service(descriptor, str(args.get("service", "") or ""))
                if not service:
                    service = next(
                        (item for item in descriptor.get("services", []) if "kafka" in item.get("name", "").lower()),
                        None,
                    )
                container = _container_of(service or {})
                if not container:
                    return {"ok": False, "result": "Kafka 服务未配置可执行的 Docker 容器"}
                if not _docker_ok():
                    return {
                        "ok": False,
                        "result": f"离线评测不支持：run_kafka_command 需要 docker exec {container}，当前 Docker/Colima 未运行",
                    }
                script = f"kafka-{parts[0]}.sh"
                cmd = ["docker", "exec", container, f"/opt/kafka/bin/{script}", "--bootstrap-server", "localhost:9092", *parts[1:]]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "result": f"Kafka 命令执行失败: {exc}"}
                output = (result.stdout + result.stderr).strip()
                return {"ok": result.returncode == 0, "result": output[:5000] or "(无输出)"}

            return {"ok": False, "result": f"离线评测不支持命令 {command}"}
        except Exception as exc:  # noqa: BLE001 - 适配器任何异常都降级成 ok=False
            _note(f"{command} 执行异常: {exc}")
            return {"ok": False, "result": f"离线评测执行失败: {exc}"}

    return execute


# ── dry-run mock ──────────────────────────────────────────────────────────

def _stable_jitter(case_id: str, span: int = 120) -> int:
    if span <= 0:
        return 0
    return zlib.crc32(case_id.encode("utf-8")) % span


def mock_diagnose(case: dict, params: dict, events: list[dict], model_name: str = "") -> dict:
    """确定性 mock Agent：不调 LLM、不碰 Docker，但产出与真实 `DiagnosisRun` 同构的结果。

    质量策略（按 difficulty 固定，用于让 dry-run 覆盖全部指标分支）：
      easy   → 调全部 required_tools，答案含全部 evidence_keys，无幻觉
      medium → 少调最后一个 required_tool，并重复调用第一个工具一次（冗余），答案含全部证据
      hard   → 少调一个工具 + 只写一半证据词 + 故意写一句 forbidden_conclusion（幻觉）
    """
    ground_truth = case.get("ground_truth", {}) or {}
    evidence_keys = list(ground_truth.get("evidence_keys", []) or [])
    required = list(ground_truth.get("required_tools", []) or [])
    forbidden = list(ground_truth.get("forbidden_conclusion", []) or [])
    difficulty = case.get("difficulty", "easy")

    plan = list(required)
    keep_keys = list(evidence_keys)
    hallucinated: list[str] = []
    redundant = False
    if difficulty == "medium":
        if len(plan) > 1:
            plan = plan[:-1]
        redundant = bool(plan)
    elif difficulty == "hard":
        if len(plan) > 1:
            plan = plan[:-1]
        redundant = bool(plan)
        keep_keys = evidence_keys[: max(1, len(evidence_keys) // 2)]
        hallucinated = forbidden[:1]
    if redundant:
        plan.append(plan[0])

    tool_calls: list[dict] = []
    for index, tool in enumerate(plan):
        call_id = f"mock-{case.get('id', 'case')}-{index}"
        arguments = _mock_tool_args(tool, case)
        events.append({"kind": "tool_start", "call_id": call_id, "tool": tool, "input": arguments})
        duration = 40 + _stable_jitter(f"{case.get('id')}-{tool}", 300)
        output = f"[dry-run mock] {tool} 返回合成结果（{case.get('fault_type')}）"
        tool_calls.append(
            {
                "tool": tool,
                "input": arguments,
                "status": "success",
                "duration_ms": duration,
                "output": output,
            }
        )
        events.append(
            {
                "kind": "tool_end",
                "call_id": call_id,
                "tool": tool,
                "status": "success",
                "duration_ms": duration,
                "output": output,
            }
        )

    answer = _mock_answer(case, params, keep_keys, hallucinated)
    total_tokens = 60 + len(case.get("question", "")) // 2 + len(answer) // 2 + 30 * len(tool_calls)
    duration_ms = 300 + sum(call["duration_ms"] for call in tool_calls) + _stable_jitter(case.get("id", ""), 200)
    return {
        "answer": answer,
        "model": model_name or "mock://dry-run",
        "duration_ms": duration_ms,
        "total_tokens": total_tokens,
        "tool_calls": tool_calls,
        "events": events,
        "error": None,
    }


def _mock_tool_args(tool: str, case: dict) -> dict:
    fault_type = case.get("fault_type", "")
    return {
        "list_services": {},
        "check_service": {"service": _mock_service_name(fault_type)},
        "read_logs": {"service": _mock_service_name(fault_type), "lines": 50},
        "search_logs": {"service": _mock_service_name(fault_type), "keyword": "ERROR", "lines": 200},
        "query_prometheus": {"promql": _mock_promql(fault_type)},
        "run_kafka_command": {"subcommand": "consumer-groups --describe --all-groups"},
        "run_redis_command": {"command": "INFO memory"},
        "query_business_data": {"question": case.get("question", "")},
        "query_business_dataset": {"dataset": "ops_daily", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "search_knowledge_base": {"query": case.get("question", "")},
    }.get(tool, {})


def _mock_service_name(fault_type: str) -> str:
    return {
        "service_unreachable": "API",
        "redis_memory": "Redis",
        "kafka_lag": "Kafka",
        "mysql_connections": "MySQL",
        "container_oom": "Kafka",
        "slow_sql": "MySQL",
        "log_error_storm": "API",
    }.get(fault_type, "API")


def _mock_promql(fault_type: str) -> str:
    return {
        "kafka_lag": "kafka_consumergroup_lag_sum",
        "mysql_connections": "mysql_global_status_threads_connected",
        "container_oom": "kafka_brokers",
        "slow_sql": "mysql_global_status_slow_queries",
    }.get(fault_type, "up")


def _mock_answer(case: dict, params: dict, keep_keys: list[str], hallucinated: list[str]) -> str:
    ground_truth = case.get("ground_truth", {}) or {}
    root_cause = ground_truth.get("root_cause", "根因待定")
    evidence_lines = "\n".join(f"- {key}" for key in keep_keys) or "- （无证据词）"
    if hallucinated:
        evidence_lines += "\n- 也可能是" + "，".join(hallucinated)
    playbook = f"（引用剧本 {params.get('skill_name')}）" if params.get("skill_name") else ""
    return (
        f"**结论**：{root_cause}（置信度：中）\n"
        f"**影响**：受影响服务为 {_mock_service_name(case.get('fault_type', ''))} 相关链路\n"
        f"**关键证据**：\n{evidence_lines}\n"
        f"**建议**：\n- 按证据逐项复核配置并回滚变更\n- 补充监控阈值告警\n"
        f"**待确认**：dry-run 合成答案，ablation={params.get('ablation')} {playbook}".strip()
    )


# ── 单用例运行 ────────────────────────────────────────────────────────────

@dataclass
class RunOptions:
    dry_run: bool = True
    model: str = ""
    model_mode: str = "auto"
    descriptor_dir: Path = field(default_factory=lambda: DEFAULT_DESCRIPTOR_DIR)
    docs_root: Path = field(default_factory=lambda: DEFAULT_DOCS_ROOT)
    keyword_only_rag: bool = True
    progress_echo: Callable[[str], None] | None = None


def load_descriptor_for(fault_type: str, descriptor_dir: str | Path | None = None) -> dict:
    from dataset import load_descriptor

    return load_descriptor(fault_type, descriptor_dir)


def base_record(case: dict, group: str, options: RunOptions) -> dict:
    """一条用例轨迹记录的骨架（timeout / 异常等场景也复用它）。"""
    return {
        "case_id": case.get("id", ""),
        "fault_type": case.get("fault_type", ""),
        "difficulty": case.get("difficulty", ""),
        "system_id": case.get("system_id", ""),
        "question": case.get("question", ""),
        "ablation": group,
        "params": {
            "skill_name": "",
            "skill_steps_chars": 0,
            "knowledge_context_chars": 0,
            "data_catalog_chars": 0,
        },
        "descriptor_path": str(Path(options.descriptor_dir) / f"{case.get('fault_type', '')}.json"),
        "ground_truth": case.get("ground_truth", {}),
        "answer": "",
        "model": "",
        "duration_ms": 0,
        "total_tokens": 0,
        "tool_calls": [],
        "events": [],
        "error": "",
        "model_mode_effective": options.model_mode,
    }


def run_case(case: dict, group: str, options: RunOptions) -> dict:
    """跑一条用例的一个消融组，返回可写进 trajectories.jsonl 的完整记录。

    任何异常（fixture 缺失、RAG 失败、LLM 限流/网络错误）都只写进 record["error"]，
    不向上抛——单条用例失败不能影响整批。
    """
    record = base_record(case, group, options)
    events: list[dict] = record["events"]
    try:
        descriptor = load_descriptor_for(case.get("fault_type", ""), options.descriptor_dir)
        prepare_descriptor(descriptor)
        params = resolve_ablation(
            case,
            group,
            descriptor=descriptor,
            docs_root=options.docs_root,
            keyword_only_rag=options.keyword_only_rag,
        )
        record["params"] = {
            "skill_name": params["skill_name"],
            "skill_steps_chars": params["skill_steps_chars"],
            "knowledge_context_chars": params["context_chars"],
            "data_catalog_chars": len(params["data_catalog"]),
        }

        if options.dry_run:
            mock = mock_diagnose(case, params, events, model_name=options.model)
            record.update(
                {
                    "answer": mock["answer"],
                    "model": mock["model"],
                    "duration_ms": mock["duration_ms"],
                    "total_tokens": mock["total_tokens"],
                    "tool_calls": mock["tool_calls"],
                }
            )
            return record

        from app.agent.diagnostics.runner import diagnose_with_details

        model_mode = options.model_mode
        if options.model and model_mode == "auto":
            # 实测：endpoint_for_choice("auto") 直接返回 None 并忽略 model_name，
            # 所以指定 --model 时必须切到 "api" 才能生效。
            model_mode = "api"
        record["model_mode_effective"] = model_mode
        run = diagnose_with_details(
            descriptor,
            case.get("question", ""),
            org_id=0,
            system_id=0,
            skill_steps=params["skill_steps"],
            knowledge_context=params["knowledge_context"],
            trace_question=None,
            remote_command=make_remote_command(descriptor, echo=options.progress_echo),
            business_data_query=None,
            business_dataset_query=None,
            data_catalog=params["data_catalog"],
            model_mode=model_mode,
            model_name=options.model,
            on_progress=events.append,
        )
        record.update(
            {
                "answer": run.answer,
                "model": run.model,
                "duration_ms": run.duration_ms,
                "total_tokens": run.total_tokens or 0,
                "tool_calls": run.tool_calls or [],
            }
        )
    except Exception as exc:  # noqa: BLE001 - LLM/网络/限流/配置异常记为 error，不中断整批
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record
