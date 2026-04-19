# Tool Surface

## Canonical Runtime Tools

### Semantic / kernel

1. `query_kernel_state`
2. `patch_domain_kernel`

### Geometry / state reads

1. `query_snapshot`
2. `query_sketch`
3. `query_geometry`
4. `query_topology`
5. `query_feature_probes`
6. `render_view`
7. `get_history`
8. `execute_build123d_probe`

### Writes

1. `execute_build123d`
2. `apply_cad_action`

### Judge / meta

1. `validate_requirement`
2. `finish_run`

Compatibility graph aliases are no longer part of the live model-facing tool surface.

## Default Use Rules

1. `execute_build123d` is the default first write.
2. `apply_cad_action` is not a bootstrap path; it is for bounded post-code local edits.
3. `query_kernel_state` is the canonical semantic readback when the model needs a compact view of blockers, instances, repair patches, and `FamilyRepairPacket` summaries.
4. Kernel readback should preserve the latest packet's executable repair fields when present, including `recipe_id`, `recipe_summary`, and `recipe_skeleton`.
4. `validate_requirement` should be used when completion judgment is needed, not every turn.
5. `execute_build123d_probe` is diagnostics-only and must not mutate the authoritative session.
6. `execute_build123d` has a deterministic preflight lint for known-invalid legacy API or keyword surfaces; lint hits should be treated as actionable repair guidance, not as generic runtime noise.
7. Preflight lint should reject both legacy-kernel carryover and known-invalid Build123d call surfaces such as bare helper guesses (`subtract(...)`, `rotate(...)`, `shell(...)`), unsupported primitive keywords (`Cylinder(..., axis=...)`, `Rectangle(..., centered=...)`, `Box(..., depth=...)`, `Circle(..., arc_size=...)`, `CenterArc(..., end_angle=...)`), wrong workplane/rotation guesses such as `Plane.rotated(..., (0, 0, 0))`, nonexistent helpers such as `Semicircle(...)` or wrong countersink helper/keyword guesses (`CountersinkHole(...)`, `countersink_radius=...`), or `CounterSinkHole(...)` placed inside `BuildSketch(...)` instead of `BuildPart(...)`, before sandbox execution.
8. Preflight lint should also reject invalid Python syntax or indentation in `execute_build123d` scripts before sandbox execution so code-repair stays on the write lane instead of degrading into probe-only retries.
9. When the requirement text already implies a low-variance family recipe, preflight lint should expose a compact family-specific repair recipe together with the lint hits instead of only a generic API warning.
10. Compacted `previous_tool_failure_summary` payloads should deduplicate repeated preflight lint hits by `rule_id`, preserve first-seen order, and carry an `occurrence_count` field when the same failure recurs multiple times in one write so the next turn sees distinct repair surfaces instead of prompt spam.
11. Preflight lint numeric-alias evaluation for contract guards such as `RectangleRounded(width, height, radius)` must use bounded source-order convergence. Reassigning the same temporary name in separate loops or builders must not turn lint into an infinite fixed-point scan.
12. If fresh validation says evidence is insufficient, prefer `query_kernel_state` and `query_feature_probes` before another broad rewrite unless a concrete repair patch/packet is already available.
13. Preferred probe families should be biased by the freshest domain-kernel repair state and latest validation taxonomy, not only by raw requirement text.
14. Validation clause interpretation must not use whole-body bbox fallback to contradict feature-local dimensions such as slot cutter span, hemispherical recess radius, or pattern direction/count wording when family-specific geometry checks already provide the authoritative evidence surface.
15. Snapshot-only spherical-recess validation should recover local centers from host-plane-open spherical bbox geometry when raw spherical face centers are only surface centroids.
16. Repeated artifactless Build123d failures that are already classified as concrete API/lint/syntax/boolean-shape mistakes should stay on `execute_build123d` repair rather than being diverted into probe-first semantic refresh by default.
17. Snapshot-only cylindrical-slot validation should recover the slot centerline from the named host outer face and cutter radius when the realized cylindrical wall is truncated, instead of trusting raw cylindrical `axis_origin` or cylindrical surface centroids.
18. Validation clause interpretation may reuse already-passed `dimension_*` checks for single-dimension width/height/length clauses when those clauses are split out of a structured requirement payload and do not carry richer body-shape wording by themselves.
19. `render_view` custom render sampling must tolerate Build123d vector accessor variants (`x/y/z`, `X/Y/Z`, callable accessors) so focused face renders do not fail solely on coordinate-surface incompatibility.
20. Preflight lint should reject `BuildPart.solid` arithmetic misuse such as `part.solid = part.solid - cutter` and surface a builder-native subtract recipe for repeated recess/pattern families.
21. First-pass spherical-recess pattern guidance should surface a builder-native Build123d recipe early: keep the host inside one `BuildPart`, compute the centered center-set explicitly, place cutters with `Locations(...)`, subtract with `Sphere(..., mode=Mode.SUBTRACT)`, and avoid mutating `part.solid` as an arithmetic target.
22. Centered face-pattern guidance should infer explicit local center sets from spacing-and-quantity requirements when the prompt says to center the pattern, and it should treat default centered `Rectangle(...)` / `Box(...)` hosts as origin-centered unless the host was explicitly translated.
23. When the requirement says a hemispherical recess diameter edge coincides with the host face, code-generation guidance should prefer `sphere_center_z = top_face_z` on that host plane, and validation should require host-plane circular opening evidence rather than accepting buried full-sphere voids as equivalent.
24. Preflight lint should reject nested `BuildPart()` cutter arithmetic such as `with BuildPart() as cutter: ...` followed by `part.part -= cutter.part` inside an active host builder, because that pattern does not reliably preserve the active placement context for repeated subtractive features.
25. Countersink guidance should treat `CounterSinkHole(...)` as a `BuildPart` operation that still needs the correct host-face plane placement; for face-local holes on a centered host, the placement should carry both the local XY center and the face-plane translation such as `top_z`.
26. `execute_build123d_probe` snapshot hydration must not crash merely because the diagnostic script omitted an explicit `result = ...`; the runtime wrapper should fall back to an empty `Part()` so probe analysis stays diagnostic instead of failing with `NameError`.
27. Validation evidence should recover compact axisymmetric band facts from cylindrical faces, including dominant axis, radius, and axial window, so axisymmetric clauses can ground against local rotational bands instead of only the whole-body bbox.
28. Axisymmetric clause interpretation should use those band facts to verify stepped shaft segment radii/lengths, profile point clauses such as `R25` / `(25, 15)`, and plane-anchored upward/downward disk or boss spans before falling back to generic bbox contradiction.
29. Symmetric-extrude clauses such as `extrude symmetrically by 15 mm about the XY plane` should verify against the signed axis range and final box dimensions, not be treated as unresolved merely because no explicit structured history entry survived into validation.
30. `validate_requirement` should classify tutorial/setup construction clauses such as part-file creation, unit setup, global coordinate narration, default sketch-plane narration, half-sectional-view instructions, profile-closing narration, and explicit revolve-method wording as `process_setup` so they do not keep otherwise-correct geometry runs incomplete.
31. High-confidence family geometry plus non-blocking process/setup clauses should produce `is_complete=true`; the runtime should not spend extra read turns merely because tutorial-style wording lacks separate geometry evidence.
32. Directional-drilling guidance should treat `Plane.offset(...)` as plane-normal translation only: `Plane.XY.offset(d)` moves along Z, `Plane.XZ.offset(d)` moves along Y, and `Plane.YZ.offset(d)` moves along X. Workplane-offset guesses must not be used as substitutes for in-plane feature coordinates.
33. For half-shell requirements that already provide explicit outer radius, inner radius, and straight length, the preferred first-pass Build123d recipe is one same-builder host using `Cylinder(...)`, `mode=Mode.SUBTRACT`, and `mode=Mode.INTERSECT` before downstream pad/lug/hole edits, rather than a hand-built arc-wire profile unless the profile path is clearly simpler.
34. During `local_finish`, when fresh exact `face_ref` or `edge_refs` already exist, `apply_cad_action` should be spent on a real geometry mutation that consumes those refs; do not use `get_history`, rollback, or similar session-control actions as the next step.
35. After a successful `create_sketch(face_ref=...)`, the next local-finish bias should prefer `apply_cad_action` for profile growth or materialization before `query_sketch`, unless the runtime has evidence that the sketch state itself is missing or invalid.
36. For living-hinge clamshell requirements, the hinge should be treated as a host-owned back-edge strip/flexure by default; do not translate the whole lid or base to the seam coordinate and do not introduce detached hinge barrels/pins unless the prompt explicitly asks for a mechanical or pin hinge.

## Tool Exposure Policy

The runtime may narrow exposed tools per turn, but only within the canonical V2 lane set:

1. code-first build
2. code repair after validation blockers
3. local finish
4. kernel refresh
5. finish or continue after judgment

## Freshness and Readback

After a successful write, stale evidence must not remain authoritative. New prompt context should prefer:

1. fresh post-write geometry evidence
2. fresh validation blockers
3. `domain_kernel_digest`
4. compacted history

`query_feature_probes` and `validate_requirement` are valuable, but older results must not override fresh write-backed evidence.

## Structured Tools

Structured tools still exist, but only as bounded local-finishing tools. They are not the default way to open a new modeling chain in V2.

`structured_bootstrap_rounds` may still be tracked as a regression metric. It is not a canonical policy lane.
