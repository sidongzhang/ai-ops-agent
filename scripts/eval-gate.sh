#!/usr/bin/env bash
# 评测门禁：默认跑「轻量冒烟」（不依赖 Docker / LLM），传 --full 再加一道真实评测门禁。
#
#   冒烟（CI 每次跑）：数据集校验 → 脚手架自测 → dry-run 门禁（链路可用 + 指标不回归）
#   真实（发布前 / 夜间）：--run --ablation all 门禁（需 Docker + LLM 额度）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/backend/.venv/bin/python}"
cd "$ROOT"

echo "== 1/3 数据集校验 =="
"$PY" evals/run_eval.py --validate

echo "== 2/3 评测脚手架自测 =="
"$PY" -m pytest evals/ -q

echo "== 3/3 dry-run 门禁（链路冒烟，阈值 evals/thresholds.smoke.json）=="
"$PY" evals/run_eval.py --dry-run --gate --thresholds evals/thresholds.smoke.json

if [ "${1:-}" = "--full" ]; then
  echo "== 4/4 真实评测门禁（需 Docker + LLM，阈值 evals/thresholds.json）=="
  "$PY" evals/run_eval.py --run --ablation all --gate --thresholds evals/thresholds.json
fi

echo "✅ 评测门禁通过"
