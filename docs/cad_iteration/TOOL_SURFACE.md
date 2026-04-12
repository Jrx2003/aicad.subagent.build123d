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
