# CAD Iterative Generation Knowledge Index

This directory is the agent-readable knowledge base for iterative CAD generation in AiCAD.

## Why This Exists

The project keeps one stable source of design intent and execution policy that both humans and agents can consume.
If the agent cannot discover a policy from this directory, that policy should be treated as non-existent.

## Canonical Source Mapping

- Primary historical source: [../CAD_ACTION_ITERATION.md](../CAD_ACTION_ITERATION.md)
- This directory reframes that source into stable records, contracts, and upgrade checkpoints.

## Read Order (for Agents)

1. [SYSTEM_RECORD.json](SYSTEM_RECORD.json) - machine-readable objective, priorities, and constraints.
2. [DESIGN_INTENT.md](DESIGN_INTENT.md) - goals, non-goals, and acceptance principles.
3. [FEATURE_GRAPH_RUNTIME.md](FEATURE_GRAPH_RUNTIME.md) - semantic feature-graph state model and sync rules.
4. [ITERATION_PROTOCOL.md](ITERATION_PROTOCOL.md) - runtime loop and convergence rules.
5. [TOOL_SURFACE.md](TOOL_SURFACE.md) - current and required MCP tool surface.
6. [CANONICAL_BASELINE.md](CANONICAL_BASELINE.md) - frozen live lane, canary suite, and baseline metric definitions.
7. [HARNESS_GUIDELINES.md](HARNESS_GUIDELINES.md) - harness-oriented doc and tool design rules.
8. [UPGRADE_ROADMAP.md](UPGRADE_ROADMAP.md) - staged upgrade plan and completion criteria.

## Agent Access Protocol

1. Read `SYSTEM_RECORD.json` first and cache only stable IDs.
2. Resolve policy from this directory before using any descriptive docs.
3. Treat missing records as non-existent behavior.
4. Use `FEATURE_GRAPH_RUNTIME.md` as the source of truth for semantic graph state ownership and sync semantics.
5. Use `TOOL_SURFACE.md` as the source of query tool capabilities and defaults.
6. Use `CANONICAL_BASELINE.md` for benchmark-facing live-lane and canary expectations.
7. Use `HARNESS_GUIDELINES.md` for access design, objective visibility, and failure visibility.
8. If runtime behavior changes, update this directory before code comments/prose.
9. Use the retrieval pattern `search -> window -> inspect -> act` to avoid prompt overflow.

## Stability Rules

- Keep stable IDs in `SYSTEM_RECORD.json` unchanged once published.
- Any behavior change in iterative CAD flow must update this directory first.
- `CAD_ACTION_ITERATION.md` can remain descriptive; this directory is normative for execution policy.
