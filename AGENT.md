# AGENT.md

## Purpose

This repository is the decoupled runtime for iterative CAD generation after upstream requirement construction.
It exists so the post-main-agent loop can be executed, inspected, and debugged independently.

The core principle is operational, not rhetorical:

`Anything the agent can't access effectively doesn't exist.`

## Mission

Given normalized `requirements`, run an iterative loop that can:

1. Plan CAD actions.
2. Execute through MCP tools.
3. Query state as structured objects.
4. Validate requirement coverage.
5. Persist full evidence for every round.

## Non-Negotiables

1. Keep run artifacts fully inspectable (`prompts/`, `plans/`, `actions/`, `queries/`, `outputs/`, `summary.json`).
2. Test-run directory naming must be timestamp-only: `YYYYMMDD_HHMMSS`.
3. Never hide intermediate LLM/tool evidence.
4. Keep upstream contract stable via:
   - `sub_agent_runtime.contracts.IterationRequest`
   - `sub_agent_runtime.contracts.IterationRunResult`
5. Any iterative-loop behavior change must update `docs/` first.

## Primary Entry Points

1. Python API: `sub_agent_runtime.runner.IterativeSubAgentRunner`
2. CLI: `aicad-iter-run`
3. Live probe: `scripts/run_aci_live_probe.sh`
4. Stage1 tool probe: `scripts/run_stage1_manual_probe.sh`
5. Benchmark runner: `benchmark/run_prompt_benchmark.sh`

## Required Read Order (for Agents)

1. `docs/cad_iteration/SYSTEM_RECORD.json`
2. `docs/cad_iteration/DESIGN_INTENT.md`
3. `docs/cad_iteration/ITERATION_PROTOCOL.md`
4. `docs/cad_iteration/TOOL_SURFACE.md`
5. `docs/cad_iteration/UPGRADE_ROADMAP.md`

## Operating Loop (Search -> Window -> Inspect -> Act)

Use this sequence by default:

1. `search`:
   - `query_geometry` with compact window (`max_items_per_type` low).
   - read `matched_entity_ids` + `next_*_offset`.
2. `window`:
   - advance `solid_offset/face_offset/edge_offset`.
   - avoid requesting full object lists unless required.
3. `inspect`:
   - `render_view` with either:
     - `target_entity_ids` (entity-based focus), or
     - `focus_center + focus_span` (coordinate-based focus).
   - customize `azimuth_deg/elevation_deg/zoom`.
4. `act`:
   - only execute next CAD action(s) after evidence is sufficient.

## Evidence and Artifact Contract

Every run under `test_runs/<timestamp>/` must include:

1. `prompts/round_XX_request.json`: exact planner input.
2. `plans/round_XX_response.json`: raw + normalized planner output.
3. `actions/round_XX_action_YY_result.json`: execution outcomes.
4. `queries/round_XX_*.json`: `query_snapshot/query_geometry/render_view/validate_requirement`.
5. `outputs/`: `final_model.step`, render images, intermediate artifacts.
6. `summary.json`: convergence and failure summary.
7. `run_manifest.json`: reproducibility metadata.

Benchmark runs are separated under:

`benchmark/runs/<timestamp>/<case_id>/`

Each case directory additionally stores:

1. `prompt.txt`
2. `ground_truth.step`
3. `benchmark_case.json`
4. `benchmark_runner.stdout.log`
5. `benchmark_runner.stderr.log`

## Multimodal Mode (Kimi)

Use Kimi when visual evidence should feed planning.

Checklist:

1. `.env` has non-empty `KIMI_API_KEY`.
2. set:
   - `LLM_REASONING_PROVIDER=kimi`
   - `LLM_REASONING_MODEL=kimi-k2-thinking`
3. run:
   - `AICAD_PROBE_ONE_ACTION_PER_ROUND=1 scripts/run_aci_live_probe.sh`
4. verify in artifacts:
   - `summary.json` -> `render_image_attached_for_prompt`
   - `queries/*.json` -> `render_view.view_file` and `camera` metadata.

## Change Protocol

When modifying iterative behavior:

1. Update docs in `docs/` first.
2. Implement code changes.
3. Run focused unit tests for modified modules.
4. Run at least one real probe writing `test_runs/<timestamp>`.
5. Report exact run directory + key files.

## Fast Failure Triage

1. LLM config error: inspect `summary.json.llm_error`.
2. Tool/runtime error: inspect `actions/*.json` + `queries/*.json`.
3. Requirement non-convergence: inspect `validate_requirement` checks/blockers.
4. Visual evidence missing: inspect `queries/*.json.render_view` and `outputs/*.png`.
