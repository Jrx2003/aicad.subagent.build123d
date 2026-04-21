# Upgrade Roadmap (Execution Order)

This roadmap is intentionally ordered and should be executed in sequence.

## Stage 2 - System Record First

Goal: make design intent discoverable and stable for agents.
Status: implemented.

Done criteria:

1. Agent-readable index and machine-readable record exist.
2. CAD iteration intent, loop, and tool plan are canonicalized.
3. Existing descriptive docs reference this canonical set.

## Stage 1 - Queryable CAD State

Goal: upgrade from coarse statistics to queryable state objects.
Status: active focus.

Done criteria:

1. `query_snapshot`, `query_geometry`, `validate_requirement` are implemented.
2. `render_view` supports custom camera inspection for the current session step.
3. `render_view` has explicit degraded path (preview fallback) when custom render is unavailable.
4. Sub Agent can consume these outputs in planning context.
5. Tests cover tool contracts and planner data flow.
6. Planner supports inspection-only rounds and windowed retrieval (`offset + focus`).

## Stage 3 - Mechanical Guardrails

Goal: enforce quality via deterministic checks.
Status: deferred.

Done criteria:

1. Action plans are validated before execution.
2. Unsupported or unsafe plans produce structured feedback.
3. Completion requires explicit validation evidence.

## Stage 4 - Throughput and Entropy Control

Goal: maintain performance and state hygiene at scale.
Status: deferred.

Done criteria:

1. Avoid redundant full-history execution where unnecessary.
2. Session GC policy is active.
3. Long-run behavior remains stable.

## Stage 5 - Higher Autonomy

Goal: move from plan-execute to plan-execute-verify-repair.
Status: deferred.

Done criteria:

1. Requirement validation participates in convergence decisions.
2. Attempt metrics include autonomy stage telemetry.
3. Repair loop uses structured failure and validation evidence.

## Stage 6 - Legacy Planner Cleanup

Goal: remove the old planner/codegen chain and keep historical material out of
the live architecture narrative.
Status: implemented.

Done criteria:

1. `src/sub_agent/*` is removed from the live package set.
2. Legacy prompt resources are removed from the active runtime path.
3. Retired planning artifacts no longer appear in canonical runtime docs as live routes.
4. Historical work logs, interview prep, and old plans/specs live under `docs/archive/`.

## Stage 7 - Hotspot Deconcentration

Goal: keep the current four-subdomain structure frozen while moving remaining real logic
out of oversized hotspot files and into the owner modules that already exist.
Status: active focus.

Done criteria:

1. `orchestration/policy/shared.py` no longer owns lane-specific code-repair or local-finish helpers.
2. `orchestration/policy/shared.py` no longer owns auto-validation/result helpers or repair/failure-cluster helpers that have clear owner modules.
3. `orchestration/policy/shared.py` no longer owns semantic-refresh lookback helpers that belong in `semantic_refresh.py`.
4. `tooling/execution/batch.py` keeps execution coordination only and no longer owns lint/routing/recipe logic.
5. `tooling/execution/__init__.py` no longer re-exports lint family detector implementations as a shadow owner surface.
6. `tooling/lint/preflight.py` depends on `lint/{families,ast_utils,routing,recipes}` directly instead of reflecting rules back through execution.
7. `prompting/skill_assembly.py` only changes through owner cleanup and requirement-semantic dedupe; no new package layers are introduced.
8. Canonical docs continue to describe this phase as frozen structure plus hotspot deconcentration, not another architecture expansion.
