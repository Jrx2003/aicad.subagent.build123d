# AiCAD SubAgent Iteration (Decoupled)

This repo is the decoupled runtime for iterative CAD action planning after upstream requirement generation.

It provides:

- Independent LLM planning loop (`sub_agent.codegen` + runtime orchestrator)
- MCP sandbox tool client (`query_snapshot/query_geometry/render_view/validate_requirement`)
- High-visibility run artifacts for each round
- Stable integration contract for upstream modules

Quick start: see [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Agent operating manual: [`AGENT.md`](AGENT.md).

Run artifact roots:

1. Interactive/probe runs: `test_runs/<timestamp>/`
2. Benchmark runs: `benchmark/runs/<timestamp>/<case_id>/`
