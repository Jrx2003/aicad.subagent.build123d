#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLI_LLM_REASONING_PROVIDER="${LLM_REASONING_PROVIDER-}"
CLI_LLM_REASONING_MODEL="${LLM_REASONING_MODEL-}"
CLI_AICAD_TEST_RUNS_ROOT="${AICAD_TEST_RUNS_ROOT-}"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

if [[ -n "$CLI_LLM_REASONING_PROVIDER" ]]; then
  export LLM_REASONING_PROVIDER="$CLI_LLM_REASONING_PROVIDER"
fi
if [[ -n "$CLI_LLM_REASONING_MODEL" ]]; then
  export LLM_REASONING_MODEL="$CLI_LLM_REASONING_MODEL"
fi
if [[ -n "$CLI_AICAD_TEST_RUNS_ROOT" ]]; then
  export AICAD_TEST_RUNS_ROOT="$CLI_AICAD_TEST_RUNS_ROOT"
fi

RUNS_ROOT="${AICAD_TEST_RUNS_ROOT:-$ROOT_DIR/test_runs}"
if [[ "$RUNS_ROOT" != /* ]]; then
  RUNS_ROOT="$ROOT_DIR/$RUNS_ROOT"
fi
mkdir -p "$RUNS_ROOT"

RUN_ID="${1:-$(date +%Y%m%d_%H%M%S)}"

echo "[aci-live] run_id: $RUN_ID"
echo "[aci-live] runs_root: $RUNS_ROOT"

ARGS=(
  --run-id "$RUN_ID"
  --runs-root "$RUNS_ROOT"
  --max-rounds "${AICAD_PROBE_MAX_ROUNDS:-8}"
  --sandbox-timeout "${AICAD_PROBE_SANDBOX_TIMEOUT:-180}"
)

if [[ -n "${AICAD_PROBE_REQUIREMENTS_FILE-}" ]]; then
  ARGS+=(--requirements-file "$AICAD_PROBE_REQUIREMENTS_FILE")
elif [[ -n "${AICAD_PROBE_REQUIREMENT-}" ]]; then
  ARGS+=(--requirement-text "$AICAD_PROBE_REQUIREMENT")
fi

if [[ "${AICAD_PROBE_ONE_ACTION_PER_ROUND:-1}" == "1" ]]; then
  ARGS+=(--one-action-per-round)
else
  ARGS+=(--batch-actions)
fi
if [[ "${AICAD_PROBE_FORCE_POST_CONVERGENCE_ROUND:-0}" == "1" ]]; then
  ARGS+=(--force-post-convergence-round)
fi
if [[ -n "${AICAD_PROBE_SESSION_ID-}" ]]; then
  ARGS+=(--session-id "$AICAD_PROBE_SESSION_ID")
fi

if [[ -n "${LLM_REASONING_PROVIDER-}" ]]; then
  ARGS+=(--provider "$LLM_REASONING_PROVIDER")
fi
if [[ -n "${LLM_REASONING_MODEL-}" ]]; then
  ARGS+=(--model "$LLM_REASONING_MODEL")
fi

(
  cd "$ROOT_DIR"
  uv run aicad-iter-run "${ARGS[@]}"
)

RUN_DIR="$RUNS_ROOT/$RUN_ID"
ln -sfn "$RUN_DIR" "$RUNS_ROOT/latest"

echo "[aci-live] run_dir: $RUN_DIR"
echo "[aci-live] latest: $RUNS_ROOT/latest"
