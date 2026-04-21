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
6. default Kimi provider defaults:
   - standard: `kimi-k2.6`
   - reasoning: `kimi-k2.6`

## Canonical Live Lane

Current live-lane policy:

1. `execute_build123d` is the default first write.
2. `apply_cad_action` is a bounded local finishing lane, not a general planner surface.
3. `validate_requirement` is the completion judge and must not become the default per-turn driver.
4. `DomainKernelState` is the semantic authority for blocker, repair, and family-level guidance.

## Retired Planner Artifacts

Planner-local artifacts from the removed legacy planner chain are archived and
must not be reintroduced as the live control surface.

If new logic needs to influence the loop, it should first be expressed through:

1. tool policy
2. domain-kernel evidence
3. repair packet / patch guidance
4. validator/core evidence

## Current Internal Package Map

The live external contract remains frozen, but the current implementation map is:

1. `sub_agent_runtime.orchestration`
   - public orchestration surface
   - live loop shell remains `sub_agent_runtime.orchestration.policy.shared`
   - lane helpers and repair/validation ownership live in `sub_agent_runtime.orchestration.policy.{code_repair,semantic_refresh,local_finish,validation}`
   - `policy.shared` should only keep loop-shell logic, cross-lane shared utilities, and compatibility rebinds
2. `sub_agent_runtime.prompting.context_builder`
   - prompt payload and message-stack orchestration
   - runtime skill assembly lives in `skill_assembly`
   - requirement detectors live in `requirements`
   - failure classification lives in `failures`
3. `sub_agent_runtime.semantic_kernel._core`
   - internal bridge only
   - semantic implementation lives in `bootstrap`, `bindings`, `instances`, `taxonomy`, and `recipes`
4. `sub_agent_runtime.tooling.execution`
   - execution implementation lives in `tooling.execution.batch`
   - `tooling.execution.__init__` is only a thin execution/generic-helper surface
   - lint orchestration lives in `tooling.lint.preflight`
   - lint family owners live in `tooling.lint.families.*`
   - AST helpers live in `tooling.lint.ast_utils`
   - plane-family helpers now live in `tooling.lint.families.planes`
   - tests should import family owners directly instead of using the execution package surface for detector access

Current consolidation rule:
- keep this package map stable
- when a hotspot remains too large, move behavior into the existing owner modules before introducing any new facade or package layer
- current hotspot phase targets:
  - `orchestration/policy/shared.py` -> keep shrinking only if more behavior can move into existing owner modules; the remaining target is loop-shell size rather than lane-helper ownership
  - `tooling/lint/families/builders.py` -> absorb remaining builder-context heuristics that still sit too close to generic execution helpers
  - `tooling/lint/preflight.py` -> keep only lint orchestration plus result assembly
  - `prompting/context_builder.py` -> keep as the prompt assembly surface while moving any obvious non-assembly helper leakage into existing owner modules
  - `prompting/skill_assembly.py` -> limit changes to owner cleanup and requirement-semantic dedupe

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
