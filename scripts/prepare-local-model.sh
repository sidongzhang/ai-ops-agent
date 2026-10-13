#!/usr/bin/env bash
# 准备一个"上下文够大"的本地模型（可选，但强烈建议）
#
# 为什么需要它：
#   我们的诊断提示（系统提示 + 工具 schema）约 **8~10k tokens**。
#   Ollama 如果上下文设小了（默认远小于此，本仓库的容器曾设 1024），
#   会把提示**静默截断** —— 模型看不到工具说明，于是 0 次工具调用、答非所问，
#   而报告状态仍然是 success（实测 prompt_eval 只有 514 而非 9017）。
#
#   通过 `OLLAMA_CONTEXT_LENGTH` 环境变量可以改，但那依赖"每次都用对的方式启动 ollama"
#   （例如 `brew services start ollama` 就带不上这个变量）。
#   **把 num_ctx 烧进模型** 才是稳的：无论 ollama 怎么启动，上下文都是够的。
#
# 用法：
#   bash scripts/prepare-local-model.sh                    # 默认 qwen2.5:3b → aiops-qwen2.5-3b
#   bash scripts/prepare-local-model.sh qwen2.5:7b         # 指定基础模型
#   OLLAMA_HOST=127.0.0.1:11434 bash scripts/prepare-local-model.sh
#
set -euo pipefail

BASE_MODEL="${1:-qwen2.5:3b}"
CTX="${LOCAL_NUM_CTX:-16384}"
# aiops-qwen2.5-3b：与仓库里已有的 aiops-qwen-0.5b 命名风格一致
NAME="aiops-$(echo "$BASE_MODEL" | tr ':' '-')"

OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama || echo /opt/homebrew/opt/ollama/bin/ollama)}"

if ! "$OLLAMA_BIN" list >/dev/null 2>&1; then
  echo "✗ 连不上 ollama（$OLLAMA_BIN list 失败）。请先启动：ollama serve" >&2
  exit 1
fi

if ! "$OLLAMA_BIN" list | awk '{print $1}' | grep -qx "$BASE_MODEL"; then
  echo "→ 基础模型 $BASE_MODEL 不存在，先拉取…"
  "$OLLAMA_BIN" pull "$BASE_MODEL"
fi

MODELFILE="$(mktemp -t aiops-modelfile.XXXXXX)"
trap 'rm -f "$MODELFILE"' EXIT
{
  echo "FROM $BASE_MODEL"
  echo "# 必须 ≥ 诊断提示规模（系统提示 + 工具 schema ≈ 8~10k tokens），否则会被静默截断"
  echo "PARAMETER num_ctx $CTX"
} > "$MODELFILE"

echo "→ 创建 ${NAME}（FROM ${BASE_MODEL}, num_ctx=${CTX}）…"
"$OLLAMA_BIN" create "$NAME" -f "$MODELFILE"

echo
echo "✓ 完成。把这个写进 backend/.env 并**重启后端**："
echo "    LLM_LOCAL_MODEL=$NAME"
echo
echo "自检（应看到 num_ctx ${CTX}）："
echo "    $OLLAMA_BIN show $NAME --parameters"
