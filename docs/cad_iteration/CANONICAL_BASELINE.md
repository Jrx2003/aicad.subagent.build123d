# Canonical Runtime Baseline

## Scope

This record freezes the current benchmark-facing baseline for the Build123d V2 runtime.

It is intentionally narrower than a full architecture refactor plan:

1. public contracts stay stable
2. the live control lane is explicit
3. the canary benchmark slice is fixed
4. baseline metrics are defined from existing run artifacts

## Stable External Contract

The following surfaces must remain stable while internal convergence work continues:

1. `IterationRequest`
2. `IterationRunResult`
3. CLI `aicad-iter-run`
4. benchmark entry `benchmark/run_prompt_benchmark.sh`
5. canonical MCP tool names:
   - `execute_build123d`
   - `apply_cad_action`
   - `query_*`
   - `query_kernel_state`
   - `patch_domain_kernel`
   - `validate_requirement`

## Canonical Live Lane

Current live-lane policy:

1. `execute_build123d` is the default first write.
2. `apply_cad_action` is a bounded local finishing lane, not a general planner surface.
3. `validate_requirement` is the completion judge and must not become the default per-turn driver.
4. `DomainKernelState` is the semantic authority for blocker, repair, and family-level guidance.

## Diagnostics-Only Legacy Surface

The following artifacts may still exist in traces and prompt diagnostics, but they are not the preferred live control surface:

1. `active_surface`
2. `surface_policy`
3. `relation_focus`
4. `relation_eval`
5. `feature_agenda`

If new logic needs to influence the loop, it should first be expressed through:

1. tool policy
2. domain-kernel evidence
3. repair packet / patch guidance
4. validator/core evidence

## Canary Benchmark Set

The fixed canary set currently lives in `benchmark/canary_case_sets.json` under `canary`.

Current required canary cases:

1. `L1_122`
2. `L1_148`
3. `L1_157`
4. `L1_159`
5. `L2_88`
6. `L2_130`
7. `L2_149`
8. `L2_172`

## Baseline Metrics

Baseline metrics are computed directly from existing benchmark artifacts rather than a second reporting pipeline.

Definitions:

1. `first_solid_success_rate`
   - share of selected cases whose round digest shows a successful write with positive solid evidence
2. `requirement_complete_rate`
   - share of selected cases with `summary.validation_complete=true`
3. `runtime_rewrite_rate`
   - `(total write turns after the first write) / total write turns`
4. `mean_repair_turns_after_first_write`
   - average remaining planner turns after the first positive solid
5. `stale_evidence_incidents`
   - aggregate stale-probe carries plus freshness/evidence conflicts
6. `tokens_per_successful_case`
   - average total tokens across `status=PASS` cases
7. `family_repair_packet_hit_rate`
   - among cases where the domain kernel exposed repair-packet evidence, share that ended in `status=PASS`

## Required Outputs

Every benchmark run should keep the same source of truth:

1. `summary.json`
2. `brief_report.md`
3. `run_diagnostics.md`

These outputs should expose the same baseline metrics and use the same canary selection semantics.
