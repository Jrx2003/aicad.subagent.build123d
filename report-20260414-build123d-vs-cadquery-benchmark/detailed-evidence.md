# 2026-04-14 Build123d vs CadQuery Benchmark 重证据对照报告

## 一句话结论

截至 `2026-04-14` 这轮 fresh benchmark，Build123d 还没有在总体结果上超过 CadQuery；但它已经给出了一条比 CadQuery 更强的长期价值证据链：`Build123d 更容易把几何建模失败沉淀成显式 contract、preflight lint 和 repair recipe，而这种产品级规则已经在 L2_88 上证明可以直接拉起整个 family。`

## 1. 对照范围与方法

### 1.1 主对照 run

- Build123d：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747`
- CadQuery：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748`

### 1.2 泛化修复前后对照 run

- Build123d 修复前 `L2_88`：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88`
- Build123d 修复后 `L2_88`：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88`
- CadQuery 同题 `L2_88`：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_093041/L2_88`

### 1.3 统一条件

- provider: `kimi`
- model: `kimi-k2.5-thinking`
- benchmark 模式：fresh run
- 判定标准：
  - runtime summary
  - validator query
  - evaluator `step_geometric_signature`

## 2. 主对照总表

| Case | Build123d 结果 | CadQuery 结果 | 结论 |
| --- | --- | --- | --- |
| `L1_218` | 1 轮，`validation_complete=true`，`eval_score=0.9434446026297688` | 1 轮，`validation_complete=true`，`eval_score=0.9598178326010692` | 基本持平，CadQuery 略优 |
| `L2_130` | 5 轮，`validation_complete=true`，`eval_score=0.8825698404454414` | 1 轮，`validation_complete=true`，`eval_score=0.9126310573254163` | CadQuery 明显更稳 |
| `L2_172` | 8 轮未收敛，`validation_complete=false`，`eval_score=0.3435717237134833` | 7 轮收敛，`validation_complete=true`，`eval_score=1.0` | CadQuery 明显胜出 |

### 2.1 结论边界

- 这张表不能支持“Build123d 已经更强”。
- 但这张表也不能直接支持“Build123d 长期不值得投”。
- 长期价值要看：新发现的失败能不能快速变成通用产品规则，并在后续 case 上复用。

## 3. 为什么我判断 Build123d 的长期天花板更高

这部分不靠 CAD 库宣传，只靠当前仓库里的源码与 benchmark 证据。

### 3.1 显式对象层更适合被 runtime 编程

当前仓库已经把以下 Build123d 对象接成了真正的产品表面：

- `BuildPart`
- `BuildSketch`
- `BuildLine`
- `Plane`
- `Axis`
- `Locations`
- `Mode.SUBTRACT`
- `Mode.INTERSECT`
- `Mode.PRIVATE`

对应源码：

- `src/sub_agent/prompts/codegen.md`
- `src/sub_agent_runtime/skill_pack.py`
- `src/sub_agent_runtime/tool_runtime.py`

这些对象的价值不在“语法更现代”，而在 runtime 可以更准确地区分：

- builder 上下文错了
- plane / axis / local frame 错了
- boolean staging 错了
- helper / keyword 猜错了

### 3.2 失败可以被前移成结构化 lint

本轮有效生效的 Build123d preflight surfaces 包括：

- `invalid_build123d_context.curve_requires_buildline`
- `invalid_build123d_keyword.revolve_angle_alias`
- `invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup`
- `invalid_build123d_context.countersinkhole_requires_buildpart`

这意味着失败不再只是：

- 运行后 traceback
- 人类读日志再总结经验

而是能直接变成：

- 失败类型
- 修复 recipe
- 可复用的 family 级规则

### 3.3 `L2_88` 已经证明 family 级修复真的能落地

这一点是本文最重要的正证据，后面单独展开。

## 4. Case dossier：L1_218

### 4.1 摘要结果

#### Build123d

- 文件：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L1_218/summary.json`
- 关键字段：
  - `planner_rounds=1`
  - `validation_complete=true`
  - `first_write_tool=execute_build123d`
  - `total_tokens=8437`

#### CadQuery

- 文件：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L1_218/summary.json`
- 关键字段：
  - `planner_rounds=1`
  - `validation_complete=true`
  - `first_write_tool=execute_cadquery`
  - `total_tokens=6774`

### 4.2 计划与代码证据

#### Build123d `plans/round_01_response.json`

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L1_218/plans/round_01_response.json`

关键代码片段：

```python
with BuildPart() as base:
    Cylinder(radius=25, height=60, align=(Align.CENTER, Align.CENTER, Align.MIN))
    Box(25, 25, 60, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

with BuildPart() as groove_band:
    Cylinder(radius=25, height=2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    Cylinder(radius=20, height=2, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
```

这个片段证明：

- Build123d 在基础本体 + annular band subtraction 这类 axisymmetric family 上已经能稳定生成可执行 recipe。
- 它没有回退到 CadQuery 旧契约，而是在使用 builder-native / mode-native 的写法。

#### CadQuery `plans/round_01_response.json`

文件：

- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L1_218/plans/round_01_response.json`

关键代码片段：

```python
outer_cyl = cq.Workplane("XY").circle(outer_radius).extrude(extrude_height)
inner_square = cq.Workplane("XY").rect(inner_square_side, inner_square_side).extrude(extrude_height)
base = outer_cyl.cut(inner_square)
```

这个片段证明：

- CadQuery 在这类题上仍然很成熟。
- 两边都不是靠多轮 repair 才过，而是一轮直接写出正确 skeleton。

### 4.3 验证与几何证据

#### Build123d validator

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L1_218/queries/round_01_validate_requirement_post_write.json`

关键 clause：

- `draw a circle with a diameter of 50.0 mm` -> `verified`
- `a square with a side length of 25.0 mm centered` -> `verified`
- `Extrude the section by 60.0 mm` -> `verified`
- `use a revolved cut to create an annular groove` -> `verified`

这说明：

- validator 对几何本体和局部 annular groove 都建立了直接证据链，而不是“整体看起来差不多”。

#### evaluator 对照

文件：

- Build123d:
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L1_218/evaluation/benchmark_eval.json`
- CadQuery:
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L1_218/evaluation/benchmark_eval.json`

关键字段：

- Build123d:
  - `passed=true`
  - `final_score=0.9434446026297688`
  - `feature_anchor_rel_max=0.07304868897282052`
  - `surface_area_rel_diff=0.043478465203954514`
- CadQuery:
  - `passed=true`
  - `final_score=0.9598178326010692`
  - `feature_anchor_rel_max=0.04166666486111115`
  - `surface_area_rel_diff=0.04347846520398672`

### 4.4 L1_218 结论

- Build123d 已经能和 CadQuery 在这类基础 case 上打到相当接近的水平。
- 但当前 CadQuery 在局部 feature anchor 贴合度上仍稍优。
- 这题支持“Build123d 基础盘已经可用”，不支持“已经全面领先”。

## 5. Case dossier：L2_130

### 5.1 摘要结果

#### Build123d

- 文件：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/summary.json`
- 关键字段：
  - `planner_rounds=5`
  - `executed_action_types=["execute_build123d","execute_build123d","execute_build123d","execute_build123d","execute_repair_packet"]`
  - `validation_complete=true`
  - `total_tokens=46510`

#### CadQuery

- 文件：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_130/summary.json`
- 关键字段：
  - `planner_rounds=1`
  - `executed_action_types=["execute_cadquery"]`
  - `validation_complete=true`
  - `total_tokens=5822`

### 5.2 Build123d 失败与修复链

#### round 1：plane offset 语义错误被前移拦截

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/actions/round_01_execute_build123d.json`

关键 stderr：

```text
invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup
```

它证明：

- 模型首轮在 directional drilling 上仍会混淆 `Plane.offset(...)` 的法向平移和 in-plane 坐标。
- 但这个错误已经不再等 sandbox 爆炸，而是被 preflight 机制化识别。

#### round 3：临时实体布尔纪律错误被前移拦截

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/actions/round_03_execute_build123d.json`

关键 stderr：

```text
invalid_build123d_contract.active_builder_temporary_primitive_arithmetic
```

它证明：

- 当前 Build123d 在 half-shell family 上，same-builder skeleton 仍不稳。
- 但问题已经被归类为一条通用 contract，而不是 case 私有经验。

#### final validation：多数 clause 已被明确验证

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/queries/round_05_validate_requirement_post_write.json`

关键字段：

- `coverage_confidence=1.0`
- `insufficient_evidence=false`

关键 `clause_interpretations`：

- `an inner semicircle of radius 17.5 millimeters on the XY plane` -> `verified`
- `remove the inner 35.0 millimeter diameter clearance` -> `verified`
- `drill two 6.0 millimeter through-holes through the lugs in the Y direction` -> `verified`

这说明：

- 到最终收口时，Build123d 并不是“蒙混过 validator”，而是已经能给出较完整的 feature-level 证据。

### 5.3 evaluator 几何证据

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/evaluation/benchmark_eval.json`

关键字段：

- `difference_notes=["local feature-anchor deviation 0.119 is high"]`
- `feature_anchor_rel_max=0.11902194186680076`
- `face_rel_diff=0.14285714285714285`
- `edge_rel_diff=0.14285714285714285`
- `final_score=0.8825698404454414`

这说明：

- Build123d 最终整体形体已经接近正确。
- 但局部孔位/特征锚点仍存在可见偏差。

### 5.4 L2_130 结论

- 当前 CadQuery 在这条 half-shell family 上依然明显更成熟。
- 但 Build123d 的失败已经收缩成明确的 plane/boolean contract 缺口。
- 这类 gap 是可以继续产品化沉淀的，方向是健康的。

## 6. Case dossier：L2_172

### 6.1 摘要结果

#### Build123d

- 文件：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/summary.json`
- 关键字段：
  - `planner_rounds=8`
  - `converged=false`
  - `validation_complete=false`
  - `total_tokens=97012`

#### CadQuery

- 文件：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_172/summary.json`
- 关键字段：
  - `planner_rounds=7`
  - `converged=true`
  - `validation_complete=true`
  - `total_tokens=101136`

### 6.2 Build123d 的关键失败链

#### round 2：helper 名称错误

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/actions/round_02_execute_build123d.json`

关键 stderr：

```text
invalid_build123d_api.countersink_helper_name
Build123d uses `CounterSinkHole(...)`
```

这说明：

- 当前模型已经知道要进入 countersink family。
- 但 canonical helper 仍会猜错。

#### round 5：keyword 契约错误

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/actions/round_05_execute_build123d.json`

关键 stderr：

```text
invalid_build123d_keyword.countersink_angle_alias
`CounterSinkHole(...)` uses `counter_sink_angle=...`
```

这说明：

- 即使 helper 已经收窄，参数层 contract 仍不稳定。

#### final plan：模型已经在尝试 centered host + top face 语义

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/plans/round_08_response.json`

关键代码片段：

```python
Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_HEIGHT)

# The top face is at +PLATE_HEIGHT/2 = +4.0
```

这说明：

- Build123d 并没有完全走偏到错误几何家族。
- 它已经在尝试 centered box + top face 这一条正确方向。
- 但 through-hole + conical countersink 的 canonical recipe 还没统一下来。

### 6.3 final validator：失败原因非常具体

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/queries/round_08_validate_requirement_post_write.json`

关键字段：

- `is_complete=false`
- `coverage_confidence=0.5294117647058824`
- `insufficient_evidence=true`

最关键的检查项：

- `feature_hole = pass`
- `feature_hole_position_alignment = pass`
- `feature_hole_exact_center_set = pass`
- `feature_local_anchor_alignment = pass`
- `feature_countersink = fail`

失败证据字符串：

```text
countersink_action=False, snapshot_countersink_geometry=False, hole_feature=True, cone_like_face_present=False
```

这条证据非常重要，因为它说明：

- 坐标没有错
- 孔位没有错
- base plate / merged body 没有错
- 真正错的是 countersink 语义本身

### 6.4 evaluator：几何差异也支持同一结论

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/evaluation/benchmark_eval.json`

关键字段：

- `face_rel_diff=0.5714285714285714`
- `face_type_iou=0.38461538461538464`
- `feature_anchor_rel_max=1.0`
- `final_score=0.3435717237134833`

`difference_notes`：

- `face count relative deviation 0.571 is high`
- `local feature-anchor deviation 1.000 is high`
- `face-type histogram overlap is low (0.385)`

这说明：

- Build123d 最终几何并不是简单“只差一点点沉头深度”。
- 它在 conical countersink 这个特征层面仍然明显偏离 ground truth。

### 6.5 CadQuery 对照证据

#### final plan：normalized points + top face + cskHole

文件：

- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_172/plans/round_07_response.json`

关键 `decision_summary`：

- 明确指出宿主是 centered frame
- 将 `(25,15),(25,45),(75,15),(75,45)` 归一化成 `(-25,-15),(-25,15),(25,-15),(25,15)`
- 最终采用：
  - `.faces(">Z").workplane().pushPoints(points).cskHole(...)`

这说明：

- CadQuery 不是“轻松秒过”，而是经过多轮后最终把 centered host frame normalization 和 countersink helper 收住了。

#### final validator

文件：

- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_172/queries/round_07_validate_requirement_post_write.json`

关键字段：

- `is_complete=true`
- `feature_countersink = pass`

关键证据字符串：

```text
countersink_action=False, snapshot_countersink_geometry=True, hole_feature=True, cone_like_face_present=True
```

#### final evaluator

文件：

- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_172/evaluation/benchmark_eval.json`

关键字段：

- `passed=true`
- `final_score=1.0`
- `face_type_iou=1.0`
- `feature_anchor_rel_max=1.0416604245834984e-08`

### 6.6 L2_172 结论

- 当前 Build123d 最大的剩余短板非常清楚：
  - `explicit_anchor_hole / named_face_local_edit / countersink`
- 这条 family 还没有形成稳定的一轮 canonical recipe。
- 但失败面已经从“完全猜错库”收缩到了“明确的 helper / keyword / host-plane / cone geometry contract”。

## 7. 最关键的正证据：L2_88 前后对照

### 7.1 修复前 run

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88/summary.json`

关键字段：

- `planner_rounds=8`
- `converged=false`
- `validation_complete=false`
- `inspection_requested_rounds=4`
- `failure_cluster="code_path_family_gap"`
- `total_tokens=57224`

#### round 1 失败

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88/actions/round_01_execute_build123d.json`

关键 stderr：

```text
TypeError: revolve() got an unexpected keyword argument 'angle'
```

#### round 2 失败

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88/actions/round_02_execute_build123d.json`

关键 stderr：

```text
RuntimeError: BuildSketch doesn't have a Polyline object or operation
(Polyline applies to ['BuildLine'])
```

### 7.2 这轮产品级改动

#### prompt contract

文件：

- `src/sub_agent/prompts/codegen.md`

新增重点：

- curve helper 必须在 `BuildLine`
- `revolve(...)` 不能写 `angle=...`
- revolve profile 要走 `BuildSketch -> BuildLine -> make_face -> revolve`

#### runtime skill

文件：

- `src/sub_agent_runtime/skill_pack.py`

新增重点：

- 明确 explicit revolve profile recipe
- 把 BuildLine / revolve contract 写成 runtime guidance

#### preflight lint

文件：

- `src/sub_agent_runtime/tool_runtime.py`

新增重点：

- `invalid_build123d_context.curve_requires_buildline`
- `invalid_build123d_keyword.revolve_angle_alias`
- 对应 repair recipe：`build123d_revolve_profile_contract`

#### 测试验证

命令：

```bash
PATH="/opt/homebrew/bin:$PATH" uv run pytest -q \
  tests/unit/sub_agent_runtime/test_codegen_prompt_contract.py \
  tests/unit/sub_agent_runtime/test_skill_pack.py \
  tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py
```

结果：

```text
78 passed in 1.38s
```

### 7.3 修复后 run

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88/summary.json`

关键字段：

- `planner_rounds=1`
- `converged=true`
- `validation_complete=true`
- `executed_action_types=["execute_build123d"]`
- `total_tokens=4462`

#### final plan

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88/plans/round_01_response.json`

关键 `decision_summary`：

```text
Following the explicit revolve profile recipe, I'll build a closed 2D profile on the plane containing the rotation axis and revolve it 360 degrees.
```

关键代码片段：

```python
with BuildPart() as part:
    with BuildSketch(Plane.XZ):
        with BuildLine() as profile_line:
            Line((10, 0), (25, 0))
            Line((25, 0), (25, 15))
            Line((25, 15), (20, 15))
            Line((20, 15), (20, 20))
            Line((20, 20), (10, 20))
            Line((10, 20), (10, 0))
        make_face()
```

它证明：

- 模型首轮已经自动进入正确的 Build123d canonical recipe。

#### final validator

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88/queries/round_01_validate_requirement_post_write.json`

关键字段：

- `summary="Requirement validation passed"`
- `coverage_confidence=1.0`
- `insufficient_evidence=false`

关键 clause：

- `draw a vertical centerline through the origin as the axis of rotation` -> `verified`
- `start from point (10.0, 0)` -> `verified`
- `draw horizontally outward to (25.0, 0)` -> `verified`
- `draw vertically upward to (25.0, 15.0)` -> `verified`

这说明：

- validator 已经能直接理解这条 axisymmetric band family 的关键几何点与轴。

#### final action snapshot

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88/actions/round_01_execute_build123d.json`

关键 `snapshot.geometry`：

- `solids=1`
- `faces=6`
- `edges=9`
- `bbox=[50.0, 50.0, 20.0]`
- `volume=29452.431127404176`

#### final evaluator

文件：

- `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88/evaluation/benchmark_eval.json`

关键字段：

- `passed=true`
- `final_score=1.0`
- `difference_notes=["STEP geometric signatures are closely aligned"]`
- `face_type_iou=1.0`
- `feature_anchor_rel_max≈1.04e-13`

### 7.4 CadQuery 同题对照

文件：

- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_093041/L2_88/summary.json`
- `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_093041/L2_88/evaluation/benchmark_eval.json`

关键结果：

- `planner_rounds=1`
- `validation_complete=true`
- `final_score=1.0`

这说明：

- 修复后的 Build123d 已经把这条 family 拉回到与 CadQuery 同级的成熟度。
- 关键在于：Build123d 的回升来自通用产品规则，而不是 case-specific patch。

### 7.5 L2_88 结论

这条前后对照是本文最重要的论据，因为它证明了：

1. Build123d 的失败可以被提炼成通用 contract。
2. 通用 contract 一旦接入 prompt / skill / lint 主路径，会直接影响首轮行为。
3. 同一类 case 的能力提升可以通过少量产品层改动获得，而不是反复堆案例补丁。

## 8. 这组证据真正支持的结论

### 8.1 可以支持

- Build123d 当前已经具备独立、显式、可编程的 runtime 契约面。
- Build123d 的失败更容易沉淀成 preflight lint 和 repair recipe。
- Build123d 在 axisymmetric revolve family 上，已经出现了“通用规则直接拉起整类 case”的强证据。
- Build123d 的长期天花板更高，这个判断在当前仓库里已经有足够具体的工程证据支撑。

### 8.2 不能支持

- 不能说 Build123d 当前 benchmark 均值已经超过 CadQuery。
- 不能说切库本身就会自然提升表现。
- 不能说 half-shell 和 countersink family 已经完成迁移。

## 9. 继续投入 Build123d 的正确方向

接下来应该继续投的是 family-level recipe，而不是 case patch：

1. `axisymmetric_profile`
   - 已经被 `L2_88` 证明值得继续固化。
2. `half_shell + directional_hole`
   - 继续提升 first-turn same-builder skeleton。
3. `explicit_anchor_hole / countersink`
   - 统一 helper、keyword、host face、corner-frame 到 centered-frame 的归一化，以及 through-hole + cone 的 canonical 建模路径。

## 10. 推荐汇报时的表述

建议直接用下面这段：

> 今天的 fresh benchmark 还没有证明 Build123d 已经整体超过 CadQuery。  
> 但 Build123d 已经证明了更强的产品化潜力。  
> 因为我们已经可以把几何建模错误提炼成显式 contract、preflight lint 和 repair recipe，而且 `L2_88` 已经证明，这样的通用规则可以把同一条几何 family 从失败直接拉到一轮满分。  
> 这说明 Build123d 的长期天花板更高，继续投入是有工程证据支撑的。
