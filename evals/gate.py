"""评测门禁：把 summary 指标与阈值对比，作为 CI / 发布回归的"闸门"。

用法（在 run_eval 里）：
    python evals/run_eval.py --run --ablation all --gate \
        --thresholds evals/thresholds.json

阈值文件（JSON）示例：
    {
      "ablation": "full",
      "min": { "task_success_rate": 70, "tool_selection_f1": 0.6, "evidence_coverage_rate": 50 },
      "max": { "hallucination_rate": 10, "errors": 0 }
    }

- `ablation` 指定看哪个消融组；缺省用 summary 的主组。
- `min` 为下限（实际值须 >= 阈值）；`max` 为上限（实际值须 <= 阈值）。
- 任一指标缺失或越界即判为「未通过」。
"""
from __future__ import annotations

import json
from pathlib import Path


def load_thresholds(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("阈值文件必须是 JSON 对象")
    if not (data.get("min") or data.get("max")):
        raise ValueError("阈值文件至少要有一项 min 或 max")
    return data


def evaluate(summary: dict, thresholds: dict) -> list[dict]:
    """返回逐项判定：[{metric, op, threshold, actual, passed}]。"""
    meta = summary.get("meta", {}) or {}
    group = thresholds.get("ablation") or meta.get("primary_group") or "full"
    metrics = (
        (summary.get("ablations", {}) or {}).get(group, {}).get("overall")
        or summary.get("overall", {})
        or {}
    )

    rows: list[dict] = []
    for metric, floor in (thresholds.get("min") or {}).items():
        actual = metrics.get(metric)
        rows.append({
            "metric": metric, "op": ">=", "threshold": floor, "actual": actual,
            "passed": actual is not None and actual >= floor,
        })
    for metric, ceil in (thresholds.get("max") or {}).items():
        actual = metrics.get(metric)
        rows.append({
            "metric": metric, "op": "<=", "threshold": ceil, "actual": actual,
            "passed": actual is not None and actual <= ceil,
        })
    return rows


def passed(rows: list[dict]) -> bool:
    return bool(rows) and all(row["passed"] for row in rows)


def format_rows(rows: list[dict]) -> str:
    if not rows:
        return "  （未配置任何阈值）"
    width = max(len(row["metric"]) for row in rows)
    lines = []
    for row in rows:
        mark = "✅" if row["passed"] else "❌"
        lines.append(f"  {mark} {row['metric']:<{width}} {row['op']} {row['threshold']}  实际={row['actual']}")
    return "\n".join(lines)
