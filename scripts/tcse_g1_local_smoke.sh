#!/usr/bin/env bash
# Run the same G1 model-swapped smoke on any Linux Docker host.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
: "${RRSI_POLICY_MODEL:=ollama_chat/qwen2.5-coder:1.5b}"
: "${RRSI_POLICY_API_BASE:=http://127.0.0.1:11434}"
: "${RRSI_SEARCH_MODEL:=$RRSI_POLICY_MODEL}"
: "${RRSI_SEARCH_API_BASE:=$RRSI_POLICY_API_BASE}"
: "${RRSI_SEARCH_BACKEND:=litellm}"
: "${RRSI_POLICY_MAX_TURNS:=12}"
export RRSI_POLICY_MODEL RRSI_POLICY_API_BASE RRSI_SEARCH_MODEL RRSI_SEARCH_API_BASE RRSI_SEARCH_BACKEND RRSI_POLICY_MAX_TURNS
command -v docker >/dev/null || { echo 'Docker missing' >&2; exit 2; }
docker info >/dev/null || { echo 'Docker daemon unavailable' >&2; exit 2; }
test -x domains/coding/.venv/bin/harbor || { echo 'Install Harbor to domains/coding/.venv before running' >&2; exit 2; }
python3 -m pytest -q tests
python3 rrsi.py --domain coding smoke
