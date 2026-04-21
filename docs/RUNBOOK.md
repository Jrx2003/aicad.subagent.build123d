# Runbook

## 1. Setup

```bash
cd ~/code/aicad.subagent.build123d
cp .env.example .env
# fill at least one provider key, e.g. GLM_API_KEY or KIMI_API_KEY
uv sync
```

Quick credential sanity check (no secret printing):

```bash
cd ~/code/aicad.subagent.build123d
uv run python - <<'PY'
from common.config import settings
print("provider=", settings.llm_reasoning_provider)
print("model=", settings.llm_reasoning_model)
print("has_kimi_key=", bool(settings.kimi_api_key))
print("has_glm_key=", bool(settings.glm_api_key))
PY
```

Build123d demo suite:

```bash
cd ~/code/aicad.subagent.build123d
uv run python demos/build123d_foundations/run_all.py
cat demos/build123d_foundations/artifacts/summary.json
```

## 2. Live iterative probe (LLM + MCP tools)

```bash
cd ~/code/aicad.subagent.build123d
LLM_REASONING_PROVIDER=kimi \
LLM_REASONING_MODEL=moonshot-v1-128k \
LLM_TIMEOUT_SECONDS=180 \
AICAD_PROBE_ONE_ACTION_PER_ROUND=1 \
scripts/run_aci_live_probe.sh
```

Notes:

1. Default run directory name is strict timestamp only: `test_runs/YYYYMMDD_HHMMSS`.
2. `latest` symlink points to the most recent run.
3. If provider network latency is high (for example occasional `APITimeoutError`), keep the same command and switch provider/model (`glm` <-> `kimi`) to compare stability.
4. Default execution mode is dynamic (`one action -> inspect -> re-plan`).
5. Solid bootstrap safeguard:
   - in one-action mode, if no solid exists and planner already proposed a near-future
     solid-building action (`extrude`/`revolve`/`loft`), runtime may execute multiple
     actions in that round up to the first solid-building step.
   - inspect this in `trace/events.jsonl` via `event=action_execution_policy`.
6. For Kimi, image attachment is enabled only for vision-capable model names (containing `vision`/`vl`/`multimodal`); text-only models receive render metadata but no inline image blob.
7. CAD action compatibility highlights:
   - `extrude` supports `both_sides=true` for symmetric extrusion.
   - `add_circle` supports `position` / `center`.
   - `create_sketch` supports `position` / `center` (including `x,y,z` string/list mapping by plane).
   - `add_polygon` supports alias lists: `length_list` and `radius_list`.

Post-run verification:

```bash
RUN_DIR=$(readlink test_runs/latest)
cat "$RUN_DIR/summary.json"
```

Check these fields:

1. `step_file_exists` should be `true` for successful geometry output.
2. `render_file_exists` should be `true` when visual evidence is available.
3. `render_image_attached_for_prompt` should be `true` for multimodal rounds with usable image evidence.
4. `token_usage.total_tokens` shows total planner token consumption for this case.

Trace process log:

```bash
RUN_DIR=$(readlink test_runs/latest)
cat "$RUN_DIR/trace/events.jsonl"
```

`trace/events.jsonl` shows per-round planner decisions, action execution, and tool-query events.
`plans/round_XX_response.json` also includes `planner_note` for concise per-round intent.

Render planning intent:

1. `render_view.intent=global_overview` for whole-model checks.
2. `render_view.intent=detail_check` for local/feature-level confirmation.

## 3. Stage1 tool probe (no planner loop changes, direct tool visibility)

```bash
cd ~/code/aicad.subagent.build123d
scripts/run_stage1_manual_probe.sh
```

## 4. Multi-angle render probe (real custom render validation)

This probe builds a completed 3D model first, then calls `render_view` with multiple
continuous camera parameters (`azimuth/elevation/zoom`) plus optional focused render.

```bash
cd ~/code/aicad.subagent.build123d
scripts/run_render_view_multiview_probe.sh
```

Key outputs:

1. `results/render_views/*.json` - per-angle render contract and `render_source`.
2. `outputs/*_render_view.png` - image outputs for each custom angle.
3. `summary.json` - success counts and `render_source` distribution.

## 5. Prompt benchmark run (separate from test_runs)

Run one benchmark case:

```bash
cd ~/code/aicad.subagent.build123d
./benchmark/run_prompt_benchmark.sh \
  --cases L1_20 \
  --reasoning-provider kimi \
  --reasoning-model kimi-k2.6
```

Run with explicit provider/model:

```bash
cd ~/code/aicad.subagent.build123d
./benchmark/run_prompt_benchmark.sh \
  --cases L1_20 \
  --reasoning-provider kimi \
  --reasoning-model kimi-k2.6 \
  --batch-actions \
  --max-rounds 2
```

Operational default:

1. Prefer `kimi` / `kimi-k2.6` for real probes and benchmark reruns unless you are explicitly diagnosing another provider/model.
2. If your shell/default `.env` points to another provider, pass `--reasoning-provider kimi --reasoning-model kimi-k2.6` explicitly.
3. The benchmark runtime is fixed to `v2`; there is no live `legacy` selection surface anymore.
4. CLI flags such as `--reasoning-provider` and `--reasoning-model` are authoritative.
5. If the CLI omits them, caller-provided environment variables such as `LLM_REASONING_PROVIDER` and `LLM_REASONING_MODEL` remain authoritative and must not be overwritten by repository `.env`.
6. Benchmark execution also requires a live sandbox backend; if Docker is unavailable, planner tokens may still be consumed but action execution will fail before any geometry is produced.
7. If you pass `--run-id`, it must still be timestamp-only: `YYYYMMDD_HHMMSS`.
   Put practice labels in `practice_identity`, reports, or `by_practice/` links instead of the benchmark root directory name.

Run benchmark without automatic scoring:

```bash
./benchmark/run_prompt_benchmark.sh --levels L1 --skip-eval
```

Benchmark outputs:

1. `benchmark/runs/<timestamp>/<case_id>/outputs/final_model.step`
2. `benchmark/runs/<timestamp>/<case_id>/ground_truth.step`
3. `benchmark/runs/<timestamp>/<case_id>/evaluation/benchmark_eval.json`
4. `benchmark/runs/<timestamp>/summary.json`
5. `benchmark/runs/<timestamp>/<case_id>/evaluation/{generated_preview_iso,generated_preview_front,generated_preview_right,generated_preview_top}.png`
6. `benchmark/runs/<timestamp>/<case_id>/evaluation/{ground_truth_preview_iso,ground_truth_preview_front,ground_truth_preview_right,ground_truth_preview_top}.png`
7. `benchmark/runs/<timestamp>/brief_report.md`
8. `benchmark/runs/<timestamp>/run_diagnostics.md`

Image semantics:

1. `generated_preview_*.png` and `ground_truth_preview_*.png` are for manual side-by-side review.
2. Benchmark automatic score uses STEP geometric signatures only (no image similarity, no LLM).
3. `render_view` is runtime evidence for the iterative planner and is separate from benchmark evaluation images.

Historical run archive maintenance:

```bash
cd ~/code/aicad.subagent.build123d
PATH="/opt/homebrew/bin:$PATH" uv run python scripts/archive_historical_runs.py --cutoff 2026-04-12
```

Archive behavior:

1. Active timestamp runs stay at the top level of `benchmark/runs/` and `test_runs/`.
2. Older run directories move under `archive/pre_<YYYYMMDD>/`.
3. `benchmark/runs/by_practice/` keeps only active links; older links move under the matching archive root.
4. The archive script repairs archived `by_practice` links that still reference legacy repository paths.
5. Use `uv run python ...` rather than bare system `python3`; some system interpreters are too old for the repository runtime.

## 6. Main adjustable parameters

Environment variables used by `run_aci_live_probe.sh`:

1. `LLM_REASONING_PROVIDER` (`glm`/`kimi`/`openai`/...)
2. `LLM_REASONING_MODEL` (e.g. `glm-4.7`, `kimi-k2.6`)
3. `AICAD_PROBE_REQUIREMENT` (plain text requirement)
4. `AICAD_PROBE_REQUIREMENTS_FILE` (JSON object file path)
5. `AICAD_PROBE_MAX_ROUNDS` (default `4`)
6. `AICAD_PROBE_ONE_ACTION_PER_ROUND` (`0`/`1`)
7. `AICAD_PROBE_FORCE_POST_CONVERGENCE_ROUND` (`0`/`1`)
8. `AICAD_PROBE_SANDBOX_TIMEOUT` (seconds)
9. `AICAD_TEST_RUNS_ROOT` (default `test_runs`)

## 7. Artifact layout (each run)

```text
test_runs/20260310_173217/
  run_manifest.json
  summary.json
  prompts/
  plans/
  actions/
  queries/
  outputs/
```

High-visibility files:

1. `prompts/round_XX_request.json` - exact planner input evidence.
2. `prompts/round_XX_request_full.json` - full untrimmed evidence snapshot.
3. `prompts/round_XX_user_prompt.txt` - actual text prompt sent to planner.
4. `plans/round_XX_response.json` - raw model output + normalized actions/inspection.
5. `actions/round_XX_action_YY_request.json` - exact action replay request payload.
6. `actions/round_XX_action_YY_result.json` - per-action execution result.
7. `queries/*.json` - `query_snapshot/query_geometry/render_view/validate_requirement` payloads.
8. `outputs/` - `final_model.step`, render images, intermediate `.step` files.
9. `summary.json` - convergence/validation/failure/token summary.

On-demand tool policy:

1. Runtime no longer forces `query_snapshot/query_geometry/render_view/validate_requirement` after every action.
2. Planner (`inspection`) decides whether to call each tool in that round.
3. If no inspection tools are requested, `queries/round_XX_action_YY.json` contains `queried_sections: []`.
4. In one-action mode, action batching can still occur under `bootstrap_to_first_solid`
   / `bootstrap_to_first_solid_compressed` / `complete_sketch_window`
   / `endgame_multi_action_no_solid`
   policy when needed to avoid sketch-only dead-ends.
5. Runtime replays the full selected action prefix on a cleared session each execution step,
   preventing duplicated-history drift across rounds.
6. Runtime may rewrite unsafe operations before execution (see `trace/events.jsonl` event `action_rewritten`).

Replay any recorded action request:

```bash
cd ~/code/aicad.subagent.build123d
uv run python scripts/replay_action_sequence.py \
  --request-file test_runs/<timestamp>/actions/round_01_action_01_request.json
```

## 8. Kimi Multimodal Troubleshooting

If Kimi runs but no render image is attached:

1. Inspect `queries/round_XX_*.json -> render_view`.
2. Confirm `view_file` exists and `output_files` contains a `.png`.
3. Inspect `render_view.camera.render_fallback_used`:
   - `false`: custom render path worked.
   - `true`: fallback preview image was used because custom render was unavailable.
4. If both custom render and fallback are unavailable, inspect `error_message`.
5. If Kimi returns `Image input not supported`, use a vision-capable Kimi model or rely on text-only mode (no inline image attachment).
