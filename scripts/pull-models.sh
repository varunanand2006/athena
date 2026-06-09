#!/usr/bin/env bash
# Pull required models into Ollama via the API.
# Run from any machine that can reach ollama.local (needs /etc/hosts entry).
#
# Usage: ./scripts/pull-models.sh [ollama-base-url]
# Default URL: http://ollama.local
set -euo pipefail

OLLAMA_URL="${1:-http://ollama.local}"
MODELS=(
  "gemma4:e2b"        # swap to gemma4:12b when moving to production use
  "nomic-embed-text"
)

check_ollama() {
  if ! curl -sf "${OLLAMA_URL}/api/tags" >/dev/null; then
    echo "ERROR: Cannot reach Ollama at ${OLLAMA_URL}"
    echo "       Make sure ollama.local is in /etc/hosts and the pod is Running."
    exit 1
  fi
}

pull_model() {
  local model="$1"
  echo ""
  echo "==> Pulling ${model} (this may take a while on CPU-only hardware)..."
  curl -sf "${OLLAMA_URL}/api/pull" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"${model}\"}" \
    --no-buffer | while IFS= read -r line; do
      status=$(echo "${line}" | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || true)
      [ -n "${status}" ] && echo "    ${status}"
    done
  echo "==> ${model} ready."
}

echo "==> Checking Ollama at ${OLLAMA_URL}..."
check_ollama

for model in "${MODELS[@]}"; do
  pull_model "${model}"
done

echo ""
echo "==> All models pulled. Verifying..."
curl -sf "${OLLAMA_URL}/api/tags" | grep -o '"name":"[^"]*"' | cut -d'"' -f4
echo ""
echo "==> Done. Phase gate: curl ${OLLAMA_URL}/api/tags from vlinux1 should list both models."
