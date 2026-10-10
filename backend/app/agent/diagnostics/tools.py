"""Tooling and runtime helpers for the diagnosis agent."""
import functools
import inspect
import json
import logging
import shlex
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import httpx
from pydantic_ai import Agent, RunContext

from ...services.descriptors.container_state import format_container_state, inspect_container
from ...services.descriptors.health import collect_health, read_service_logs, search_service_logs
from ...services.descriptors.ops_policy import kafka_command_denied, redis_command_denied
from ...services.descriptors.prompt import build_prompt
from .knowledge.store import search_with_memories
from .skill_router import get_skill_steps
from .tool_events import NESTED_OUTPUT_LIMIT, iter_tool_events, safe_emit

log = logging.getLogger(__name__)


def _memoized(func):
    """同一诊断内、参数完全相同的工具调用直接返回缓存结果。

    重复调用同一个只读工具很常见（实测一次诊断里 run_kafka_command 被调 6 次、
    query_business_dataset 被调 4 次）。每多一次重复，agent 就多跑一轮 loop，
    而每一轮都要把整段上下文重发给模型，成本是重复的。
    """
    signature = None

    @functools.wraps(func)
    def wrapper(ctx: RunContext["AgentDeps"], *args, **kwargs):
        nonlocal signature
        cache = getattr(ctx.deps, "tool_cache", None)
        if cache is None:
            return func(ctx, *args, **kwargs)
        if signature is None:
            try:
                signature = inspect.signature(func)
            except (TypeError, ValueError):
                return func(ctx, *args, **kwargs)
        try:
            # 按签名归一化，避免 (a, y=1) 和 (a,) 被当成两次不同的调用。
            bound = signature.bind(ctx, *args, **kwargs)
            bound.apply_defaults()  # 把默认值补齐，否则 (a,) 和 (a, y=1) 仍会被视为不同参数
            payload = {k: v for k, v in bound.arguments.items() if k != "ctx"}
            key = (func.__name__, json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            return func(ctx, *args, **kwargs)
        if key in cache:
            return f"{cache[key]}\n\n（本次诊断已查过相同参数，以上为缓存结果，请直接使用，不要再重复调用本工具）"
        value = func(ctx, *args, **kwargs)
        cache[key] = value
        return value

    return wrapper


@dataclass
class AgentDeps:
    descriptor: dict
    question: str = ""
    skill_steps: str | None = None
    knowledge_context: str = ""
    conversation_context: str = ""
    remote_command: Callable[[str, dict], dict] | None = None
    business_data_query: Callable[[str], str] | None = None
    business_dataset_query: Callable[..., str] | None = None
    archive_list: Callable[[str], str] | None = None
    archive_read: Callable[[str, str], str] | None = None
    data_catalog: str = ""
    # 同一诊断内的工具结果缓存，key = (工具名, 参数)，由 _memoized 装饰器读写。
    tool_cache: dict = field(default_factory=dict)
    # 取证子代理的工具调用会冒泡到这里（保证审计链完整：required_tools 匹配、
    # 证据链、live 工具链都能看到子代理内部步骤）。
    nested_tool_calls: list = field(default_factory=list)
    # 主代理运行时传入的进度回调（runner 的 on_progress）；子代理事件经它冒泡到证据链/UI。
    progress_sink: Callable[[dict], None] | None = None


def register_tools(agent: Agent, *, evidence_only: bool = False) -> Agent:
    @agent.system_prompt
    def system_prompt(ctx: RunContext[AgentDeps]) -> str:
        base = build_prompt(ctx.deps.descriptor)
        skill_steps = (
            get_skill_steps(ctx.deps.question)
            if ctx.deps.skill_steps is None
            else ctx.deps.skill_steps
        )
        parts = [base]
        if skill_steps:
            parts.append(skill_steps)
        if ctx.deps.conversation_context:
            parts.append(
                "## 对话上下文（用户基于上一轮诊断继续提问）\n"
                f"{ctx.deps.conversation_context}\n\n"
                "⚠️ 上轮已确认、且用户当前仍需要的结论可直接复用，不必重复取数；"
                "但若用户现在要的是**更细的字段或逐条明细**，而上轮只给了汇总数字，"
                "必须重新调用工具去取明细，绝不能拿上轮那句摘要糊弄、更不能说「无法匹配明细」。"
                "若当前问题与上一轮无关，忽略上文按正常流程诊断。"
            )
        if ctx.deps.data_catalog:
            parts.append(
                "## 可用业务数据集（只读）\n"
                f"{ctx.deps.data_catalog}\n\n"
                "⚠️ 用户问具体数量/次数/成功率/卡住多少这类量化问题时，"
                "必须调用 query_business_dataset 取真实数据后再回答，"
                "不要凭快照里的空字段或 0 值猜测，也不要回答「证据不足」。"
                "取到 0 条时要明确说明「该时间段内确实没有记录」，而不是「无法确认」。\n"
                "⚠️ 用户要求「明细 / 详情 / 详细信息 / 列出 / 都有哪些」时，"
                "必须把查询返回的记录**逐条列出关键字段**（Markdown 表格，最多 20 行）"
                "放在「关键证据」里；不要只回答总数或 ID 区间、再让用户去指定条件重查——"
                "你已经查到了就直接给出。\n"
                "⚠️ 表格要**窄而清爽**：只保留 5~6 个关键列（如 task_id、search_type、"
                "analysis_status、切割完成/需、create_time），列名用简短中文；"
                "run_id、t_cat_id 这类超长标识默认不列，除非用户明确要看（要看时也请截断中段）。"
            )
        if ctx.deps.knowledge_context:
            parts.append(
                "## 知识库预检索（本系统权威，回答时必须优先遵循）\n"
                f"{ctx.deps.knowledge_context}\n\n"
                "⚠️ 以上段落来自本系统知识库。回答时必须引用对应文档名，"
                "按其中步骤排查；不要用其他平台的通用采集器模板替代。"
            )
        return "\n\n".join(parts)

    @agent.tool
    @_memoized
    def list_services(ctx: RunContext[AgentDeps]) -> str:
        if ctx.deps.remote_command:
            result = ctx.deps.remote_command("health_check", {})
            if result.get("ok"):
                return "\n".join(
                    f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}"
                    for item in result.get("result", [])
                )
        health = collect_health(ctx.deps.descriptor)
        if not health:
            return "该系统未注册任何服务"
        return "\n".join(
            f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}" for item in health
        )

    @agent.tool
    @_memoized
    def check_service(ctx: RunContext[AgentDeps], service: str) -> str:
        if ctx.deps.remote_command:
            result = ctx.deps.remote_command("health_check", {"service": service})
            if result.get("ok"):
                item = next((entry for entry in result.get("result", []) if entry.get("name") == service), None)
                if item:
                    return f"{service}: {item['detail']} {'✅' if item['ok'] else '❌'}"
        for item in collect_health(ctx.deps.descriptor):
            if item["name"] == service:
                return f"{service}: {item['detail']} {'✅' if item['ok'] else '❌'}"
        return f"系统中无服务「{service}」"

    @agent.tool
    @_memoized
    def check_container_state(ctx: RunContext[AgentDeps], service: str) -> str:
        """查容器真实状态：运行/退出原因/OOMKilled/ExitCode/重启次数/内存上限。

        判断「容器是 OOM 被杀还是自己退出」「是否达到内存上限」时用这个，
        check_service 只探活，看不到退出原因。
        """
        service_cfg = next(
            (svc for svc in ctx.deps.descriptor.get("services", []) if svc.get("name") == service),
            None,
        )
        if not service_cfg:
            return f"系统中无服务「{service}」"
        container = service_cfg.get("container")
        if not container:
            return f"服务「{service}」不是容器化部署，无容器状态可查"
        state = inspect_container(container)
        if state is None:
            return f"docker inspect {container} 失败（容器不存在或 Docker 不可用）"
        return format_container_state(state)

    @agent.tool
    @_memoized
    def read_logs(ctx: RunContext[AgentDeps], service: str, lines: int = 50) -> str:
        if ctx.deps.remote_command:
            result = ctx.deps.remote_command("fetch_logs", {"service": service, "lines": lines})
            if result.get("ok"):
                return str(result.get("result", "日志为空"))
        return read_service_logs(ctx.deps.descriptor, service, lines)

    @agent.tool
    @_memoized
    def search_logs(ctx: RunContext[AgentDeps], service: str, keyword: str, lines: int = 200) -> str:
        if ctx.deps.remote_command:
            result = ctx.deps.remote_command(
                "search_logs", {"service": service, "keyword": keyword, "lines": lines}
            )
            if result.get("ok"):
                return str(result.get("result", "未找到匹配日志"))
        return search_service_logs(ctx.deps.descriptor, service, keyword, lines)

    @agent.tool
    @_memoized
    def query_prometheus(ctx: RunContext[AgentDeps], promql: str) -> str:
        if ctx.deps.remote_command:
            prom_service = next(
                (svc for svc in ctx.deps.descriptor.get("services", []) if svc.get("connector") == "prometheus"),
                None,
            )
            if prom_service:
                result = ctx.deps.remote_command(
                    "query_prometheus",
                    {"service": prom_service.get("name", ""), "query": promql},
                )
                if not result.get("ok"):
                    return f"远程 Prometheus 查询失败: {result.get('result', '未知错误')}"
                rows = result.get("result", [])
                if not rows:
                    return f"PromQL: `{promql}` 无数据"
                return "远程 PromQL: `" + promql + "`\n" + "\n".join(
                    f"  {item.get('metric', {})} = {item.get('value', ['', ''])[1]}" for item in rows[:8]
                ) + (f"\n（其余 {len(rows) - 8} 条省略）" if len(rows) > 8 else "")
        base_url = find_prometheus_url(ctx.deps.descriptor)
        if not base_url:
            return "未找到 Prometheus 地址。请在服务列表中注册一个 health_url 含 9090 的 HTTP 服务，或在 infra.prometheus_url 中配置。"
        try:
            response = httpx.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                return f"Prometheus 返回错误: {data.get('error', data)}"
            results = data["data"]["result"]
            if not results:
                return f"查询 `{promql}` 无数据（指标可能不存在或目标未被抓取）"
            lines = []
            for result in results[:8]:
                metric = result.get("metric", {})
                labels = ", ".join(f'{k}="{v}"' for k, v in metric.items() if k not in ("__name__", "job"))
                if len(labels) > 80:   # 标签过长只留 instance 等核心标识
                    labels = labels[:77] + "..."
                name = metric.get("__name__", promql.split("{")[0])
                value = result["value"][1]
                lines.append(f"  {name}{'{' + labels + '}' if labels else ''} = {value}")
            note = f"（其余 {len(results) - 8} 条省略——已足够判断，如需明细可缩小查询范围）" if len(results) > 8 else ""
            header = f"PromQL: `{promql}`  ({len(results)} 条结果)"
            return header + note + "\n" + "\n".join(lines)
        except Exception as exc:
            return f"Prometheus 查询失败: {exc}"

    @agent.tool
    @_memoized
    def run_kafka_command(ctx: RunContext[AgentDeps], subcommand: str) -> str:
        denied = kafka_command_denied(subcommand)
        if denied:
            return denied
        parts = shlex.split(subcommand)

        if ctx.deps.remote_command:
            kafka = next(
                (svc for svc in ctx.deps.descriptor.get("services", [])
                 if "kafka" in svc.get("name", "").lower()),
                None,
            )
            result = ctx.deps.remote_command(
                "run_kafka_command",
                {"service": kafka.get("name", "") if kafka else "", "command": subcommand},
            )
            return str(result.get("result", "远程 Kafka 查询失败")) if result.get("ok") else f"远程 Kafka 查询失败: {result.get('result', '')}"

        container = None
        for candidate in ("ai-ops-agent-kafka-1", "kafka"):
            inspect = subprocess.run(["docker", "inspect", candidate], capture_output=True, timeout=5)
            if inspect.returncode == 0:
                container = candidate
                break
        if not container:
            return "未找到 Kafka 容器（ai-ops-agent-kafka-1），请确认容器正在运行"

        script = f"kafka-{parts[0]}.sh"
        command = ["docker", "exec", container, f"/opt/kafka/bin/{script}", "--bootstrap-server", "localhost:9092"] + parts[1:]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            return output[:3000] if output else "(无输出)"
        except subprocess.TimeoutExpired:
            return "命令超时（30s）"
        except Exception as exc:
            return f"执行失败: {exc}"

    @agent.tool
    @_memoized
    def run_redis_command(ctx: RunContext[AgentDeps], command: str) -> str:
        denied = redis_command_denied(command)
        if denied:
            return denied

        if ctx.deps.remote_command:
            redis = next(
                (svc for svc in ctx.deps.descriptor.get("services", [])
                 if "redis" in svc.get("name", "").lower()
                 or int(svc.get("config", {}).get("port", svc.get("port", 0)) or 0) == 6379),
                None,
            )
            result = ctx.deps.remote_command(
                "run_redis_command",
                {"service": redis.get("name", "") if redis else "", "command": command},
            )
            return str(result.get("result", "远程 Redis 查询失败")) if result.get("ok") else f"远程 Redis 查询失败: {result.get('result', '')}"

        host, port = find_redis_addr(ctx.deps.descriptor)
        if not host:
            return "未找到 Redis 服务（需要 connector=tcp 且 port=6379 的服务）"

        try:
            command_parts = command.strip().split()
            resp_command = f"*{len(command_parts)}\r\n" + "".join(
                f"${len(part)}\r\n{part}\r\n" for part in command_parts
            )
            with socket.create_connection((host, int(port)), timeout=5) as conn:
                conn.sendall(resp_command.encode())
                chunks = []
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if len(chunk) < 65536:
                        break
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            lines = [line for line in raw.splitlines() if not line.startswith(("*", "$", ":")) or " " in line]
            return "\n".join(lines[:80]) or raw[:500]
        except Exception as exc:
            return f"Redis 命令执行失败: {exc}"

    # ---- 取证子代理到此为止：以下工具仅主代理可用 ----
    if evidence_only:
        return agent

    @agent.tool
    def investigate(ctx: RunContext[AgentDeps], task: str) -> str:
        """把一段多步取证委派给子代理执行：它独立调用工具收集证据，只返回紧凑的证据摘要。

        适用：需要连续调用 3 个以上工具、或要翻大量日志/指标才能下结论的场景。
        简单单点检查（查一个服务状态、查一条指标）直接调对应工具即可，不用委派。
        委派时把任务描述写清楚：查什么服务、关注什么指标、要什么证据。
        """
        from .investigator import get_evidence_agent
        from .models import default_model

        sub_deps = AgentDeps(
            descriptor=ctx.deps.descriptor,
            question=task,
            remote_command=ctx.deps.remote_command,
            business_data_query=ctx.deps.business_data_query,
            business_dataset_query=ctx.deps.business_dataset_query,
            tool_cache=ctx.deps.tool_cache,  # 共享缓存：子代理查过的主代理侧不再重复
        )
        # 子代理工具调用冒泡：同一份规范化载荷同时写进主 deps 的 nested_tool_calls
        # （审计链/评分/轨迹可见）与主代理的 progress_sink（证据链与前端实时工具链）。
        main_nested = ctx.deps.nested_tool_calls
        main_sink = ctx.deps.progress_sink

        async def sub_handler(_ctx, events) -> None:
            async for payload in iter_tool_events(events, id_prefix="sub-"):
                if payload["kind"] == "tool_start":
                    main_nested.append({
                        "tool": payload["tool"],
                        "input": payload["input"],
                        "status": "started",
                        "duration_ms": 0,
                        "output": "",
                        "call_id": payload["call_id"],
                    })
                else:
                    item = next(
                        (c for c in main_nested if c.get("call_id") == payload["call_id"]),
                        None,
                    )
                    if item is None:
                        item = {
                            "tool": payload["tool"] or "unknown",
                            "input": {},
                            "call_id": payload["call_id"],
                        }
                        main_nested.append(item)
                    item["status"] = payload["status"]
                    item["duration_ms"] = payload["duration_ms"]
                    item["output"] = payload["output"][:NESTED_OUTPUT_LIMIT]
                safe_emit(main_sink, payload)

        try:
            result = get_evidence_agent().run_sync(
                task,
                deps=sub_deps,
                model=default_model(),
                retries=2,
                event_stream_handler=sub_handler,
            )
            summary = str(result.output).strip()
            used = sorted({c.get("tool", "") for c in main_nested})
            return f"【子代理取证报告】（取证工具: {', '.join(used)}）\n{summary[:2200]}"
        except Exception as exc:  # noqa: BLE001 - 委派失败降级为主代理自己查
            log.warning("[investigate] 子代理执行失败，降级为主代理直接取证: %s", exc)
            return (
                f"取证子代理执行失败（{str(exc)[:120]}）。\n"
                "请改用 check_service / read_logs / query_prometheus 等工具直接取证。"
            )

    @agent.tool
    @_memoized
    def query_business_data(ctx: RunContext[AgentDeps], question: str) -> str:
        """查询业务数据库中的只读统计数据（新增量、最近记录、卡住任务等）。
        仅当用户明确问业务表/任务流水/订单是否到达、数量统计、长时间未更新任务时调用。
        不要用于日志、报错、异常、堆栈、服务故障排查——那些应使用 search_logs / read_logs / check_service。
        仅执行受控只读分析，不会修改业务数据。"""
        if not ctx.deps.business_data_query:
            return "当前系统未启用只读业务数据源，无法查询业务表。"
        return ctx.deps.business_data_query(question)

    @agent.tool
    @_memoized
    def query_business_dataset(
        ctx: RunContext[AgentDeps],
        dataset: str,
        date_from: str = "",
        date_to: str = "",
        filters: dict | None = None,
    ) -> str:
        """查询业务系统的只读数据集（按天的运行量/切割/文件/产物/卡住任务，以及归档文件索引等）。

        用户问「今天/某天执行了多少次」「有多少条记录」「成功多少、失败多少」「卡住多少」
        这类量化问题时必须调用本工具取真实数据，不要凭快照猜测或回答证据不足。
        参数：
          dataset：数据集名称，只能从 system prompt 的「可用业务数据集」里选。
          date_from / date_to：日期区间，格式 YYYY-MM-DD；留空表示不限制。查询今天时两个都填今天。
          filters：可选，按列精确过滤，如 {"t_cat_id": "tn260717_120003_gbm"}；
                   只允许目录里标注「可按 … 过滤」的列，用别的列会报错。
        返回值末尾会给出「汇总」行；需要总数时直接引用汇总行，不要自己对明细行做加减。
        只执行受控的 SELECT 查询，不会修改任何业务数据。"""
        if not ctx.deps.business_dataset_query:
            return "当前系统未配置可查询的业务数据集。"
        return ctx.deps.business_dataset_query(dataset, date_from, date_to, filters)

    @agent.tool
    @_memoized
    def list_archive_files(ctx: RunContext[AgentDeps], tcat_id: str) -> str:
        """列出某次触发/爆发(TCat)的归档目录与文件清单（文件名、类型、大小、时间）。

        用户问「归档在哪 / 有哪些文件 / 有哪些产物 / 这次爆发生成了什么」时调用。
        参数 tcat_id：触发ID（形如 tn260717_120003_gbm，也就是 t_cat_id）。"""
        if not ctx.deps.archive_list:
            return "当前系统未启用归档文件访问，无法列出归档文件。"
        return ctx.deps.archive_list(tcat_id)

    @agent.tool
    @_memoized
    def read_archive_file(ctx: RunContext[AgentDeps], tcat_id: str, file_name: str) -> str:
        """读取某个归档文件的内容：文本文件返回文本，FITS 文件返回头摘要（头关键字）。

        用于解释文件是什么、做简要分析。请先用 list_archive_files 拿到确切文件名。
        参数 tcat_id：触发ID；file_name：文件名（如 result_tn260717_120003_gbm_loc.fits）。"""
        if not ctx.deps.archive_read:
            return "当前系统未启用归档文件访问，无法读取文件。"
        return ctx.deps.archive_read(tcat_id, file_name)

    @agent.tool
    @_memoized
    def search_knowledge_base(ctx: RunContext[AgentDeps], query: str) -> str:
        """从该系统的运维知识库检索历史故障经验、操作手册、系统文档。
        遇到不熟悉的故障类型、需要参考历史处理经验或查找操作步骤时调用。
        返回语义相关的文档片段；知识库为空或无匹配时返回空字符串。"""
        system_id = ctx.deps.descriptor.get("id", "default")
        hits = search_with_memories(query, system_id)
        if not hits:
            return "知识库中暂无相关记录。可基于实时探活和日志继续分析，并说明知识库无匹配。"
        doc_names = "、".join(hit["name"] for hit in hits)
        body = "\n\n".join(f"### {hit['name']}\n{hit['snippet']}" for hit in hits)
        return (
            "## 知识库检索结果（必须作为本系统权威依据）\n\n"
            f"引用文档：{doc_names}\n\n"
            f"{body}\n\n"
            "请在最终回答中：1) 列出上述文档名；2) 按文档步骤组织排查建议；"
            "3) 不要用蓝鲸 GSE 等外部平台模板覆盖这些内容。"
        )

    return agent


def find_prometheus_url(descriptor: dict) -> str:
    infra = descriptor.get("infra", {})
    if infra.get("prometheus_url"):
        return infra["prometheus_url"].rstrip("/")
    for service in descriptor.get("services", []):
        config = service.get("config", {})
        url = (
            service.get("url", "")
            or config.get("url", "")
            or service.get("health_url", "")
            or config.get("health_url", "")
        )
        if service.get("connector") == "prometheus" and url:
            return url.rstrip("/")
        if url and ("9090" in url or "prometheus" in url.lower()):
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def find_redis_addr(descriptor: dict) -> tuple[str, int]:
    for service in descriptor.get("services", []):
        config = service.get("config", {})
        name = service.get("name", "").lower()
        if int(config.get("port", 0)) == 6379 or "redis" in name:
            return config.get("host", "127.0.0.1"), int(config.get("port", 6379))
    return "", 6379
