# Feature Graph Runtime

## Purpose

The runtime now uses a single canonical semantic state surface:

`DomainKernelState`

It is not a full CAD topology/history kernel yet. It is the runtime-owned semantic state that binds:

1. requirement intent
2. feature families and feature instances
3. current blockers
4. fresh write-backed geometry evidence
5. validation and probe bindings
6. repair-oriented kernel patches

The goal is not to replace geometry queries. The goal is to stop reconstructing the same semantic state from raw evidence every turn.

## Truth-Source Split

1. `DomainKernelState`
   - authoritative semantic coordination state
   - active targets, blocked targets, completion tracking
   - feature instances and repair-oriented patches
2. persisted sandbox session
   - authoritative geometry state
   - snapshots, topology refs, STEP artifacts
3. `validate_requirement`
   - authoritative completion judge through `core_checks`

`DomainKernelState` is canonical for runtime coordination. Geometry and completion still come from tools.

## Current Scope

The current kernel must support:

1. initialization from requirements
2. sync after persisted writes
3. sync after validation and probes
4. compact digest in prompt context through `domain_kernel_digest`
5. traceable bindings and revision history in artifacts
6. repair-oriented semantic objects:
   - `FeatureInstance`
   - `DomainKernelPatch`
   - `FamilyRepairPacket`

The current kernel does not yet provide:

1. exact topology-history reconstruction
2. full kernel-to-Build123d synthesis
3. direct geometry mutation through kernel tools

## Canonical Surfaces

Canonical runtime/kernel names:

1. state class: `DomainKernelState`
2. digest key: `domain_kernel_digest`
3. semantic read tool: `query_kernel_state`
4. semantic patch tool: `patch_domain_kernel`

Compatibility graph aliases are no longer part of the live runtime surface.

Internal implementation now lives under `sub_agent_runtime.semantic_kernel`.
Live semantic-kernel logic lives in:

1. `models`
2. `sync`
3. `patches`
4. `digest`
5. `repair_packets`
6. `bootstrap`
7. `bindings`
8. `instances`
9. `taxonomy`
10. `recipes`

The `_core` module remains an internal bridge only and should not reclaim new semantic implementation.

## Repair-Oriented Objects

`FeatureInstance` carries instance-level semantic ownership:

1. family id
2. host/body attachment
3. blocker ids
4. anchor keys
5. parameter bindings
6. latest repair mode
7. repair intent

`DomainKernelPatch` carries the current compact repair target:

1. affected feature instances
2. affected hosts
3. anchor keys
4. parameter keys
5. recommended repair mode

`FamilyRepairPacket` carries the executable family-level repair surface:

1. feature instance id
2. host frame
3. normalized target anchors
4. realized anchor summary
5. recipe id / compact recipe skeleton
6. repair mode and repair intent

Current high-value packet families include:

1. `explicit_anchor_hole`
   - centered/local center-set normalization
   - countersink-safe `pushPoints(...).cskHole(...)` recipe skeleton
2. `spherical_recess`
   - centered host-face center set
   - sphere-array subtraction recipe skeleton with centers constrained to the host face plane
   - validator/probe bindings may recover the realized center set from host-plane circular edges and spherical-face bbox evidence even when explicit sphere radii are absent in the snapshot face records

These are runtime repair surfaces, not a hidden autonomous geometry engine.

When a write fails before sandbox execution because runtime preflight lint catches a known-invalid legacy API surface, the runtime may expose:

1. structured `lint_hits`
2. a compact `repair_recipe`

alongside the kernel digest so the next write can repair toward the same feature family without waiting for a full validator/probe cycle.

## Sync Rules

Kernel sync must run:

1. once at run start
2. after every persisted write result
3. after validation
4. after feature probes
5. before final failure / stop artifacts are written

Sync must preserve specificity:

1. fresh family-scoped evidence outranks older generic fallback evidence
2. actionable probe/validation bindings may refine the active patch
3. generic fallbacks must not erase a narrower fresh repair surface

## Freshness Rules

After a successful write, older read-side evidence is stale. Runtime must invalidate or clearly mark stale:

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

Prompt-facing evidence priority is:

1. fresh post-write evidence
2. fresh validation blockers
3. `domain_kernel_digest`
4. compacted history

The kernel must not let stale blockers or stale probe results override fresh write-backed evidence.

## Recovery Policy

Kernel-driven recovery in V2 is limited to canonical lanes:

1. `code_first_build`
2. `code_repair_after_validation_blocker`
3. `local_finish`
4. `kernel_refresh`
5. `finish_or_continue_after_judgment`

`apply_cad_action` is allowed only for bounded local finishing after a stable code-backed host already exists.

## Artifacts

Live prompt context exposes only `domain_kernel_digest`.

`domain_kernel_digest` must preserve the latest packet's compact executable fields when present:

1. `latest_repair_packet_family_id`
2. `latest_repair_packet_feature_instance_id`
3. `latest_repair_packet_repair_mode`
4. `latest_repair_packet_host_frame`
5. `latest_repair_packet_target_anchor_summary`
6. `latest_repair_packet_realized_anchor_summary`
7. `latest_repair_packet_recipe_id`
8. `latest_repair_packet_recipe_summary`
9. `latest_repair_packet_recipe_skeleton`

Artifacts may still show:

1. kernel bindings
2. kernel patch summaries
3. revision history
4. repair-mode summaries

The runtime should optimize for one authoritative kernel surface, not mirrored graph aliases.
