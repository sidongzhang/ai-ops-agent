"""对比 prompt 改造前后的评测结果：RCA / Tool-F1 / 成功率 / tokens。"""
import json
import sys
from collections import defaultdict

if len(sys.argv) < 2:
    print("用法: python compare_eval.py <新结果目录> [旧结果目录]")
    sys.exit(1)
new_dir = sys.argv[1]
old_dir = new_dir + "  # 默认与第三轮基线对比"
old_path = "evals/results/20260922-082453/summary.json"

def load_summary(d):
    return json.load(open(f"evals/results/{d}/summary.json"))

def agg(summary, groups=("baseline", "playbook", "rag", "full")):
    by = defaultdict(list)
    for c in summary["cases"]:
        by[c["ablation"]].append(c)
    out = {}
    for g, runs in by.items():
        n = len(runs)
        out[g] = {
            "成功率%": round(100 * sum(r["task_success"] for r in runs) / n),
            "RCA": round(sum(r["rca_accuracy"] for r in runs) / n, 3),
            "ToolF1": summary["ablations"].get(g, {}).get("overall", {}).get("tool_selection_f1", "-"),
            "幻觉率%": round(100 * sum(r.get("hallucination", 0) for r in runs) / n, 1),
            "步数": round(sum(r["tool_call_count"] for r in runs) / n, 2),
            "tokens": int(sum(r["total_tokens"] for r in runs) / n),
        }
    return out

old = load_summary("20260922-082453")
new = load_summary(new_dir)
a, b = agg(old), agg(new)

print(f"{'消融组':10s} {'指标':8s} {'旧(基线)':>10s} {'新(prompt改)':>12s} {'Δ':>8s}")
for g in ("playbook", "full"):
    print(f"—— {g} 组 ——")
    for metric in ("成功率%", "RCA", "ToolF1", "幻觉率%", "步数", "tokens"):
        pass
# 更直接：并排打印两轮完整表
for label, data in (("旧（第三轮基线）", a), (f"新（{new_dir}）", b)):
    print(f"\n===== {label} =====")
    for g, m in data.items():
        print(f"  {g:9s} 成功率={m['成功率%']:>3} RCA={m['RCA']} ToolF1={m['ToolF1']} 幻觉={m['幻觉率%']}% 步数={m['步数']} tokens={m['tokens']}")
