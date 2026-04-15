# Build123d vs CadQuery Benchmark 对照摘要

这份材料回答两个问题：

1. 今天这轮 fresh benchmark，Build123d 有没有已经整体超过 CadQuery？
2. 如果没有，为什么我仍然判断 Build123d 的长期天花板更高、值得继续投？

先给结论：

- 如果只看今天这轮同题主对照，答案是否定的。`L1_218 / L2_130 / L2_172` 三题里，Build123d 目前是 `1 题基本持平、2 题落后`。
- 但如果看“发现一类失败后，系统能不能用产品级规则快速把整个 family 收住”，Build123d 已经给出了更强的证据。
- 最硬的一条证据来自 `L2_88`：同一个 Build123d 仓库、同一个 benchmark case、同一个模型，只改了通用 prompt/skill/lint 契约，fresh run 就从 `8 轮失败` 变成 `1 轮通过，eval_score=1.0`。

所以今天最准确的话术不是“Build123d 已经全面赢了”，而是：

> Build123d 已经证明了自己更适合被 runtime 编程。一旦我们把某条几何 family 的规则写对，它更容易通过显式 contract、preflight lint、repair recipe 形成整类提升。

## 对照方法

本次对照只采用 fresh run，不使用旧印象或历史截图。

- Build123d 主对照 run root：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747`
- CadQuery 主对照 run root：
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748`
- Build123d 泛化修复前的 `L2_88`：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88`
- Build123d 泛化修复后的 `L2_88`：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88`

模型与 provider 保持一致：

- provider: `kimi`
- model: `kimi-k2.5-thinking`

## 主对照结果

| Case | Build123d | CadQuery | 当前结论 |
| --- | --- | --- | --- |
| `L1_218` | 1 轮，`eval_score=0.9434446026297688`，`8437 tokens` | 1 轮，`eval_score=0.9598178326010692`，`6774 tokens` | 基本持平，CadQuery 略优 |
| `L2_130` | 5 轮，`eval_score=0.8825698404454414`，`46510 tokens` | 1 轮，`eval_score=0.9126310573254163`，`5822 tokens` | CadQuery 明显更快 |
| `L2_172` | 8 轮未收敛，`eval_score=0.3435717237134833`，`97012 tokens` | 7 轮收敛，`eval_score=1.0`，`101136 tokens` | CadQuery 明显胜出 |

这说明：

- 今天不能说 Build123d 已经整体领先。
- 但这还不能直接推出“Build123d 不值得继续投”，因为长期上限看的不是当天均值，而是失败能否高效产品化沉淀。

## 核心判断：为什么仍然认为 Build123d 的长期天花板更高

判断依据不是“它更新”，也不是“语法更漂亮”，而是三个更硬的事实。

### 1. Build123d 的几何语义对象更显式

当前 runtime 已经把这些对象接进了产品契约：

- `BuildPart`
- `BuildSketch`
- `BuildLine`
- `Plane`
- `Axis`
- `Locations`
- `Mode.SUBTRACT / Mode.INTERSECT / Mode.PRIVATE`

这使 runtime 更容易判断错误到底发生在：

- builder 上下文
- local frame / workplane
- boolean staging
- helper / keyword contract

对应源码：

- `src/sub_agent/prompts/codegen.md`
- `src/sub_agent_runtime/skill_pack.py`
- `src/sub_agent_runtime/tool_runtime.py`

### 2. Build123d 的失败更容易前移成结构化 lint

这一轮已经落地并生效的 Build123d preflight 规则包括：

- `invalid_build123d_context.curve_requires_buildline`
- `invalid_build123d_keyword.revolve_angle_alias`
- `invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup`
- `invalid_build123d_context.countersinkhole_requires_buildpart`

这意味着新发现的失败不必总等到 sandbox traceback 才暴露，而是能在执行前被识别成：

- 哪类契约错了
- 应该触发什么 repair recipe
- 这个 repair recipe 是否能复用到同一族 case

### 3. 已经出现了“通用规则直接解锁一个 family”的真实证据

这一点不是架构描述，而是 `L2_88` 的前后对照：

- 修复前：`8 轮失败`
- 修复后：`1 轮通过`
- 改动位置不是 case 数据，而是通用 prompt / skill / lint contract

这条证据最能说明“长期上限更高”。

## Case 1：L1_218 说明当前基础 axisymmetric/annular family 已基本持平

### 结果

- Build123d:
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L1_218/summary.json`
  - `planner_rounds=1`
  - `validation_complete=true`
  - `eval_score=0.9434446026297688`
- CadQuery:
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L1_218/summary.json`
  - `planner_rounds=1`
  - `validation_complete=true`
  - `eval_score=0.9598178326010692`

### 关键 artifact 证据

1. `plans/round_01_response.json`
   - Build123d 代码直接采用 `Cylinder(...) + Box(..., mode=Mode.SUBTRACT)` 构建本体，再单独做 groove band 后布尔减。
   - CadQuery 代码使用 `circle().extrude()` / `rect().extrude()` / `cut()` 的经典链式写法。
2. `queries/round_01_validate_requirement_post_write.json`
   - Build123d validator 对以下子句都给出 `verified`：
   - `draw a circle with a diameter of 50.0 mm`
   - `a square with a side length of 25.0 mm centered`
   - `Extrude the section by 60.0 mm`
   - `use a revolved cut to create an annular groove`
3. `evaluation/benchmark_eval.json`
   - Build123d: `feature_anchor_rel_max=0.07304868897282052`
   - CadQuery: `feature_anchor_rel_max=0.04166666486111115`
   - 两者都 `passed=true`，且 `difference_notes` 都是 `STEP geometric signatures are closely aligned`

### 这题证明什么

- 基础 axisymmetric + local groove 族，Build123d 已经能稳定走通。
- 但在当前实现下，CadQuery 在局部 feature anchor 上仍稍微更贴近 ground truth。
- 所以 `L1_218` 证明的是“Build123d 已经具备可用基础盘”，不是“已经全面反超”。

## Case 2：L2_130 说明 Build123d 仍落后，但失败已经机制化

### 结果

- Build123d:
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_130/summary.json`
  - `planner_rounds=5`
  - `validation_complete=true`
  - `eval_score=0.8825698404454414`
  - `executed_action_types=["execute_build123d", ..., "execute_repair_packet"]`
- CadQuery:
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_130/summary.json`
  - `planner_rounds=1`
  - `validation_complete=true`
  - `eval_score=0.9126310573254163`

### 关键 artifact 证据

1. Build123d `actions/round_01_execute_build123d.json`
   - preflight 直接拦截：
   - `invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup`
   - 这条 lint 明确指出：在 `XZ/YZ` 平面上，`Plane.offset(...)` 沿法向平移，不是 in-plane 坐标。
2. Build123d `actions/round_03_execute_build123d.json`
   - preflight 再次拦截：
   - `invalid_build123d_contract.active_builder_temporary_primitive_arithmetic`
   - 说明模型还在用 active builder 内部的临时 primitive 算术，而不是 same-builder 的规范 skeleton。
3. Build123d `queries/round_05_validate_requirement_post_write.json`
   - 最终 validator 已经能把多数 clause 明确解释为 `verified`
   - 例如：
   - `an inner semicircle of radius 17.5 millimeters on the XY plane`
   - `remove the inner 35.0 millimeter diameter clearance`
   - `drill two 6.0 millimeter through-holes through the lugs in the Y direction`
   - `coverage_confidence=1.0`
4. Build123d `evaluation/benchmark_eval.json`
   - `difference_notes=["local feature-anchor deviation 0.119 is high"]`
   - `feature_anchor_rel_max=0.11902194186680076`
   - `face_rel_diff=0.14285714285714285`

### 这题证明什么

- 当前 Build123d 在 half-shell family 上，first-turn bias 仍明显不如 CadQuery。
- 但更重要的是，它的失败已经被收缩到非常具体的两层 contract：
  - directional drill 的 workplane / offset 语义
  - active builder 下的临时实体布尔纪律
- 这说明接下来该投入的是 family recipe，而不是对单个 case 堆补丁。

## Case 3：L2_172 说明 Build123d 当前最大的短板仍是 countersink family

### 结果

- Build123d:
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_091747/L2_172/summary.json`
  - `planner_rounds=8`
  - `converged=false`
  - `validation_complete=false`
  - `eval_score=0.3435717237134833`
- CadQuery:
  - `/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260414_091748/L2_172/summary.json`
  - `planner_rounds=7`
  - `converged=true`
  - `validation_complete=true`
  - `eval_score=1.0`

### Build123d 失败证据

1. `actions/round_02_execute_build123d.json`
   - preflight 拦截：
   - `invalid_build123d_api.countersink_helper_name`
   - 明确指出 Build123d 应该使用 `CounterSinkHole(...)`，而不是猜错 helper 名。
2. `actions/round_05_execute_build123d.json`
   - preflight 拦截：
   - `invalid_build123d_keyword.countersink_angle_alias`
   - 明确指出应使用 `counter_sink_angle=`，不是 `countersink_angle=`
3. `queries/round_08_validate_requirement_post_write.json`
   - 这份验证结果最关键，因为它把“哪里已经对了、哪里还没对”说得很清楚：
   - `feature_hole = pass`
   - `feature_hole_position_alignment = pass`
   - `feature_hole_exact_center_set = pass`
   - `feature_local_anchor_alignment = pass`
   - `feature_countersink = fail`
   - 失败证据是：
   - `countersink_action=False, snapshot_countersink_geometry=False, hole_feature=True, cone_like_face_present=False`
4. `evaluation/benchmark_eval.json`
   - `face_rel_diff=0.5714285714285714`
   - `face_type_iou=0.38461538461538464`
   - `feature_anchor_rel_max=1.0`
   - `difference_notes` 明确指出：
   - `face count relative deviation 0.571 is high`
   - `local feature-anchor deviation 1.000 is high`
   - `face-type histogram overlap is low (0.385)`

### CadQuery 对照证据

1. `plans/round_07_response.json`
   - `decision_summary` 明确写出：
   - 先把 requirement 点从 corner-based frame 归一化到 centered frame：
   - `(-25,-15), (-25,15), (25,-15), (25,15)`
   - 然后在 top face 上使用 `pushPoints(points).cskHole(...)`
2. `queries/round_07_validate_requirement_post_write.json`
   - `feature_countersink = pass`
   - 证据字符串是：
   - `snapshot_countersink_geometry=True, cone_like_face_present=True`
3. `evaluation/benchmark_eval.json`
   - `passed=true`
   - `final_score=1.0`
   - `face_type_iou=1.0`
   - `feature_anchor_rel_max≈1.04e-08`

### 这题证明什么

- Build123d 并不是完全不会做这题的“局部坐标 + 点位”部分。
- 它已经把孔位坐标、hole center set、local anchor layout 都走对了。
- 真正没收敛的是 countersink family 的统一表达：
  - helper 选择
  - keyword 契约
  - 宿主 face plane
  - through-hole + cone 的语义统一
- 这就是 Build123d 当前最明确、也最值得继续投的主战场。

## 最关键的正证据：L2_88 前后对照

如果没有这条证据，“Build123d 天花板更高”只能算判断；有了它，这个判断才真正站住。

### 修复前

- 目录：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_093041/L2_88`
- `summary.json`：
  - `planner_rounds=8`
  - `converged=false`
  - `validation_complete=false`
  - `total_tokens=57224`
  - `failure_cluster="code_path_family_gap"`

前两轮最关键的失败：

1. `actions/round_01_execute_build123d.json`

```text
TypeError: revolve() got an unexpected keyword argument 'angle'
```

2. `actions/round_02_execute_build123d.json`

```text
RuntimeError: BuildSketch doesn't have a Polyline object or operation
(Polyline applies to ['BuildLine'])
```

这两条都不是 case 私有知识，而是通用 Build123d contract：

- `revolve(...)` 的合法签名
- curve helper 的 builder 归属

### 这次具体改了什么

本轮没有改 benchmark case，也没有改 evaluator。

真正改的是 3 个产品层：

1. `src/sub_agent/prompts/codegen.md`
   - 明确：
   - `Polyline(...) / Line(...) / CenterArc(...) / RadiusArc(...)` 属于 `BuildLine`
   - `revolve(...)` 不接受 `angle=...`，要用默认 360 或 `revolution_arc=...`
2. `src/sub_agent_runtime/skill_pack.py`
   - 强化 explicit revolve profile recipe：
   - `BuildSketch(target_plane) -> BuildLine() -> make_face() -> revolve(axis=...)`
3. `src/sub_agent_runtime/tool_runtime.py`
   - 新增 preflight lint：
   - `invalid_build123d_context.curve_requires_buildline`
   - `invalid_build123d_keyword.revolve_angle_alias`

相关验证：

- `PATH="/opt/homebrew/bin:$PATH" uv run pytest -q tests/unit/sub_agent_runtime/test_codegen_prompt_contract.py tests/unit/sub_agent_runtime/test_skill_pack.py tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py`
- 输出：
  - `78 passed in 1.38s`

### 修复后

- 目录：
  - `/Users/jerryx/code/aicad.subagent.build123d/benchmark/runs/20260414_130005/L2_88`
- `summary.json`：
  - `planner_rounds=1`
  - `converged=true`
  - `validation_complete=true`
  - `total_tokens=4462`
- `trace/stop_reason.json`：
  - `post_write_validated_complete`
- `queries/round_01_validate_requirement_post_write.json`
  - `summary = Requirement validation passed`
  - `coverage_confidence = 1.0`
- `evaluation/benchmark_eval.json`
  - `passed=true`
  - `final_score=1.0`
  - `difference_notes=["STEP geometric signatures are closely aligned"]`

最关键的是 `plans/round_01_response.json` 里的代码已经直接变成正确的 Build123d canonical recipe：

```python
with BuildSketch(Plane.XZ):
    with BuildLine():
        Line((10, 0), (25, 0))
        Line((25, 0), (25, 15))
        ...
    make_face()
revolve(axis=Axis.Z)
```

### 这条前后对照证明什么

- Build123d 的提升可以通过通用 runtime contract 直接落地。
- 一旦 contract 被写对，模型会在首轮就自动进入正确 recipe，而不是必须多轮试错。
- 这就是我判断 Build123d 长期天花板更高的最硬证据。

## 为什么说这不是“碰巧命中”

因为这次提升同时满足 4 个条件：

1. 同一题、同一仓库、同一模型。
2. 没有改 benchmark case，也没有改 evaluator。
3. 改的是 prompt / skill / preflight lint 这些产品层规则。
4. 单测和 fresh benchmark 都同时证明规则已经进入主路径。

如果只是 prompt 运气，不会出现这种“同 family 从失败直接变成 1 轮满分”的效果。

## 当前最诚实的汇报话术

可以直接说：

> 今天的主 benchmark 均值还没有证明 Build123d 已经超过 CadQuery。  
> 但 Build123d 已经证明了自己更适合被 runtime 编程。  
> 我们已经能把一类失败前移成显式 contract、lint、repair recipe，而且 `L2_88` 已经证明这种产品级修复可以直接拉起同一条几何 family。

不能过度说：

- “Build123d 现在已经全面更强”
- “只要切库就会自然变好”

## 接下来应该继续投哪里

接下来的投入不应该是 case patch，而应该继续投 family recipe：

1. `axisymmetric_profile`
   - 这轮已经证明 generic fix 有效，值得继续固化成默认一轮偏置。
2. `half_shell + directional_hole`
   - 继续加强 same-builder skeleton 与 local-frame/drill-plane contract。
3. `explicit_anchor_hole / countersink`
   - 统一 helper、keyword、宿主 plane、centered host frame normalization、through-hole + cone 语义。

## 对外转发建议

如果需要一份更细的证据版文档，请直接配合下面这份一起转发：

- `report-20260414-build123d-vs-cadquery-benchmark/detailed-evidence.md`

这份重证据文档会把 `plans / actions / queries / trace / evaluation` 的具体路径、关键字段和它们各自证明的结论逐条写清楚。
