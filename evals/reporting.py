"""评测报告生成：summary.json / summary.md / trajectories.jsonl。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dataset import FAULT_TYPE_LABELS
from metrics import (
    METRIC_LABELS,
    aggregate,
    failure_reasons,
    group_by_fault_type,
    score_case,
    sorted_fault_types,
)

CORE_METRICS = (
    "task_success_rate",
    "rca_accuracy",
    "tool_selection_f1",
    "tool_param_validity",
    "evidence_coverage_rate",
    "avg_tool_calls",
    "avg_progress_events",
    "redundant_call_rate",
    "hallucination_rate",
    "avg_duration_ms",
    "avg_total_tokens",
)

ABLATION_METRICS = (
    "task_success_rate",
    "rca_accuracy",
    "tool_selection_f1",
    "tool_selection_precision",
    "tool_selection_recall",
    "evidence_coverage_rate",
    "hallucination_rate",
    "avg_tool_calls",
    "redundant_call_rate",
    "avg_total_tokens",
)

FAULT_TABLE_METRICS = (
    "task_success_rate",
    "rca_accuracy",
    "tool_selection_f1",
    "evidence_coverage_rate",
    "hallucination_rate",
    "avg_tool_calls",
    "redundant_call_rate",
    "avg_duration_ms",
)

METRIC_FORMULAS: dict[str, str] = {
    "task_success_rate": "命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比",
    "rca_accuracy": "Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0",
    "tool_selection_f1": "以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均",
    "tool_param_validity": "tool_call 中 status==success 的占比（近似：只反映工具函数是否报错）",
    "evidence_coverage_rate": "至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct）",
    "avg_tool_calls": "平均每次诊断的 tool_call 数",
    "avg_progress_events": "on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次）",
    "redundant_call_rate": "(tool_call 总数 - 去重后的 tool+参数 组合数)/总数",
    "hallucination_rate": "答案命中 forbidden_conclusion 的用例占比",
    "tool_degraded_rate": "工具输出含降级标记（如「离线评测不支持」「查询失败」）的调用占比",
    "avg_duration_ms": "DiagnosisRun.duration_ms 的平均值",
    "avg_total_tokens": "DiagnosisRun.total_tokens 的平均值",
}


def build_summary(records: Sequence[dict], cases: Sequence[dict], meta: dict) -> dict:
    """把运行记录 + 用例打成可序列化的 summary。"""
    cases_by_id = {case.get("id"): case for case in cases}
    rows: list[dict] = []
    for record in records:
        case = cases_by_id.get(record.get("case_id"))
        if not case:
            continue
        rows.append(score_case(case, record))

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("ablation", ""), []).append(row)

    ablations: dict[str, dict] = {}
    for group, group_rows in groups.items():
        ablations[group] = {
            "description": meta.get("ablation_descriptions", {}).get(group, ""),
            "overall": aggregate(group_rows),
            "by_fault_type": group_by_fault_type(group_rows),
        }

    ordered_groups = [group for group in meta.get("ablation_groups", []) if group in ablations]
    ordered_groups += [group for group in ablations if group not in ordered_groups]
    primary = ordered_groups[0] if ordered_groups else ""
    primary_rows = groups.get(primary, [])

    summary = {
        "meta": {**meta, "ablation_groups": ordered_groups, "primary_group": primary},
        "overall": aggregate(primary_rows),
        "by_fault_type": group_by_fault_type(primary_rows),
        "ablations": ablations,
        "cases": rows,
    }
    return summary


def write_trajectories(records: Sequence[dict], out_dir: Path) -> Path:
    path = Path(out_dir) / "trajectories.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def write_summary_json(summary: dict, out_dir: Path) -> Path:
    path = Path(out_dir) / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _metric_table(metrics: dict, keys: Sequence[str]) -> str:
    lines = ["| 指标 | 值 | 说明 |", "| --- | --- | --- |"]
    for key in keys:
        label = METRIC_LABELS.get(key, key)
        lines.append(f"| {label} | {_fmt(metrics.get(key, 0))} | {METRIC_FORMULAS.get(key, '')} |")
    return "\n".join(lines)


def _fault_table(by_fault_type: dict, keys: Sequence[str]) -> str:
    headers = ["故障类型", "用例数"] + [METRIC_LABELS.get(key, key) for key in keys]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for fault_type in sorted_fault_types(by_fault_type.keys()):
        metrics = by_fault_type[fault_type]
        row = [f"{fault_type}（{FAULT_TYPE_LABELS.get(fault_type, '')}）", str(metrics.get("cases", 0))]
        row += [_fmt(metrics.get(key, 0)) for key in keys]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _ablation_table(ablations: dict, keys: Sequence[str]) -> str:
    headers = ["消融组", "用例数"] + [METRIC_LABELS.get(key, key) for key in keys]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for group, payload in ablations.items():
        metrics = payload["overall"]
        row = [group, str(metrics.get("cases", 0))]
        row += [_fmt(metrics.get(key, 0)) for key in keys]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _failure_lines(rows: Sequence[dict], limit: int = 40) -> str:
    failures = [row for row in rows if row.get("status") == "error" or not row.get("task_success")]
    if not failures:
        return "无失败用例。"
    lines = ["| 用例 | 消融组 | 故障类型 | 失败原因 |", "| --- | --- | --- | --- |"]
    for row in failures[:limit]:
        reasons = "、".join(failure_reasons(row)) or "-"
        lines.append(
            f"| {row.get('case_id')} | {row.get('ablation')} | {row.get('fault_type')} | {reasons} |"
        )
    if len(failures) > limit:
        lines.append(f"| … | | | 其余 {len(failures) - limit} 条见 summary.json |")
    return "\n".join(lines)


def render_markdown(summary: dict) -> str:
    meta = summary.get("meta", {})
    dry = bool(meta.get("dry_run"))
    mode = "dry-run（mock Agent，合成数据）" if dry else "真实 LLM 调用"
    overall = summary.get("overall", {})
    ablations = summary.get("ablations", {})
    ordered_groups = meta.get("ablation_groups", list(ablations))
    primary = meta.get("primary_group", ordered_groups[0] if ordered_groups else "")

    lines: list[str] = []
    lines.append("# LLM Agent 离线评测报告")
    lines.append("")
    if dry:
        lines.append(
            "> ⚠️ **本报告由 `--dry-run` 的 mock Agent 生成，数字是合成数据，不能当作模型真实能力。**"
            " 它只证明评测链路（数据集→消融→打分→报告）是通的。"
        )
        lines.append("")
    lines.append(f"- 生成时间：{meta.get('generated_at', '')}")
    lines.append(f"- 运行模式：{mode}")
    lines.append(f"- 数据集：`{meta.get('dataset_path', '')}`（{meta.get('case_count', 0)} 条用例）")
    lines.append(f"- 故障注入状态：{meta.get('injection_note', '未注入/未检查')}")
    lines.append(f"- 描述符目录：`{meta.get('descriptor_dir', '')}`")
    lines.append(f"- 知识库目录：`{meta.get('docs_root', '')}`（{meta.get('doc_count', 0)} 篇：{', '.join(meta.get('docs', [])) or '无'}）")
    lines.append(f"- RAG 检索模式：{meta.get('rag_mode', '')}")
    lines.append(f"- 模型覆盖：`{meta.get('model_override') or '(默认路由)'}`（model_mode={meta.get('model_mode', 'auto')}）")
    lines.append(f"- 并发度：{meta.get('concurrency', 1)}；单用例超时：{meta.get('timeout_s', '-')}s")
    lines.append(f"- 消融组：{', '.join(ordered_groups)}")
    lines.append(f"- 主要指标组：**{primary}**（下方「总体指标」「分故障类型」均指该组）")
    if meta.get("proxy_notes"):
        lines.append(f"- 环境修正：{'；'.join(meta['proxy_notes'])}")
    lines.append("")

    lines.append(f"## 总体指标（{primary}）")
    lines.append("")
    lines.append(_metric_table(overall, CORE_METRICS))
    lines.append("")

    lines.append(f"## 分故障类型指标（{primary}）")
    lines.append("")
    lines.append(_fault_table(summary.get("by_fault_type", {}), FAULT_TABLE_METRICS))
    lines.append("")

    if len(ablations) > 1:
        lines.append("## 消融对比")
        lines.append("")
        lines.append(_ablation_table(ablations, ABLATION_METRICS))
        lines.append("")
        lines.append("消融组定义：")
        for group in ordered_groups:
            lines.append(f"- `{group}`：{ablations.get(group, {}).get('description', '')}")
        lines.append("")

    lines.append(f"## 失败用例（{primary} 组，按需展开）")
    lines.append("")
    lines.append(_failure_lines([row for row in summary.get("cases", []) if row.get("ablation") == primary]))
    lines.append("")

    lines.append("## 指标口径")
    lines.append("")
    for key in CORE_METRICS:
        lines.append(f"- `{key}`：{METRIC_FORMULAS.get(key, '')}")
    lines.append(f"- `tool_degraded_rate`：{METRIC_FORMULAS['tool_degraded_rate']}")
    lines.append("")
    lines.append(
        "线上对齐：`evidence_coverage_rate` ↔ `analytics.diagnosis_evidence_rate_pct`；"
        "`task_success_rate` 是 analytics `diagnosis_success_rate_pct` 的离线严格版"
        "（线上只统计诊断是否完成，离线额外要求工具命中 + 证据词命中 + 无幻觉）。"
    )
    lines.append("")
    lines.append("产物：`summary.json`（机器可读）、`summary.md`（本文件）、`trajectories.jsonl`（每条用例完整 tool_calls 轨迹）。")
    lines.append("")
    return "\n".join(lines)


def write_report(records: Sequence[dict], cases: Sequence[dict], meta: dict, out_dir: str | Path) -> dict:
    """写出三件套，返回路径与 summary。"""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    summary = build_summary(records, cases, meta)
    summary_path = write_summary_json(summary, target)
    markdown_path = target / "summary.md"
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    trajectories_path = write_trajectories(records, target)
    return {
        "out_dir": str(target),
        "summary_json": str(summary_path),
        "summary_md": str(markdown_path),
        "trajectories": str(trajectories_path),
        "summary": summary,
    }


def default_out_dir(base: str | Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path(base) / stamp
