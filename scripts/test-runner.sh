#!/usr/bin/env bash
# AIOps 后端测试入口：统一 pytest 调用方式，供 Cursor Agent / 本地开发使用。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"

usage() {
  cat <<'EOF'
用法: ./scripts/test-runner.sh <command> [args...]

命令:
  all                 运行全部后端测试（默认）
  file <path>         运行单个测试文件（相对 backend/tests/ 或绝对路径）
  keyword <expr>      按 pytest -k 表达式过滤
  collect             只收集用例，不执行
  last-failed         只重跑上次失败的用例（需 pytest-cache）

示例:
  ./scripts/test-runner.sh all
  ./scripts/test-runner.sh file test_diagnosis_evidence.py
  ./scripts/test-runner.sh keyword workflow
  ./scripts/test-runner.sh keyword "DiagnoseAndWebhook"
EOF
}

run_pytest() {
  cd "$BACKEND"
  if [[ ! -d .venv ]] && ! command -v uv >/dev/null 2>&1; then
    echo "错误: 未找到 backend/.venv，且系统无 uv。请先安装依赖。" >&2
    exit 1
  fi
  uv run pytest "$@"
}

cmd="${1:-all}"
shift || true

case "$cmd" in
  all)
    run_pytest -q
    ;;
  file)
    target="${1:-}"
    if [[ -z "$target" ]]; then
      echo "错误: 请指定测试文件" >&2
      usage
      exit 1
    fi
    if [[ "$target" != /* ]]; then
      if [[ "$target" != tests/* ]]; then
        target="tests/$target"
      fi
      target="$BACKEND/$target"
    fi
    run_pytest -q "$target"
    ;;
  keyword|k)
    expr="${1:-}"
    if [[ -z "$expr" ]]; then
      echo "错误: 请指定 -k 表达式" >&2
      usage
      exit 1
    fi
    run_pytest -q -k "$expr"
    ;;
  collect)
    run_pytest --collect-only -q
    ;;
  last-failed|lf)
    run_pytest -q --lf
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "未知命令: $cmd" >&2
    usage
    exit 1
    ;;
esac
