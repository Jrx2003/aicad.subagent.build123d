#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLI_LLM_REASONING_PROVIDER="${LLM_REASONING_PROVIDER-}"
CLI_LLM_REASONING_MODEL="${LLM_REASONING_MODEL-}"

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

cd "$ROOT_DIR"
uv run python benchmark/run_prompt_benchmark.py "$@"
