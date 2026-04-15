# Benchmark Runner

This directory contains benchmark data and benchmark run artifacts for the iterative Build123d runtime.

## Dataset

Default dataset root:

`benchmark/sampled_10_per_L`

Expected structure:

1. `L1/L1_sampled_rows.csv`, `L2/L2_sampled_rows.csv`, `L3/L3_sampled_rows.csv`
2. `L*/steps/<case_id>.step` ground-truth files
3. optional `canonical_case_overrides.json` for audited prompt / GT corrections

## Canonical Reference Overrides

When a sampled CSV row is inconsistent with the executable reference or stored STEP, use:

`benchmark/sampled_10_per_L/canonical_case_overrides.json`

Allowed per-case override fields:

1. `prompt`
2. `prompt_field`
3. `gt_step_path` (optional)
4. `canonical_reference`
5. `notes`

This is a benchmark-data repair layer. Runtime fixes must not be hidden inside this manifest.

## Run Benchmark

Single case:

```bash
./benchmark/run_prompt_benchmark.sh --cases L1_20
```

Named canary set:

```bash
./benchmark/run_prompt_benchmark.sh --case-set canary
```

By default the benchmark runs with:

1. `runtime=v2`
2. dynamic loop mode (`one-action-per-round`)
3. automatic deterministic STEP evaluation
4. `execute_build123d` as the default code-first whole-part write tool

Optional model overrides:

```bash
./benchmark/run_prompt_benchmark.sh \
  --cases L1_20 \
  --reasoning-provider kimi \
  --reasoning-model moonshot-v1-128k \
  --batch-actions \
  --max-rounds 2
```

Multiple cases:

```bash
./benchmark/run_prompt_benchmark.sh --cases L1_20,L2_88,L3_11
```

Level subset:

```bash
./benchmark/run_prompt_benchmark.sh --levels L1,L2 --limit 5
```

## Output Layout

Benchmark artifacts live under:

`benchmark/runs/<YYYYMMDD_HHMMSS>/<case_id>/`

Per case:

1. `prompt.txt`
2. `ground_truth.step`
3. iterative evidence: `prompts/`, `plans/`, `actions/`, `queries/`, `outputs/`
4. `summary.json`
5. `benchmark_case.json`
6. `benchmark_runner.stdout.log`
7. `benchmark_runner.stderr.log`
8. `evaluation/benchmark_eval.json`
9. `evaluation/benchmark_eval_summary.txt`
10. preview images when evaluation rendering succeeds

Run root:

1. `summary.json`
2. `brief_report.md`
3. `run_diagnostics.md`

## Canary And Baseline

Named case sets live in:

`benchmark/canary_case_sets.json`

The default fixed regression slice is:

1. `L1_122`
2. `L1_148`
3. `L1_157`
4. `L1_159`
5. `L2_88`
6. `L2_130`
7. `L2_149`
8. `L2_172`

Run-level reports now expose baseline metrics derived from existing case artifacts:

1. `first_solid_success_rate`
2. `requirement_complete_rate`
3. `runtime_rewrite_rate`
4. `mean_repair_turns_after_first_write`
5. `stale_evidence_incidents`
6. `tokens_per_successful_case`
7. `family_repair_packet_hit_rate`

## Notes

1. Run directory names must stay timestamp-only.
2. Effective runtime is always reported as `v2`.
3. Practice identity and benchmark reports must reflect the effective runtime actually used.
