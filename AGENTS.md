# AGENTS.md

## Purpose

This repository isolates the iterative CAD generation loop (`sub_agent` + MCP tools) so it can be tested and evolved independently from upstream services.

## Repository Invariants

1. Run artifacts remain fully inspectable:
   - `prompts/`
   - `plans/`
   - `actions/`
   - `queries/`
   - `outputs/`
   - `summary.json`
2. Default run directory naming is timestamp-only:
   - `YYYYMMDD_HHMMSS`
3. Intermediate LLM/tool evidence must not be hidden or removed.
4. Upstream contract remains stable:
   - `sub_agent_runtime.contracts.IterationRequest`
   - `sub_agent_runtime.contracts.IterationRunResult`
5. Behavior changes in the iterative loop require docs updates under `docs/` first.

## Canonical Interfaces

1. Python API:
   - `sub_agent_runtime.runner.IterativeSubAgentRunner`
2. CLI:
   - `aicad-iter-run`
3. Shell probes:
   - `scripts/run_aci_live_probe.sh`
   - `scripts/run_stage1_manual_probe.sh`
4. Benchmark entry:
   - `benchmark/run_prompt_benchmark.sh`

## Agent Working Contract

Before writing code:

1. Read `docs/cad_iteration/SYSTEM_RECORD.json`.
2. Follow `docs/cad_iteration/INDEX.md` read order.
3. Resolve behavior from `docs/cad_iteration/` first; treat absent records as non-existent behavior.

During execution:

1. Prefer `search -> window -> inspect -> act`.
2. Keep prompt evidence compact; expand windows only when needed.
3. Use focused `render_view` (`target_entity_ids` or `focus_center/focus_span`) for local confirmation.

After changes:

1. Run focused unit tests for touched modules.
2. Execute at least one real probe writing to `test_runs/<timestamp>`.
3. Report exact run directory and key evidence files.

## Harness-Oriented Documentation Standard

Docs must make the objective and controls directly usable by agents:

1. Objective function must be explicit (`docs/cad_iteration/DESIGN_INTENT.md`).
2. Runtime loop and stop policy must be explicit (`docs/cad_iteration/ITERATION_PROTOCOL.md`).
3. Tool affordances and defaults must be explicit (`docs/cad_iteration/TOOL_SURFACE.md`).
4. Machine-readable system record must stay current (`docs/cad_iteration/SYSTEM_RECORD.json`).
