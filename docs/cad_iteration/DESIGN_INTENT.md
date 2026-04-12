# Design Intent for Iterative CAD Generation

## Mission

Transform natural-language requirements into manufacturable CAD artifacts through a model-driven tool loop.

The preferred runtime architecture is now:

1. one main agent loop
2. one authoritative semantic state (`DomainKernelState`) inside the runtime
3. direct tool orchestration by the model
4. compact current-state context
5. post-hoc validation and diagnostics
6. thin runtime skill notes when the current failure mode maps to a reusable CAD repair/tool-choice pattern
7. public decision/transcript artifacts that explain the next move without exposing private chain-of-thought

The system defaults to direct Build123d execution for the first write and only uses structured CAD actions when a local post-code edit is materially cheaper.

## Problem Statement

A language model can only make correct CAD decisions when it can:

1. Access current model state with low friction.
2. Decide which tool to call next without going through a heavyweight hard-coded planner shim.
3. Query missing evidence before acting.
4. Validate requirement coverage before stopping.
5. Keep diagnostic artifacts rich without forcing all of that detail back into every planning turn.

## Product Goals

1. `goal_first_solid_fast`: Build first valid solid quickly.
2. `goal_requirement_convergence`: Converge to requirement-complete geometry with traceable reasoning.
3. `goal_diagnosable_loop`: Make every action, state transition, and failure diagnosable.
4. `goal_runtime_stability`: Keep runtime stable as action history grows.
5. `goal_model_driven_orchestration`: Keep the main decision loop model-driven rather than rule-driven.
6. `goal_context_budget_control`: Keep prompt growth bounded through explicit compaction and budget management.
7. `goal_skill_layer_repairs`: Push recurring CAD repair advice into reusable tool/skill guidance instead of runner-local case logic.
8. `goal_semantic_state_authority`: Keep feature-level intent, blockers, and completion state in one authoritative semantic graph instead of scattering them across prompt-local summaries.

## Non-Goals

1. Do not optimize for aesthetically perfect code output.
2. Do not hide uncertainty behind free-form narrative.
3. Do not assume raw long-context memory is reliable without explicit context management.
4. Do not keep moving feature-specific decision logic into runner-level hard-coded repairs.

## Non-Negotiable Constraints

1. `constraint_feedback_every_round`: Every iteration must persist inspectable evidence.
2. `constraint_evidence_completion`: Completion must be evidence-based, not guess-based.
3. `constraint_typed_tool_contracts`: Tool contracts must be explicit and typed.
4. `constraint_external_contract_stability`: `IterationRequest` and `IterationRunResult` stay stable even if the internal runtime is replaced.

## Decision Principles

1. Prefer model-driven tool choice over rule-driven planner shims.
2. Prefer query-first over blind repair.
3. Prefer deterministic tool contracts and compact current evidence over large narrative prompt templates.
4. Prefer recoverable failures with structured error context.
5. Prefer incremental state evolution over restarts.
6. Prefer visual evidence (`render_view`) when provider supports multimodal planning.
7. Prefer explicit compaction and budget control over unconstrained evidence accumulation.
8. Prefer relation/feature diagnostics as optional explanations, not as the main decision surface.
9. Prefer validator core facts that describe final geometry/completion over provenance-only checks that mostly explain how the geometry was built.
10. Prefer a persistent semantic graph over ad-hoc per-turn planner notes when the model needs to track what remains to be built or repaired.
11. Prefer deterministic repair surfaces over repeating known-invalid API retries when the failure is already classifiable before sandbox execution.

## Relation Layer Clarification

The relation layer is not a second validator.

It exists to describe the current CAD model in terms of objective relative structure, for example:

1. which loops are concentric
2. which path segments are tangent
3. which profile is attached to which path endpoint
4. which edges are parallel to a named global axis

This should be separated conceptually into:

1. `relation_base`: objective relations that can be extracted from the current model state
2. `relation_focus`: which relations matter for the current requirement and stage
3. `relation_eval`: comparison between expected relations and observed relations

Current implementation work in this repository should follow this ownership split:

1. query tools expose `relation_base`
2. runtime round assembly derives `relation_focus`
3. runtime round assembly derives `relation_eval`
4. `validate_requirement` remains a semantic completion check and must not become the primary home of relation feedback

`relation_focus` and `relation_eval` are planner-facing runtime artifacts.
They should stay inspectable in run artifacts and should not replace the underlying raw query evidence.

## Acceptance Principles

A run is considered complete only if:

1. A valid STEP artifact exists.
2. Requirement validation reports pass for key checks.
3. No blocking geometry/runtime errors remain.

## Migration Direction (2026-04-05)

The preferred architecture is now a Claude-Code-style V2 runtime:

1. one model-facing query loop
2. direct tool orchestration
3. typed hooks around tool execution
4. compacted context grouped by turn/tool round
5. post-hoc validation used as judge, not as the default driver

Preferred write tools:

1. `execute_build123d`
2. `apply_cad_action` only for bounded local finishing after a stable code-backed host already exists

Preferred read tools:

1. `query_snapshot`
2. `query_sketch`
3. `query_geometry`
4. `query_topology`
5. `render_view`

Preferred judge/meta tools:

1. `get_history`
2. `validate_requirement`
3. `finish_run` (runtime-local virtual tool)

Legacy planner-facing artifacts such as `active_surface`, `surface_policy`,
`expected_outcome`, `outcome_delta`, `relation_focus`, `relation_eval`, and
`feature_agenda` may remain available for diagnostics, but they are no longer
the preferred control surface for the main decision loop.

Validation lanes now follow this split:

1. `core_checks`
   - objective completion facts that are safe to feed back into the loop
   - examples: solids exist, positive volume, geometry errors absent, required feature geometry is present
2. `diagnostic_checks`
   - provenance/history-sensitive explanations that remain inspectable but should not dominate stop/continue
   - examples: which construction window or plane-specific build sequence produced the geometry

## Domain Kernel Direction (2026-04-07)

V2 now treats an internal `DomainKernelState` as the preferred semantic state authority.

The domain kernel is not a second geometry kernel. Its job is to keep:

1. requirement-level intent
2. current feature/body decomposition
3. active, blocked, and completed semantic targets
4. evidence-linked semantic state

in one stable runtime object that both the model and diagnostics can inspect.

Build123d code remains the main executable format.
The domain kernel is the semantic source of truth used to decide:

1. what still needs to be built
2. which feature family is currently blocked
3. whether the next step should be a whole-part rewrite, subtree rewrite, local edit, or finish

When a code path is already invalid before execution, runtime should narrow the repair surface with deterministic lint hits and family-scoped repair recipes instead of consuming a full sandbox round just to rediscover the same API failure.

Ownership split:

1. `DomainKernelState`
   - semantic planning state
   - active targets
   - blocked/completed tracking
   - fresh binding between geometry / validator / probe evidence and semantic feature families
2. Build123d / structured tools
   - geometry mutation
3. `validate_requirement`
   - post-hoc completion judgment
