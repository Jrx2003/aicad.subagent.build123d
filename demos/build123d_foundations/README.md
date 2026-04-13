# Build123d 基础演示集

这个目录包含 3 组以 Build123d 为核心的小型 demo，它们不是随手写的 CAD 小例子，而是直接对应这个仓库最近真正处理过的 runtime 问题。

## 为什么是这 3 个 demo

它们分别对应 3 类对迭代 runtime 很关键的 contract surface：

1. `demo_local_frame_countersink.py`
   - 把 corner-based sketch coordinates 映射到 centered host frame
   - 用 `Locations` 表达重复孔位 placement
   - 对应成功案例 `benchmark/runs/20260413_102600/L2_172`
2. `demo_half_shell_directional_holes.py`
   - 使用 same-builder `Mode.SUBTRACT` 和 `Mode.INTERSECT`
   - 把定向打孔固定在 `Plane.XZ.offset(0)` 上，使局部坐标保持 `(x, z)`
   - 对应 `benchmark/runs/20260413_142700/L2_130` 暴露的 half-shell repair surface
3. `demo_enclosure_body_lid.py`
   - 用 `Mode.PRIVATE` staging cavity 与 lip geometry
   - 把 body 和 lid 语义显式拆开
   - 对应外部 enclosure 实验 `test_runs/20260413_094502`

## 运行整套 demo

```bash
cd ~/code/aicad.subagent.build123d
uv run python demos/build123d_foundations/run_all.py
```

生成的产物会落在 `demos/build123d_foundations/artifacts/`。

## 汇报时可以直接讲的要点

1. Build123d 用 `Plane` 和 `Locations` 把 local frame 变成了一等公民，这和我们的 hole array、face-local feature 场景非常匹配。
2. Builder-native 的 boolean mode 比起临时拼接的 chained workplane state，更容易被 lint、repair 和解释。
3. `Mode.PRIVATE` 很适合在活动 builder 内 staging solid，避免过早污染 host。
4. 这些模式比旧的 CadQuery 风格契约更容易沉淀成确定性的 runtime guidance、preflight lint 和 validator expectation。
