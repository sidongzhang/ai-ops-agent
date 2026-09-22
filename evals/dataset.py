"""评测数据集：schema 定义、加载、校验与筛选。

只依赖标准库，保证 `--validate` 在没有任何第三方依赖时也能跑。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = EVALS_DIR / "datasets" / "seed_cases.jsonl"
DEFAULT_DESCRIPTOR_DIR = EVALS_DIR / "fixtures" / "descriptors"
DEFAULT_DOCS_ROOT = EVALS_DIR / "knowledge" / "docs"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"

#: 数据集里所有用例共用的知识库/描述符 system_id（见 evals/knowledge/docs/<id>/）
EVAL_SYSTEM_ID = "eval-docker-local"

FAULT_TYPES: tuple[str, ...] = (
    "service_unreachable",
    "redis_memory",
    "kafka_lag",
    "mysql_connections",
    "container_oom",
    "slow_sql",
    "log_error_storm",
)

FAULT_TYPE_LABELS: dict[str, str] = {
    "service_unreachable": "服务不可达",
    "redis_memory": "Redis 内存打满",
    "kafka_lag": "Kafka 消费堆积",
    "mysql_connections": "MySQL 连接打满",
    "container_oom": "容器 OOMKill",
    "slow_sql": "慢 SQL",
    "log_error_storm": "日志错误风暴",
}

DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")
INJECT_ACTIONS: tuple[str, ...] = ("docker_exec", "docker_stop", "docker_start", "http", "manual")

#: diagnose agent 注册的 10 个工具（backend/app/agent/diagnostics/tools.py）
TOOL_NAMES: tuple[str, ...] = (
    "list_services",
    "check_service",
    "read_logs",
    "search_logs",
    "query_prometheus",
    "run_kafka_command",
    "run_redis_command",
    "query_business_data",
    "query_business_dataset",
    "search_knowledge_base",
)

TOP_LEVEL_FIELDS = ("id", "fault_type", "difficulty", "system_id", "question", "ground_truth", "inject", "setup", "teardown")
GROUND_TRUTH_FIELDS = ("root_cause", "evidence_keys", "required_tools", "forbidden_conclusion")
INJECT_FIELDS = ("action", "target", "command")

MIN_CASES = 21
MIN_CASES_PER_FAULT = 3
#: 至少这么多条用例要带「看似合理的错误归因」诱饵
MIN_DECOY_CASES = 5


class DatasetError(Exception):
    """数据集无法加载（文件缺失/JSON 解析失败）。"""


def load_cases(path: str | Path | None = None) -> list[dict]:
    """加载 JSONL 数据集（每行一个对象，空行与 # 注释行忽略）。"""
    target = Path(path) if path else DEFAULT_DATASET
    if not target.exists():
        raise DatasetError(f"数据集不存在: {target}")
    cases: list[dict] = []
    with target.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{target}:{lineno} JSON 解析失败: {exc}") from exc
            if not isinstance(item, dict):
                raise DatasetError(f"{target}:{lineno} 每行必须是 JSON 对象")
            item["_line"] = lineno
            cases.append(item)
    return cases


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def validate_cases(cases: Sequence[dict]) -> list[str]:
    """校验数据集 schema + 覆盖度要求，返回错误信息列表（空列表 = 通过）。"""
    errors: list[str] = []
    if len(cases) < MIN_CASES:
        errors.append(f"用例总数 {len(cases)} < 要求下限 {MIN_CASES}")

    seen_ids: dict[str, int] = {}
    fault_counter: Counter[str] = Counter()
    decoy_cases = 0

    for index, case in enumerate(cases, start=1):
        where = f"第 {index} 行"
        case_id = case.get("id")
        if isinstance(case_id, str) and case_id.strip():
            where = f"用例 {case_id}"
            if case_id in seen_ids:
                errors.append(f"{where}: id 重复（首现于第 {seen_ids[case_id]} 行）")
            seen_ids[case_id] = case.get("_line", index)
        for field in TOP_LEVEL_FIELDS:
            if field not in case:
                errors.append(f"{where}: 缺少字段 {field}")

        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{where}: id 必须是非空字符串")
        if case.get("fault_type") not in FAULT_TYPES:
            errors.append(f"{where}: fault_type={case.get('fault_type')!r} 不在 {FAULT_TYPES}")
        else:
            fault_counter[case["fault_type"]] += 1
        if case.get("difficulty") not in DIFFICULTIES:
            errors.append(f"{where}: difficulty={case.get('difficulty')!r} 不在 {DIFFICULTIES}")
        if not isinstance(case.get("system_id"), str) or not case.get("system_id", "").strip():
            errors.append(f"{where}: system_id 必须是非空字符串")
        if not isinstance(case.get("question"), str) or len(case.get("question", "").strip()) < 6:
            errors.append(f"{where}: question 缺失或过短")

        if not _is_str_list(case.get("setup")):
            errors.append(f"{where}: setup 必须是字符串列表（可为空列表）")
        if not _is_str_list(case.get("teardown")):
            errors.append(f"{where}: teardown 必须是字符串列表（可为空列表）")

        ground_truth = case.get("ground_truth")
        if not isinstance(ground_truth, dict):
            errors.append(f"{where}: ground_truth 必须是对象")
        else:
            for field in GROUND_TRUTH_FIELDS:
                if field not in ground_truth:
                    errors.append(f"{where}: ground_truth 缺少字段 {field}")
            if not isinstance(ground_truth.get("root_cause"), str) or not ground_truth.get("root_cause", "").strip():
                errors.append(f"{where}: ground_truth.root_cause 必须是非空字符串")
            if not _is_str_list(ground_truth.get("evidence_keys")):
                errors.append(f"{where}: ground_truth.evidence_keys 必须是非空字符串列表")
            required_tools = ground_truth.get("required_tools")
            if not _is_str_list(required_tools):
                errors.append(f"{where}: ground_truth.required_tools 必须是非空字符串列表")
            else:
                unknown = [tool for tool in required_tools if tool not in TOOL_NAMES]
                if unknown:
                    errors.append(f"{where}: required_tools 含未知工具 {unknown}（合法值: {list(TOOL_NAMES)}）")
            forbidden = ground_truth.get("forbidden_conclusion")
            if not _is_str_list(forbidden) and forbidden != []:
                errors.append(f"{where}: ground_truth.forbidden_conclusion 必须是字符串列表（可为空）")
            elif forbidden:
                decoy_cases += 1

        inject = case.get("inject")
        if not isinstance(inject, dict):
            errors.append(f"{where}: inject 必须是对象（无注入时写 {{}}）")
        elif inject:
            action = inject.get("action")
            if action not in INJECT_ACTIONS:
                errors.append(f"{where}: inject.action={action!r} 不在 {INJECT_ACTIONS}")
            if not isinstance(inject.get("target"), str) or not inject.get("target", "").strip():
                errors.append(f"{where}: inject.target 必须是非空字符串")
            if not isinstance(inject.get("command"), str):
                errors.append(f"{where}: inject.command 必须是字符串（无命令写 \"\"）")
            for field in INJECT_FIELDS:
                if field not in inject:
                    errors.append(f"{where}: inject 缺少字段 {field}")

    for fault_type in FAULT_TYPES:
        count = fault_counter.get(fault_type, 0)
        if count < MIN_CASES_PER_FAULT:
            errors.append(f"故障类型 {fault_type} 只有 {count} 条，要求 ≥{MIN_CASES_PER_FAULT}")
    if decoy_cases < MIN_DECOY_CASES:
        errors.append(f"带 forbidden_conclusion 诱饵的用例只有 {decoy_cases} 条，要求 ≥{MIN_DECOY_CASES}")
    return errors


def fault_type_counts(cases: Iterable[dict]) -> dict[str, int]:
    return dict(Counter(case.get("fault_type", "unknown") for case in cases))


def difficulty_counts(cases: Iterable[dict]) -> dict[str, int]:
    return dict(Counter(case.get("difficulty", "unknown") for case in cases))


def select_cases(
    cases: Sequence[dict],
    *,
    fault_types: Sequence[str] | None = None,
    case_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """按故障类型/用例 id 过滤，并可选截断条数。"""
    selected = list(cases)
    if fault_types:
        wanted = set(fault_types)
        selected = [case for case in selected if case.get("fault_type") in wanted]
    if case_ids:
        wanted_ids = set(case_ids)
        missing = wanted_ids - {case.get("id") for case in selected}
        if missing:
            raise DatasetError(f"未找到用例: {sorted(missing)}")
        selected = [case for case in selected if case.get("id") in wanted_ids]
    if limit is not None and limit > 0:
        selected = selected[:limit]
    return selected


def descriptor_path(fault_type: str, descriptor_dir: str | Path | None = None) -> Path:
    base = Path(descriptor_dir) if descriptor_dir else DEFAULT_DESCRIPTOR_DIR
    return base / f"{fault_type}.json"


def load_descriptor(fault_type: str, descriptor_dir: str | Path | None = None) -> dict:
    path = descriptor_path(fault_type, descriptor_dir)
    if not path.exists():
        raise DatasetError(f"缺少 {fault_type} 的 descriptor fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def format_case_table(cases: Sequence[dict], *, max_question: int = 34) -> str:
    """`--list` 用的纯文本表格。"""
    lines = [f"{'id':<26} {'fault_type':<20} {'难度':<7} 问题"]
    lines.append("-" * 100)
    for case in cases:
        question = case.get("question", "")
        if len(question) > max_question:
            question = question[: max_question - 1] + "…"
        lines.append(
            f"{case.get('id', ''):<26} {case.get('fault_type', ''):<20} "
            f"{case.get('difficulty', ''):<7} {question}"
        )
    return "\n".join(lines)


def format_distribution(cases: Sequence[dict]) -> str:
    counts = fault_type_counts(cases)
    total = len(cases)
    lines = [f"用例总数: {total}", f"故障类型分布（{len(FAULT_TYPES)} 类）:"]
    for fault_type in FAULT_TYPES:
        label = FAULT_TYPE_LABELS.get(fault_type, "")
        count = counts.get(fault_type, 0)
        lines.append(f"  - {fault_type:<20} {label:<16} {count} 条")
    lines.append(f"难度分布: {difficulty_counts(cases)}")
    decoys = sum(1 for case in cases if case.get("ground_truth", {}).get("forbidden_conclusion"))
    lines.append(f"含错误归因诱饵（forbidden_conclusion 非空）: {decoys} 条")
    return "\n".join(lines)
