#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Run the forked AgentHarness on a harbor dataset.
# Usage: run_eval.sh <job-name> [dataset] [n_attempts] [n_concurrent] [extra harbor flags...]
#   dataset default: terminal-bench/terminal-bench-2-1
#   extra flags: --jobs-dir <dir> (default <this checkout>/runs/jobs), -i <task> ...
# Model/infra invariants are pinned here (frozen; not evolvable).
set -euo pipefail
REPO="${RRSI_CODING_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

JOB_NAME="${1:?job name required}"
DATASET="${2:-terminal-bench/terminal-bench-2-1}"
N_ATTEMPTS="${3:-1}"
N_CONCURRENT="${4:-4}"
shift $(( $# > 4 ? 4 : $# ))

JOBS_DIR="$REPO/runs/jobs"
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs-dir) JOBS_DIR="$2"; shift 2;;
    *) EXTRA+=("$1"); shift;;
  esac
done

export PATH="$REPO/bin:$PATH"            # docker -> sudo -E docker shim
export PYTHONPATH="$(cd "$REPO/../.." && pwd)/third_party"   # so --agent harbor_terminus2:AgentHarness resolves
MODEL_NAME="${MODEL:-${RRSI_POLICY_MODEL:-}}"
if [[ -z "$MODEL_NAME" ]]; then
  echo "ERROR: select a policy model with RRSI_POLICY_MODEL or MODEL" >&2
  exit 2
fi
if [[ "$MODEL_NAME" == vertex_ai/* ]]; then
  : "${VERTEXAI_PROJECT:?Vertex explicitly selected; set VERTEXAI_PROJECT}"
  export VERTEXAI_LOCATION="${VERTEXAI_LOCATION:-global}"
fi
# Harbor accepts repeated --agent-kwarg key=value flags.
BASE_ARGS=()
if [[ -n "${RRSI_POLICY_MAX_TURNS:-}" ]]; then
  [[ "${RRSI_POLICY_MAX_TURNS}" =~ ^[0-9]+$ ]] || { echo "RRSI_POLICY_MAX_TURNS must be positive integer" >&2; exit 2; }
  BASE_ARGS+=(--agent-kwarg "max_turns=${RRSI_POLICY_MAX_TURNS}")
fi
if [[ -n "${RRSI_POLICY_API_BASE:-}" ]]; then
  BASE_ARGS+=(--agent-kwarg "api_base=\"${RRSI_POLICY_API_BASE}\"")
fi
if [[ -n "${RRSI_POLICY_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${RRSI_POLICY_API_KEY}"
fi
if [[ -n "${TCSE_G1_PROGRESS_MODE:-}" ]]; then
  case "$TCSE_G1_PROGRESS_MODE" in off|observe|feedback) ;; *)
    echo "Invalid TCSE_G1_PROGRESS_MODE (off, observe, feedback)" >&2; exit 2 ;;
  esac
fi
VENV="${RRSI_CODING_VENV:-$REPO/.venv}"

exec "$VENV/bin/harbor" run \
  -d "$DATASET" \
  --agent harbor_terminus2:AgentHarness \
  -m "$MODEL_NAME" \
  --agent-kwarg 'llm_kwargs={"num_retries": 10}' \
  "${BASE_ARGS[@]}" \
  -k "$N_ATTEMPTS" \
  -n "$N_CONCURRENT" \
  --jobs-dir "$JOBS_DIR" \
  --job-name "$JOB_NAME" \
  "${EXTRA[@]}"
