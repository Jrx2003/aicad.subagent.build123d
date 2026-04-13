# Build123d Foundation Demos

This directory contains three small Build123d-first demos that are aligned with the runtime problems we have actually been debugging in this repo.

## Why these demos

These are not generic CAD toy examples. Each demo mirrors one contract surface that matters to the iterative runtime:

1. `demo_local_frame_countersink.py`
   - maps corner-based sketch coordinates into a centered host frame
   - uses `Locations` for repeated hole placement
   - mirrors the successful `benchmark/runs/20260413_102600/L2_172` path
2. `demo_half_shell_directional_holes.py`
   - uses same-builder `Mode.SUBTRACT` and `Mode.INTERSECT`
   - keeps directional drilling on `Plane.XZ.offset(0)` so local coordinates remain `(x, z)`
   - mirrors the half-shell repair surface exposed by `benchmark/runs/20260413_142700/L2_130`
3. `demo_enclosure_body_lid.py`
   - stages cavity and lip geometry with `Mode.PRIVATE`
   - keeps body and lid semantics separate
   - mirrors the external enclosure experiment at `test_runs/20260413_094502`

## Run the full demo suite

```bash
cd ~/code/aicad.subagent.build123d
uv run python demos/build123d_foundations/run_all.py
```

Generated artifacts land in `demos/build123d_foundations/artifacts/`.

## Presentation talking points

1. Build123d makes local frames first-class with `Plane` and `Locations`, which fits our hole-array and face-local feature cases.
2. Builder-native boolean modes are easier to lint and repair than ad-hoc chained workplane state.
3. `Mode.PRIVATE` is useful for staging solids inside a live builder without accidentally mutating the host too early.
4. These patterns are easier to turn into deterministic runtime guidance, preflight lint, and validator expectations than the older CadQuery-style contract.
