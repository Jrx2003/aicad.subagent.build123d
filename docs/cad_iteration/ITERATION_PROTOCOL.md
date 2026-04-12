# Iteration Protocol

## Loop Contract

The runtime has a single canonical execution path:

1. build prompt bundle from current run state
2. let the model choose tool calls directly
3. execute tools under runtime turn policy
4. sync domain-kernel state from fresh evidence
5. persist artifacts and decide continue / stop

There is no legacy planner/runtime path in the canonical protocol.

## Turn Rules

1. Multiple read tools may run in one turn when safe.
2. At most one write tool may run in one turn.
3. `execute_build123d` is the default first write.
4. `apply_cad_action` is only for bounded local finishing after a stable code-backed host already exists.
5. `validate_requirement` is a judge, not a per-turn mandatory action.
6. `execute_build123d` may be rejected before sandbox execution by deterministic preflight lint when the code already contains known-invalid legacy API or argument usage.

## Canonical Turn Lanes

V2 only recognizes these lanes:

1. `code_first_build`
2. `code_repair_after_validation_blocker`
3. `local_finish`
4. `kernel_refresh`
5. `finish_or_continue_after_judgment`

Any older structured-bootstrap or semantic-admission compatibility lane is non-canonical and should not guide new runtime behavior.

## Freshness

After a successful write, older read evidence must be invalidated or treated as stale:

1. `query_kernel_state`
2. `query_snapshot`
3. `query_sketch`
4. `query_geometry`
5. `query_topology`
6. `query_feature_probes`
7. `validate_requirement`
8. `render_view`
9. `get_history`
10. `execute_build123d_probe`

Prompt evidence priority is:

1. fresh post-write evidence
2. fresh validation/core blockers
3. `domain_kernel_digest`
4. compacted history

## Stop / Continue

Runtime should stop when one of these is true:

1. `validate_requirement` says complete through core checks
2. model explicitly calls `finish_run` and runtime sees no stronger contrary evidence
3. no further useful progress is possible and failure artifacts are ready

Runtime should continue when:

1. fresh blockers exist after a write
2. fresh geometry evidence contradicts older diagnostics
3. a kernel refresh is required after repeated read-only or repeated failed repair turns

## Kernel Sync

The kernel must sync after:

1. persisted writes
2. validation
3. feature probes
4. manual kernel patches

The runtime should always prefer the freshest family-scoped repair evidence over older generic fallback evidence.

When fresh blockers persist after a write, runtime should prefer a `FamilyRepairPacket`-backed repair lane over a generic free-form retry whenever the kernel already has enough host, anchor, and parameter evidence to narrow the repair.

For recurring families with low-variance repair recipes, the packet should carry a compact executable skeleton instead of only prose. Current examples include:

1. centered explicit-anchor countersink arrays
2. centered spherical-recess patterns on a host face plane

For direct spherical-recess geometry coming from `execute_build123d` or `execute_repair_packet`, validator/probe fallback should accept host-plane circle-equivalent evidence even when the snapshot's spherical faces do not carry explicit radius fields, as long as the sphere bbox and host-plane circular edge set recover the required local centers.

When a write fails before sandbox execution because preflight lint catches a known-invalid API surface, runtime should expose the lint hits and any family-specific recipe hint as the next-turn repair surface instead of collapsing that failure into a generic syntax/runtime retry.

## Artifact Contract

Every run remains fully inspectable through:

1. `prompts/`
2. `plans/`
3. `actions/`
4. `queries/`
5. `outputs/`
6. `trace/`
7. `summary.json`

Prompt-facing semantic state is canonicalized to `domain_kernel_digest`.
