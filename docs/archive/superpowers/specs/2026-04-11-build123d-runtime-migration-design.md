# Build123d Runtime Migration Design

## Goal

Replace the repository's CadQuery-centered code-first runtime with a strict Build123d-centered runtime.
This is a contract migration, not a compatibility layer:

1. all public and internal `cadquery`/`execute_cadquery` surfaces are renamed to `build123d`/`execute_build123d`
2. runtime guidance is redesigned around Build123d's builder-oriented modeling style
3. benchmark identity, diagnostics, and evidence naming are updated to report the new tool surface truthfully
4. verification ends with a full `L1` benchmark run using the configured `.env` LLM credentials

## Why This Change

The current repository treats CadQuery as more than an implementation detail:

1. MCP tools expose `execute_cadquery` and `execute_cadquery_probe`
2. sandbox prelude logic assumes `import cadquery as cq`
3. runtime guidance teaches a chain-heavy CadQuery mental model
4. failure taxonomy, benchmark summaries, and report wording all encode CadQuery-specific language

That makes a library swap incomplete unless the entire runtime contract changes with it.
The target state is a repository where the planner, sandbox, validator bridge, benchmark, and reports all speak a single Build123d-native language.

## User-Approved Constraints

1. cut the old contract completely; no external compatibility aliases
2. use Python `build123d` as the geometry runtime
3. execute the stricter migration approach, not a shallow string replacement
4. validate the result with a full `L1` benchmark run

## Design Principles

### 1. One tool language everywhere

The repo must not internally execute Build123d while externally pretending the tool is still CadQuery.
Names, errors, audit summaries, and runtime notes must all refer to Build123d.

### 2. Builder-first modeling guidance

The runtime should guide the model toward Build123d's strengths:

1. `BuildPart` for whole-part or subtree construction
2. `BuildSketch` for profile authoring
3. `BuildLine` for path/rail construction
4. `Plane`, `Axis`, `Pos`, `Rot`, and `Locations` for explicit spatial reasoning
5. operation-oriented calls such as `extrude`, `revolve`, `loft`, `sweep`, `fillet`, and `chamfer`

This replaces the current workplane-chain-heavy guidance.

### 3. Explicit geometry ownership

Where CadQuery often encourages long chained expressions, Build123d is clearer when intermediate objects are explicit.
The runtime guidance and lint layer should bias toward:

1. explicit builder scopes
2. explicit final `Part` assignment
3. explicit sketch/path separation
4. explicit placement and axis definitions

### 4. No "magic translation" layer

The migration will not silently reinterpret CadQuery code as Build123d code.
The system should force the planner and repair loops to learn the new surface rather than preserving old habits through shims.

## Scope

### In Scope

1. sandbox code execution entrypoints
2. MCP tool names, contracts, registry entries, server wiring, and runner wrappers
3. runtime tool metadata, prompt guidance, failure taxonomy, and planner-facing notes
4. benchmark summaries, reports, run metadata, and tests
5. focused documentation updates required to describe the new behavior truthfully
6. full unit/regression verification and full `L1` benchmark execution

### Out of Scope

1. rewriting historical `benchmark/runs/*` artifacts
2. introducing backward-compatible `execute_cadquery*` aliases
3. building an automated CadQuery-to-Build123d transpiler
4. unrelated runtime architecture refactors outside the migration path

## Build123d Features To Leverage

This migration should use Build123d as a modeling strategy improvement, not just as a replacement dependency.

### Builder contexts

`BuildPart`, `BuildSketch`, and `BuildLine` provide natural decomposition boundaries.
They are a better fit for iterative repair than CadQuery-style global chained workplanes because they separate:

1. volumetric construction
2. profile generation
3. path generation

This helps the planner produce smaller, more stable repair deltas.

### Explicit placement tools

`Pos`, `Rot`, `Locations`, `Plane`, and `Axis` make spatial intent legible.
The runtime should push the planner toward explicit placements instead of inferring geometry from chained workplane state.
That directly helps cases involving:

1. mirrored or repeated features
2. multi-plane layouts
3. non-default sketch planes
4. path sweeps and revolves

### Typed shape flow

Build123d's distinction between `Part`, `Sketch`, `Wire`, `Face`, and builder results is useful for linting.
The new preflight checks should catch common category errors before sandbox execution, especially when the planner confuses:

1. profile objects versus solids
2. builder-local objects versus exported final parts
3. path definitions versus sweep-ready sections

### Operation-first APIs

Build123d's direct operations map well to runtime guidance.
The planner should be nudged toward explicit `extrude`, `revolve`, `loft`, and `sweep` recipes rather than indirect chain composition when repairing topology-sensitive cases.

## Target Architecture

### 1. Sandbox execution surface

Affected areas:

1. `src/sandbox/docker_runner.py`
2. `src/sandbox/mcp_runner.py`
3. `src/common/config.py`

Changes:

1. replace CadQuery-specific runtime prelude/epilogue with Build123d imports and result extraction
2. rename Docker image defaults from `cadquery-runtime:latest` to a Build123d-specific image name
3. rename runner defaults and result wrapper classes to Build123d terms
4. preserve current artifact contract where possible: STEP export, rendered previews, and geometry info remain available to downstream consumers

Execution behavior:

1. the runtime prelude imports Build123d symbols
2. the executed script must produce an exportable final `Part`
3. the epilogue finds the final object from explicit `result` first, then approved fallback names if needed
4. only export when the resolved final object is a valid solid-bearing `Part`

### 2. MCP tool contract

Affected areas:

1. `src/sandbox_mcp_server/contracts.py`
2. `src/sandbox_mcp_server/server.py`
3. `src/sandbox_mcp_server/registry.py`
4. `src/sandbox_mcp_server/evidence_builder.py`
5. `src/sandbox/mcp_runner.py`

Changes:

1. rename `execute_cadquery` to `execute_build123d`
2. rename `execute_cadquery_probe` to `execute_build123d_probe`
3. rename all corresponding request/response/result dataclasses and models
4. update descriptions, examples, and diagnostics language to Build123d
5. keep the existing contract shape where practical so the migration is semantic rather than structural

Important rule:

The new contract must not mention CadQuery in field descriptions, tool descriptions, or structured error strings unless a test explicitly checks for a migration warning.

### 3. Planner/runtime guidance

Affected areas:

1. `src/sub_agent_runtime/tool_runtime.py`
2. `src/sub_agent_runtime/skill_pack.py`
3. `src/sub_agent_runtime/context_manager.py`
4. `src/sub_agent_runtime/feature_graph.py`
5. related runtime prompts and tests

Changes:

1. replace "default first write to execute_cadquery" with Build123d guidance
2. rewrite code-first advice around builder stages, explicit planes/axes, and explicit placements
3. update runtime skill IDs, follow-up recommendations, and probe guidance to Build123d naming
4. update evidence freshness and repair-lane policies to the new tool names

Behavioral intent:

1. whole-part rebuilds should prefer `execute_build123d`
2. probes should remain diagnostics-only and must not masquerade as final writes
3. after repeated build-code failures, the runtime should still encourage targeted geometry inspection before another broad rewrite

### 4. Lint and failure taxonomy

Affected areas:

1. `src/sub_agent_runtime/tool_runtime.py`
2. `src/common/blocker_taxonomy.py`
3. failure-normalization logic in runtime and tests

Changes:

1. replace CadQuery API hallucination lint with Build123d misuse lint
2. rename failure kinds from `execute_cadquery_*` to `execute_build123d_*`
3. update recommended-next-tool policies to the new names

Planned lint families:

1. use of old CadQuery-specific APIs inside `execute_build123d`
2. missing final `Part` assignment
3. builder misuse outside the appropriate context
4. obvious type-flow mistakes such as passing a non-profile object to sweep/revolve recipes
5. selector/workplane idioms that indicate the model is still reasoning in CadQuery terms

### 5. Benchmark/reporting surface

Affected areas:

1. `benchmark/run_prompt_benchmark.py`
2. `benchmark/README.md`
3. benchmark-related tests

Changes:

1. rename tool identities in summary fields and reports
2. rename terminal error wording tied to the old `execute_cadquery` path
3. keep report structure stable so existing analysis tools still work, but make all values tell the new truth

Example outcomes:

1. `first_write_tool` values become `execute_build123d`
2. `executed_action_types` record `execute_build123d`
3. diagnostics no longer say "terminal execute_cadquery path"

## Migration Strategy

### Phase 1. Rename and rewire the execution contract

Goal:

Get the sandbox, MCP server, and runtime wrappers speaking Build123d names end-to-end.

Success criteria:

1. no runtime-exposed tool names contain `cadquery`
2. contract types and test expectations compile under new names

### Phase 2. Replace planner guidance with Build123d-native guidance

Goal:

Make the code-first path actively prefer builder-based recipes rather than carry over CadQuery mental models.

Success criteria:

1. runtime notes, skill packs, and tool descriptions are Build123d-native
2. old CadQuery-first recommendations are removed

### Phase 3. Replace preflight lint and failure normalization

Goal:

Catch the most common "still thinking in CadQuery" errors before execution.

Success criteria:

1. old CadQuery lint tests are replaced with Build123d lint tests
2. failure summaries normalize to `execute_build123d_*`

### Phase 4. Update benchmark/reporting and run the full verification stack

Goal:

Ensure evidence, metrics, and run summaries stay coherent after the migration.

Success criteria:

1. benchmark unit tests pass
2. full `L1` benchmark runs with `.env` credentials
3. the final report contains Build123d tool names only

## Risks

### 1. Export semantics differ from CadQuery

Build123d object resolution may not match the old `result.solids()` assumptions exactly.
Mitigation:

1. centralize final object resolution in the sandbox epilogue
2. add focused unit tests for exportable result detection

### 2. Probe behavior may drift

Diagnostics-only execution must remain read-like even after the new runtime surface.
Mitigation:

1. preserve the current persisted-session versus non-persisted-probe split
2. keep probe artifacts on the query side only

### 3. Lint may become over-aggressive

A naive Build123d lint could block valid scripts.
Mitigation:

1. start with deterministic, high-confidence checks only
2. encode them through regression tests before broadening coverage

### 4. Benchmark comparability could become noisy

Because the planner guidance is intentionally changing, some benchmark behavior drift is expected.
Mitigation:

1. keep report schemas stable
2. treat tool identity changes as intended drift
3. report the exact run directory and summary deltas

## Testing Plan

### Focused unit/regression

Run focused tests covering:

1. sandbox runner
2. MCP contract/server/runner
3. runtime tool surface and failure normalization
4. benchmark summary/report formatting

### Full benchmark verification

Run the full `L1` benchmark using the repository `.env`.

Expected deliverables:

1. benchmark run directory
2. summary file
3. brief report
4. note of any remaining Build123d-specific failure clusters

## Implementation Notes

The migration should favor precise renaming and guidance rewrites over large-scale internal restructuring.
Where a file has deeply embedded CadQuery language, it is acceptable to refactor helper names locally if that reduces ambiguity.
The important constraint is that the resulting repository has one coherent Build123d story across execution, planning, diagnostics, and benchmark reporting.
