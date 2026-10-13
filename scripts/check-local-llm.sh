#!/usr/bin/env bash
# 本地模型自检：一条命令回答"本地部署的大模型现在能不能用"
#
# 检查三件事（前两项失败=不能用；第三项失败=最隐蔽的一种"假能用"）：
#   1) 端点可达 + 配置的模型已安装（Ollama 会把无 tag 名字归一化为 :latest）
#   2) 上下文够大：我们的提示 ≈ 9k tokens。上下文设小了 Ollama 会**静默截断**提示，
#      模型看不到工具说明 → 0 工具调用、答非所问，而报告状态仍是 success。
#   3) 报告一条可判断的结论 + 修复建议。
#
# 用法：
#   bash scripts/check-local-llm.sh
#   LLM_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 LLM_LOCAL_MODEL=aiops-qwen2.5-3b bash scripts/check-local-llm.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 后端读的是**仓库根**的 .env（见 backend/app/core/config.py 的 env_file），不是 backend/.env
ENV_FILE="$ROOT/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$ROOT/backend/.env"

# 优先用环境变量；否则读 backend/.env
read_env() { # read_env <KEY> <default>
  local key="$1" default="${2:-}" value=""
  value="$(printenv "$key" 2>/dev/null || true)"
  if [ -z "$value" ] && [ -f "$ENV_FILE" ]; then
    value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  fi
  printf '%s' "${value:-$default}"
}

BASE="$(read_env LLM_LOCAL_BASE_URL 'http://localhost:11434/v1')"
MODEL="$(read_env LLM_LOCAL_MODEL 'qwen2.5:3b')"
HOST_BASE="${BASE%/v1}"

say() { printf '%s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
bad() { printf '  ✗ %s\n' "$*"; }

say "本地模型自检"
say "  端点: $BASE"
say "  模型: $MODEL"
say ""

# ---- 1) 可达 + 已安装 ----
if ! curl -s --noproxy '*' --max-time 5 "$HOST_BASE/api/tags" >/dev/null 2>&1; then
  bad "连不上 $HOST_BASE —— 请先启动 ollama（例如：ollama serve）"
  exit 1
fi
ok "端点可达"

# 归一化名字比较（无 tag 的模型在列表里是 <name>:latest）
norm() { printf '%s' "${1%:latest}"; }
INSTALLED="$(curl -s --noproxy '*' --max-time 5 "$HOST_BASE/api/tags" \
  | python3 -c 'import json,sys;print("\n".join(m["name"] for m in json.load(sys.stdin).get("models",[])))' 2>/dev/null || true)"
MATCHED=""
while IFS= read -r n; do
  [ -n "$n" ] && [ "$(norm "$n")" = "$(norm "$MODEL")" ] && { MATCHED=yes; break; }
done <<< "$INSTALLED"
if [ "$MATCHED" = "yes" ]; then
  ok "模型已安装（$(norm "$MODEL")）"
else
  bad "模型 $MODEL 未安装。当前可用："
  printf '%s\n' "$INSTALLED" | sed 's/^/      /'
  say "      修复：ollama pull $MODEL  或  bash scripts/prepare-local-model.sh qwen2.5:3b"
  exit 1
fi

# ---- 2) 上下文够大？（关键：防"静默截断"）----
CHARS=6000
LONG="$(python3 -c "print('甲'*$CHARS, end='')")"
RESP="$(curl -s --noproxy '*' --max-time 600 "$HOST_BASE/api/chat" -d "$(python3 - "$MODEL" "$LONG" <<'PY'
import json, sys
print(json.dumps({"model": sys.argv[1], "stream": False,
                  "messages": [{"role": "system", "content": sys.argv[2]},
                               {"role": "user", "content": "答：好"}],
                  "options": {"num_predict": 1}}, ensure_ascii=False))
PY
)")"
PE="$(printf '%s' "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("prompt_eval_count") or 0)' 2>/dev/null || echo 0)"
# 中文约 0.6~0.7 token/字；低于 0.4 倍基本可判定被截断
THRESHOLD=$(( CHARS * 4 / 10 ))
if [ "$PE" -ge "$THRESHOLD" ]; then
  ok "上下文够大：$CHARS 字提示 → prompt_eval=${PE}（阈值 ${THRESHOLD}）"
else
  bad "疑似被**静默截断**：$CHARS 字提示只评了 $PE 个 token（阈值 ${THRESHOLD}）"
  say "      后果：模型看不到工具说明 → 0 次工具调用、答非所问，但报告仍显示 success"
  say "      修复：把 num_ctx 烧进模型 —— bash scripts/prepare-local-model.sh qwen2.5:3b"
  say "            （或让 ollama 带 OLLAMA_CONTEXT_LENGTH=16384 启动）"
  exit 1
fi

say ""
say "结论：**可以使用**（以上两项通过）。"
say "  注意：本地模型在纯 CPU 上会明显慢于 API；有 GPU（如 macOS 原生 Metal）时一次诊断约几十秒。"
say "  若要跑诊断本身，用：model_mode=local（控制台/SVOM 浮窗选「本地模型」）。"
