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

## Stage 6 - Surface-Bounded ReAct

Goal: constrain each round to one local work region and make repair depend on
explicit expected-vs-observed feedback.
Status: active planning.

Done criteria:

1. Runtime derives `active_surface` for each round.
2. Planner receives `active_surface` and a local `surface_policy`.
3. Planner can emit `expected_outcome`.
4. Runtime computes `outcome_delta` from actual evidence.
5. Benchmark-driven repair uses `active_surface + outcome_delta` before adding
   new global prompt rules.
