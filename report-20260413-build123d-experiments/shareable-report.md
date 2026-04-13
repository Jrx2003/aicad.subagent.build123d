## Executive Summary

The Build123d migration now has enough real evidence to report clearly.

The honest scope for today is not "the repo is fully finished". The useful result is narrower and more defensible:

1. the runtime now has a stronger Build123d contract surface
2. we have a small demo suite that directly matches the runtime problems we have been solving
3. benchmark and probe evidence already shows where Build123d is helping and where the remaining gap is

The most presentation-friendly conclusion is:

Build123d is giving this repo better local-frame control, cleaner builder-level boolean composition, and more deterministic preflight repair surfaces than the old CadQuery contract.

## Current Architecture

The repo remains an isolated iterative CAD runtime:

1. upstream hands in a normalized requirement
2. `IterativeSubAgentRunner` and the V2 loop assemble context
3. the model selects `execute_build123d` or read tools
4. the sandbox executes Build123d code
5. validator and kernel-refresh tools produce post-write evidence
6. everything remains inspectable under `prompts/`, `plans/`, `actions/`, `queries/`, `trace/`, `outputs/`, and `summary.json`

The practical migration win is that Build123d features now line up better with the runtime's needs:

1. `Plane` and `Locations` fit local-coordinate features
2. `Mode.SUBTRACT` and `Mode.INTERSECT` fit same-builder repair recipes
3. `Mode.PRIVATE` fits staged cavity/lip work without mutating the host too early
4. invalid API surfaces can be turned into deterministic preflight lint instead of opaque kernel errors

## What Changed

Today's package is built around three things:

1. rename and reposition the repo as `aicad.subagent.build123d`
2. add `demos/build123d_foundations/` as a report-friendly explanation surface
3. add `report-20260413-build123d-experiments/` so the work can be presented without narrating raw logs

The demo suite intentionally stays small:

1. local-frame countersink plate
2. half-shell with directional holes
3. shelled body plus lip-fit lid

These are the smallest examples that still match our real runtime problems.

## Negative Case

The most useful negative case is:

1. run: `test_runs/20260413_094502`
2. prompt: compact enclosure with a shelled body, a lip-fit cover, and countersunk fastener holes on the top face

Why this case matters:

1. it is an unseen external-style prompt, not a benchmark anchor
2. it mixes shelling, lid/body semantics, and countersunk fasteners
3. it is the kind of case that exposes whether the migration is genuinely generalizing

What happened is useful:

1. the first write was stopped by preflight lint
2. the failure was structured as `execute_build123d_api_lint_failure`
3. the specific rule was `legacy_api.countersink_workplane_method`
4. the runtime returned a repair recipe: `explicit_anchor_hole_countersink_array_safe_recipe`

This is a better failure than the old black-box path. It gives the runtime something teachable, testable, and reusable.

## Success Case

### Success case A: `benchmark/runs/20260413_102600/L2_172`

This is the cleanest report case.

Key outcomes:

1. `planner_rounds=1`
2. `first_write_tool=execute_build123d`
3. `validation_complete=true`
4. evaluator `passed=true`

Why it passed:

1. the model translated corner-based sketch coordinates into the centered host frame
2. the final geometry satisfied the countersink plate requirement in one Build123d write
3. this is exactly the kind of local-frame clarity we want from Build123d

### Success case B: `benchmark/runs/20260413_141000/L1_218`

This is a better loop story than a pure one-shot success.

Key outcomes:

1. `planner_rounds=5`
2. `converged=true`
3. `validation_complete=true`

Why it matters:

1. the groove path had to be repaired inside the loop
2. the runtime pulled the case back toward a cleaner Build123d contract
3. it shows the migration is not only about raw pass rate, but also about making repairs more structured

## Artifact Chain

The reportable chain remains:

1. `prompts/` - exact planner request per round
2. `plans/` - model reasoning summary and tool-call payload
3. `actions/` - Build123d execution result or lint failure
4. `queries/` - validator, kernel-state, and probe evidence
5. `trace/` - round timeline and stop reason
6. `outputs/` - generated STEP artifacts
7. `evaluation/` - benchmark-side comparison outputs when scoring is enabled

This matters because the migration is not only changing geometry code. It is changing how easy the runtime is to debug and explain.

## Structured State / Runtime Replay

Three replay moments are especially worth mentioning in a report:

1. `L2_172` decision summary explicitly says it will map corner-frame coordinates into the centered host frame before writing the Build123d code.
2. the external enclosure probe surfaces a structured repair recipe instead of dying in a generic sandbox failure.
3. `L2_130` shows why the drilling frame contract matters:
   - on `Plane.XZ`, local coordinates are `(x, z)`
   - `offset(...)` moves along the plane normal, not along the feature height variable

These are all examples of Build123d helping us turn ambiguous modeling behavior into explicit runtime contracts.

## Current Judgment

The migration has crossed the threshold where it is useful to present publicly inside the team.

The claim should stay disciplined:

1. Build123d already improves several core runtime surfaces
2. the repo now has demo material and real evidence to explain those gains
3. the work is still in an experimentation phase, not a finished product phase

The remaining open family is still enclosure-style shell-plus-lid reasoning.

## Next Plan

1. keep the demo suite small and stable as the default presentation surface
2. keep using benchmark and external probes as a generalization signal instead of optimizing for a few anchor cases
3. continue focusing on shell-opening semantics, body/lid decomposition, and top-face fastener placement
4. only keep changes that improve shared runtime contracts across multiple cases

## Key Evidence

1. `benchmark/runs/20260413_102600/L2_172`
2. `benchmark/runs/20260413_141000/L1_218`
3. `benchmark/runs/20260413_142700/L2_130`
4. `test_runs/20260413_094502`
5. `demos/build123d_foundations/demo_local_frame_countersink.py`
6. `demos/build123d_foundations/demo_half_shell_directional_holes.py`
7. `demos/build123d_foundations/demo_enclosure_body_lid.py`
