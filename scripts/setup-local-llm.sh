#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${LLM_LOCAL_MODEL:-qwen2.5:0.5b}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"
PULL_EMBEDDING="${PULL_EMBEDDING:-0}"

cd "$ROOT_DIR"

echo "Starting local LLM service with Docker Compose..."
docker compose --profile llm up -d ollama

echo "Waiting for Ollama to become ready..."
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama did not become ready on http://localhost:11434" >&2
  exit 1
fi

echo "Pulling chat model: ${MODEL}"
docker compose --profile llm exec -T ollama ollama pull "$MODEL"

if [[ "$PULL_EMBEDDING" == "1" ]]; then
  echo "Pulling embedding model: ${EMBEDDING_MODEL}"
  docker compose --profile llm exec -T ollama ollama pull "$EMBEDDING_MODEL"
fi

cat <<EOF

Local LLM is ready.

Use these environment variables for backend local mode:
  LLM_MODE=local
  LLM_LOCAL_BASE_URL=http://localhost:11434/v1
  LLM_LOCAL_API_KEY=ollama
  LLM_LOCAL_MODEL=${MODEL}

Optional local embeddings:
  EMBEDDING_BASE_URL=http://localhost:11434/v1
  EMBEDDING_API_KEY=ollama
  EMBEDDING_MODEL=${EMBEDDING_MODEL}
EOF
