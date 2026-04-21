# Iteration Protocol

## Loop Contract

The runtime has a single canonical execution path:

1. build prompt bundle from current run state
2. let the model choose tool calls directly
3. execute tools under runtime turn policy
4. sync domain-kernel state from fresh evidence
5. persist artifacts and decide continue / stop

There is no legacy planner/runtime path in the canonical protocol.

## Internal Runtime Structure

The stable external shell stays compatible, but the internal runtime is organized into four subdomains:

1. `sub_agent_runtime.orchestration`
   - run lifecycle
   - turn loop facade
   - turn policy lanes under `orchestration.policy.*`
   - stopping and artifact persistence
2. `sub_agent_runtime.prompting`
   - context assembly
   - runtime guidance
   - skill assembly in `skill_assembly`
   - requirement detectors in `requirements`
   - failure handling in `failures`
   - diagnostics inclusion policy
3. `sub_agent_runtime.semantic_kernel`
   - `DomainKernelState`
   - sync / patches / digest surfaces
   - bootstrap, binding, instance, taxonomy, and recipe internals
4. `sub_agent_runtime.tooling`
   - tool catalog
   - execution layer under `tooling.execution.*`
   - result normalization
   - runtime adapters
   - lint routing / families under `tooling.lint.*`

The unit test tree mirrors these boundaries:

1. `tests/unit/sub_agent_runtime/orchestration`
2. `tests/unit/sub_agent_runtime/prompting`
3. `tests/unit/sub_agent_runtime/semantic_kernel`
4. `tests/unit/sub_agent_runtime/tooling`

## Turn Rules

1. Multiple read tools may run in one turn when safe.
2. At most one write tool may run in one turn.
3. `execute_build123d` is the default first write.
4. `execute_repair_packet` is the preferred repair write when the latest `FamilyRepairPacket` is runtime-supported and exposes an executable recipe contract.
5. If the latest packet is descriptive only or runtime-unsupported, its recipe/skeleton should stay as next-turn `execute_build123d` guidance rather than being treated as an executable packet lane.
6. `apply_cad_action` is only for bounded local finishing after a stable code-backed host already exists.
7. `apply_cad_action` uses the canonical shape `action_type + action_params`; face/edge/diameter/center/depth fields belong in `action_params`, not at the top level.
8. `validate_requirement` is a judge, not a per-turn mandatory action.
9. `execute_build123d` may be rejected before sandbox execution by deterministic preflight lint when the code already contains known-invalid legacy API or argument usage.
10. Known-invalid Build123d helper guesses such as bare `subtract(...)`, bare `rotate(...)`, bare `shell(...)`, wrong countersink helper names such as `CountersinkHole(...)`, nonexistent semi-profile helpers such as `Semicircle(...)`, unsupported primitive/workplane keywords such as `Cylinder(..., axis=...)`, `Box(..., depth=...)`, `Circle(..., arc_size=...)`, `CenterArc(..., end_angle=...)`, `Plane.rotated(..., (0, 0, 0))`, or `countersink_radius=...`, and `CounterSinkHole(...)` misused inside `BuildSketch(...)`, should be handled as preflight repair surfaces rather than spent as full sandbox retries.
11. Invalid Python syntax or indentation inside `execute_build123d` should also be converted into a preflight repair surface so repeated failures stay on direct code repair instead of falling into probe-only semantic refresh loops.
12. Nested `BuildPart()` cutter arithmetic inside an active host builder, for example `part.part -= cutter.part` after building the cutter in a nested builder block, should also be treated as a preflight repair surface because repeated subtractive placements can collapse to the wrong location even when the script executes successfully.

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

When a `validate_requirement` result in the current round already returns `is_complete=true`, the runtime should stop in that same round. It must not schedule one more planner turn just to rediscover completion.

Runtime should continue when:

1. fresh blockers exist after a write
2. fresh geometry evidence contradicts older diagnostics
3. a kernel refresh is required after repeated read-only or repeated failed repair turns
4. fresh validation is still incomplete because evidence is insufficient or the validator explicitly hints `inspect_more_evidence`

Inside an active post-solid sketch window used for `local_finish`, the default closure bias is:

1. after `create_sketch(face_ref=...)`, prefer the next `apply_cad_action` to add the first profile or direct feature mutation
2. after a fresh profile/path add, prefer `apply_cad_action` again to materialize the cut/extrude/hole before `query_sketch`
3. do not spend `apply_cad_action` on `get_history`, rollback, or other session-control escapes while the turn is still constrained to `local_finish`

## Kernel Sync

The kernel must sync after:

1. persisted writes
2. validation
3. feature probes
4. manual kernel patches

The runtime should always prefer the freshest family-scoped repair evidence over older generic fallback evidence.

When fresh validation remains incomplete because evidence is insufficient, the next turn should refresh semantic evidence with `query_kernel_state` and/or `query_feature_probes` before another broad whole-part rewrite, unless the kernel already exposes an actionable repair patch or packet.

Preferred probe families should come from the freshest available source in this order:

1. successful feature-probe detections
2. active domain-kernel repair packets or repair patches
3. latest validation blocker taxonomy
4. requirement semantics fallback

When fresh blockers persist after a write, runtime should prefer a `FamilyRepairPacket`-backed repair lane over a generic free-form retry whenever the kernel already has enough host, anchor, and parameter evidence to narrow the repair. If the packet is runtime-supported, that lane should be `execute_repair_packet`; otherwise it should remain a code-first `execute_build123d` repair guided by the packet recipe/skeleton.

For recurring families with low-variance repair recipes, the packet should carry a compact executable skeleton instead of only prose. Current examples include:

1. centered explicit-anchor countersink arrays
2. centered spherical-recess patterns on a host face plane

For direct spherical-recess geometry coming from `execute_build123d` or `execute_repair_packet`, validator/probe fallback should accept host-plane circle-equivalent evidence even when the snapshot's spherical faces do not carry explicit radius fields, as long as the sphere bbox and host-plane circular edge set recover the required local centers.

For direct spherical-recess geometry where Build123d exposes spherical face surface centroids instead of true sphere centers, validator/probe fallback should recover the local feature center from the spherical face bbox midpoint on in-plane axes together with the host-plane coordinate on the target normal axis, instead of treating the raw spherical face surface centroid as the recess center.

For explicit cylindrical-slot / cutting-cylinder requirements that name a host outer face such as the top surface, validator/probe fallback should recover the slot centerline on the host-normal axis from the host outer-face bound and the cutter radius. Raw cylindrical `axis_origin` or cylindrical surface centroids must not be treated as authoritative slot-center evidence when the realized half-cylinder wall is truncated by the host solid.

When a write fails before sandbox execution because preflight lint catches a known-invalid API surface, runtime should expose the lint hits and any family-specific recipe hint as the next-turn repair surface instead of collapsing that failure into a generic syntax/runtime retry.

When that compacted failure surface is serialized back into the next prompt, repeated lint hits with the same `rule_id` should be deduplicated and annotated with `occurrence_count` so prompt budget stays available for distinct failures and repair recipes.

Repeated artifactless `execute_build123d` failures that are already classified as concrete API/lint/syntax/boolean-shape mistakes should remain on the code-repair lane for the next turn. They should not be forced into a probe-first semantic refresh loop unless the failure stops being concretely classifiable.

For low-variance requirement families, preflight lint should prefer a compact repair recipe over generic prose alone. Current high-value examples include:

1. centered explicit-anchor countersink arrays
2. explicit cylindrical slot / cutting-cylinder boolean rebuilds
3. repeated subtractive families that accidentally use nested `BuildPart()` cutter arithmetic inside the host builder
4. half-shell / split-bearing first-pass rebuilds where explicit outer/inner radii and straight length make a same-builder `Cylinder + SUBTRACT + INTERSECT` recipe lower-risk than an improvised arc-wire profile

For explicit-anchor countersink arrays on centered hosts, the repair surface should preserve two separate placement facts:

1. face-sketch coordinates may need corner-to-centered translation in local XY
2. `CounterSinkHole(...)` still must be placed on the actual host-face plane, for example with `Locations((x, y, top_z), ...)`, instead of remaining on the default XY mid-plane

For directional-drilling requirements, runtime guidance should keep plane-normal translation and in-plane coordinates separate. In particular:

1. `Plane.XY.offset(d)` translates along Z
2. `Plane.XZ.offset(d)` translates along Y
3. `Plane.YZ.offset(d)` translates along X

That normal-translation contract should be exposed to the model so it does not try to encode an XZ in-plane `z` coordinate with `Plane.XZ.offset(z0)` or guess an origin tuple for `Plane.rotated(...)`.

`execute_build123d_probe` is diagnostics-only, but its snapshot-hydration wrapper should still define a safe empty `result` fallback when the probe script only prints or inspects state. Missing `result = ...` in a probe must not turn into a synthetic `NameError` that hides the real diagnostic evidence.

Runtime loop state must preserve the validation assessment fields needed for turn policy, not only the blocker list. At minimum, the live `latest_validation` surface used by policy/prompt building should retain:

1. `insufficient_evidence`
2. `coverage_confidence`
3. `observation_tags`
4. `decision_hints`
5. `clause_interpretations`

Generic clause interpretation should only ground overall body-envelope dimensions against the final part bbox. Feature-local radii, pattern directions/counts, slot tool spans, and similar local construction clauses should instead resolve through family-specific geometry checks or remain explicitly unresolved; they must not be contradicted by whole-body bbox fallback alone.

Axisymmetric local clauses should prefer recovered cylindrical-face band evidence and signed axis spans before any generic bbox fallback. Current examples include stepped-shaft segment radii/lengths, local profile-point clauses, plane-anchored disk/boss spans, and symmetric-extrude wording about a named plane.

Tutorial-style setup or construction narration such as part-file creation, unit setup, global coordinate-system setup, default sketch-plane narration, half-sectional-view instructions, profile-closing narration, or explicit revolve-method wording should be marked `not_applicable` or otherwise non-blocking when it does not directly constrain the final artifact beyond geometry that is already verified elsewhere.

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
