# Observability

This project treats intermediate evidence as first-class output.

## Where to inspect

1. Planner context (compact): `prompts/round_XX_request.json`
2. Planner context (full raw): `prompts/round_XX_request_full.json`
3. Planner actual user text: `prompts/round_XX_user_prompt.txt`
4. Planner output: `plans/round_XX_response.json`
5. Action execution request: `actions/round_XX_action_YY_request.json`
6. Action execution result: `actions/round_XX_action_YY_result.json`
7. Tool outputs: `queries/round_XX_*.json`
8. Final and intermediate artifacts: `outputs/`
9. Run-level status: `summary.json`
10. Process trace timeline: `trace/events.jsonl`
Run roots:

1. Probe/manual runs: `test_runs/<timestamp>/`
2. Benchmark runs: `benchmark/runs/<timestamp>/<case_id>/`

## Inspection-first behavior

Planner may return:

```json
{"actions": [], "inspection": {"query_geometry": {...}, "render_view": {...}}}
```

When that happens, runtime records an inspection-only round and refreshes evidence before next planning round.

## Planner intent visibility

Planner intent is exposed as `planner_note`:

1. `plans/round_XX_response.json -> planner_note`
2. `trace/events.jsonl` `planner_response` events include `planner_note`

`planner_note` is designed as a short action-intent sentence so each round can be read like:
plan -> tool call -> evidence -> next plan.

## Action execution policy visibility

Runner emits per-round action execution policy in `trace/events.jsonl`:

1. `event=action_execution_policy`
2. `payload.policy`:
   - `configured_single_action`
   - `configured_batch`
   - `bootstrap_to_first_solid`
   - `bootstrap_to_first_solid_compressed`
   - `complete_sketch_window`
   - `endgame_multi_action_no_solid`
3. `planned_action_count` vs `selected_action_count`

`bootstrap_to_first_solid` means runtime intentionally executes multiple planned actions
in the same round (despite one-action mode) until the first solid-building action
(`extrude`/`cut_extrude`/`revolve`/`loft`) to avoid ending early on sketch-only state.

Runtime can also rewrite unsafe actions before execution:

1. `event=action_rewritten`
2. `payload.reason` describes rewrite intent.
3. Current rewrite reasons include:
   - `cut_extrude_without_solid_promoted_to_extrude`
   - `cut_extrude_without_solid_promoted_to_extrude_with_requirement_distance`
   - `cut_extrude_distance_aligned_with_requirement`
   - `extrude_symmetric_requirement_applied`
   - `create_sketch_plane_aligned_with_primary_extrude_axis`
   - `create_sketch_plane_aligned_with_primary_extrude_axis_and_centerline_position_inferred`
   - `create_sketch_centerline_position_inferred_from_requirement`
   - `secondary_profile_before_first_solid_skipped_for_notch_requirement`
   - `add_rectangle_position_inferred_for_notch_top_cut`
   - `add_circle_position_normalized_to_centered_array`
   - `extrude_promoted_to_cut_extrude_for_subtractive_requirement`
   - `redundant_create_sketch_without_solid_skipped`
   - `decorative_action_not_in_requirement_skipped`

No-op diagnostics are also surfaced:

1. `event=action_noop_detected`
2. payload includes `action_type`, `step_hash`, and reason.
3. `summary.json -> no_op_action_count` aggregates the run-level count.

## Render evidence visibility

For each round, inspect `queries/round_XX_*.json -> render_view`:

1. `view_file`: selected image file used as visual evidence.
2. `camera.render_fallback_used`:
   - `false`: custom camera render output exists.
   - `true`: fallback preview image selected.
3. `camera.fallback_view_file`: actual fallback filename when applicable.
4. `camera.render_source`:
   - `custom_render`
   - `custom_render_with_warning`
   - `preview_fallback`
   - `none`

For action rounds, inspect `queries/round_XX_action_YY.json -> resolved_render_view_options`:

1. `requested_step`: planner-requested render step (if any).
2. `step`: runtime-resolved render step actually sent to tool.
3. `step_adjusted_to_latest`:
   - `true`: requested step was behind latest action state and auto-clamped upward.
   - `false`: no clamp needed.
4. `intent`:
   - `global_overview`: whole-model view
   - `detail_check`: local feature inspection (often with target ids / focus window)

Prompt-image suppression markers (to avoid empty/axes-only image evidence):

1. `render_view.image_suppressed_for_prompt`
2. `render_view.image_suppression_reason` (e.g. `non_informative_render`)

## Token visibility

Per run token totals are persisted in `summary.json -> token_usage`:

1. `input_tokens`
2. `output_tokens`
3. `total_tokens`
4. `rounds_with_usage`
