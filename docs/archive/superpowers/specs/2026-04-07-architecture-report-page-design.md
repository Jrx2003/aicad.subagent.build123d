# 2026-04-07 Architecture Report Page Design

## Goal

Build a standalone static presentation page for the current architecture/reporting status of `aicad.subagent.iteration`.

The page must work without a presenter, so each screen needs to explain one idea clearly and end with a visible conclusion. It should be suitable for both projected presentation and self-guided reading.

## Output

Create a new standalone directory:

- `report-20260407-architecture/index.html`
- `report-20260407-architecture/styles.css`
- `report-20260407-architecture/main.js`

The page should open directly in a browser as a static file.

## Design Direction

Use a hybrid of the existing `course/` and `diff-review-20260403-163535/` presentation styles:

- keep the section-based, full-screen storytelling rhythm
- keep the warm editorial background and strong typography
- use an engineering-report visual language instead of a teaching-course tone
- avoid copying content structure from the reference pages

Visual tone:

- warm light background with subtle industrial grid or radial texture
- graphite text
- blue-green primary accent
- copper or amber secondary accent
- large display headings with restrained body copy
- strong card hierarchy and visible section conclusions

## Page Structure

The page should use chapter-based full-screen modules with top navigation dots.

Planned modules:

1. `Hero / Executive Summary`
   - one-sentence overall judgment
   - four headline metric cards
   - one short "what matters now" statement

2. `Architecture Overview`
   - five-layer architecture view
   - each layer shows role, current status, and interpretation

3. `Runtime Flow`
   - flow diagram for the V2 loop
   - emphasis on runtime-centered execution instead of planner-centered execution

4. `What Changed Today`
   - timeline or progress cards
   - show FeatureGraph integration, graph-driven policy, benchmark integration, and problem diagnosis work

5. `Case Study: L1_159`
   - visually explain why token cost became high
   - separate task difficulty from path cost
   - show multi-round overhead and stale evidence effect

6. `Success Case: L1_218`
   - show a cleaner code-first convergence path
   - contrast with `L1_159` on rounds, tokens, and read-only overhead
   - explain why this case reflects the intended runtime direction

7. `Artifact Explorer`
   - walk through `prompts/`, `plans/`, `actions/`, `queries/`, `trace/`, `outputs/`, and `evaluation/`
   - show one real file excerpt per artifact family
   - explain how each artifact participates in the end-to-end run

8. `FeatureGraph + Conversation Replay`
   - replay the successful run round by round
   - show how `FeatureGraph`, validation, tool policy, and recent transcript shape the next decision
   - make the reader understand the run as if they were the runtime-driven agent

9. `Current Judgment`
   - two-column comparison: current transition state vs recommended convergence direction
   - clearly state that `execute_cadquery` should be the default main write path

10. `Next Plan`
   - staged roadmap with near-term actions
   - focus on narrowing structured action to fallback status and validating benchmark impact

11. `Key Code and Evidence`
   - include a small number of high-value code excerpts
   - use excerpts only where they strengthen the architecture argument

## Content Rules

- Do not turn the page into a raw Markdown dump.
- Each screen should answer a single question.
- Each screen should have a visible summary or conclusion panel.
- Keep paragraphs short and scannable.
- Prefer diagrams, cards, step indicators, and comparison layouts over long prose.
- Include only a few code excerpts, with explanation beside them.

## Source Material

Primary content sources:

- `docs/work_logs/2026-04-07_架构现状与后续计划汇报.md`
- `docs/work_logs/2026-04-07.md`
- `docs/cad_iteration/SYSTEM_RECORD.json`
- `docs/cad_iteration/DESIGN_INTENT.md`
- `docs/cad_iteration/ITERATION_PROTOCOL.md`
- `docs/cad_iteration/TOOL_SURFACE.md`
- `src/sub_agent_runtime/runner.py`
- `src/sub_agent_runtime/agent_loop_v2.py`
- `src/sub_agent_runtime/feature_graph.py`
- `src/sub_agent_runtime/tool_runtime.py`
- `src/sandbox_mcp_server/service.py`
- `benchmark/runs/20260407_165907/L1_159/`
- `benchmark/runs/20260407_153253/L1_218/`

## Interaction Rules

- top navigation dots
- keyboard navigation with arrow keys
- smooth scroll between sections
- light reveal animations when sections enter view
- at least one step-through flow interaction
- at least one visual comparison component
- no heavy framework dependency

## Technical Constraints

- plain HTML, CSS, and JavaScript only
- keep assets local except optional web fonts already used in references
- mobile and desktop must both remain readable
- code blocks should use monospace styling and compact framing
- avoid excessive animations that would reduce readability during reporting

## Acceptance Criteria

The implementation is complete when:

1. the page opens as a static local file
2. the structure follows the planned 1+2 hybrid storytelling layout
3. the user can understand the current architecture, current problem, and next plan without narration
4. at least three architecture/code evidence areas are visually represented
5. the L1_159 token-cost diagnosis is explained clearly in one dedicated module
6. the success path is explained with a positive end-to-end case that includes artifacts, `FeatureGraph`, and conversation replay
7. the page feels visually intentional and presentation-ready rather than like a raw internal note
