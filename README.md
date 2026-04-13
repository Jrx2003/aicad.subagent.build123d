# AiCAD SubAgent Build123d Runtime

This repo is the decoupled Build123d-first runtime for iterative CAD execution after upstream requirement generation.

It provides:

- Independent LLM planning loop (`sub_agent.codegen` + runtime orchestrator)
- MCP sandbox tool client (`execute_build123d`, `query_snapshot`, `query_geometry`, `render_view`, `validate_requirement`)
- High-visibility run artifacts for each round
- Stable integration contract for upstream modules
- A small Build123d demo suite for report and live walkthrough use

Quick start: see [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Agent operating manual: [`AGENT.md`](AGENT.md), [`AGENTS.md`](AGENTS.md).

Demo entrypoints:

1. [`demos/build123d_foundations/README.md`](demos/build123d_foundations/README.md)
2. [`demos/build123d_foundations/run_all.py`](demos/build123d_foundations/run_all.py)
3. [`report-20260413-build123d-experiments/shareable-report.md`](report-20260413-build123d-experiments/shareable-report.md)

Run artifact roots:

1. Interactive/probe runs: `test_runs/<timestamp>/`
2. Benchmark runs: `benchmark/runs/<timestamp>/<case_id>/`
