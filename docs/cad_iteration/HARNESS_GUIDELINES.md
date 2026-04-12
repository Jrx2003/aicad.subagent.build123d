# Harness Guidelines for CAD Iteration

This file translates harness-engineering principles into repository-level rules.

Reference:
- OpenAI article: `https://openai.com/index/harness-engineering/`

## Core Premise

`Anything the agent can't access effectively doesn't exist.`

For this repo, that means objectives, runtime controls, and failure evidence must be discoverable through stable files and tool contracts.

## Objective Visibility Rules

1. Keep objective function explicit in machine-readable form (`SYSTEM_RECORD.json`).
2. Keep convergence criteria explicit (`ITERATION_PROTOCOL.md` stop conditions).
3. Keep tool affordances explicit (`TOOL_SURFACE.md` schemas/defaults).
4. Keep expected outputs explicit (artifact layout and summary fields).

## Access Design Rules

1. Prefer retrieval over dumping:
   - `query_geometry` compact window first
   - paginate with offsets
   - inspect local render only when needed
2. Preserve stable IDs:
   - `solid_id`, `face_id`, `edge_id`
3. Persist each planner/tool boundary:
   - prompt in
   - plan out
   - action result
   - query result

## Prompt Budget Rules

1. Prompt size is a reliability budget, not a storage target.
2. Treat oversized context as retrieval failure.
3. Prefer:
   - `entity_ids` filters
   - `next_*_offset` pagination
   - focused `render_view`
   over full-object dumps.

## Failure Visibility Rules

1. Keep failures explicit and machine-readable:
   - `error_code`
   - `error_message`
   - per-round artifacts
2. Do not suppress partial evidence from failed rounds.
3. If a high-fidelity tool fails, degrade with explicit metadata rather than silent omission.

## Benchmark Artifact Rules

Benchmark outputs must be directly useful for post-run diagnosis, not only pass/fail counting.

Per benchmark run:

1. Keep a stable machine-readable identity:
   - `run_id`
   - runtime mode
   - provider / model
   - action mode
   - selected level/cases
   - optional practice tag / human-readable label
2. Keep a human-readable report that explains:
   - what practice/configuration was run
   - which cases passed / failed / timed out
   - why each failed in one short diagnosis line
   - which key files to open next
3. Keep a machine-readable aggregate report that normalizes:
   - end-to-end status
   - evaluation pass/fail
   - validation completion state
   - validator/evaluator disagreement state
   - actual generated STEP path
   - actual token usage
   - planner rounds
   - write-tool count
   - inspection-only round count
   - validation-call count
   - repeated-validation count
   - validation state
   - evaluation result
4. Preserve backward-compatible artifacts, but if runtime behavior changes then benchmark aggregation must adapt to the new artifact shape rather than silently reading stale paths or stale field names.

Per benchmark case:

1. Write one compact analysis artifact summarizing:
   - run status
   - end-to-end status class
   - evaluator result
   - validation result
   - validator/evaluator disagreement when present
   - failure category
   - likely root cause
   - recommended fix layer
   - first bad turn
   - last good write
   - repeated read-only / validation patterns
   - actual generated STEP/render paths
   - prompt / plan / action / query files worth opening next
2. Write one round-digest artifact summarizing each round in execution order:
   - prompt size
   - model decision summary
   - tool calls
   - tool results / tool batch errors
   - validation triggers / validation results
   - round completion flags
3. `trace/events.jsonl` must reflect the real runtime timeline:
   - round start
   - model response
   - tool batch start
   - tool result
   - validation trigger/result
   - round completion
   - run finish
4. `brief_report.md` must never degrade to a table of `FAIL | ok`; if aggregation cannot explain the issue, it should explicitly say which artifact is missing or which parsing assumption failed.
5. Aggregate reporting must not collapse different failure semantics into one bucket:
   - geometry mismatch
   - validator/evaluator disagreement
   - runtime/tool error
   - timeout

## Edit Protocol for Agents

1. Update docs first for any iterative-loop behavior change.
2. Implement code changes.
3. Verify with:
   - focused unit tests
   - at least one real probe under `test_runs/<timestamp>`
4. Report run directory and key evidence paths.
