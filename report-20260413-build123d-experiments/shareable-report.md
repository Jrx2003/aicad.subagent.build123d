## 执行摘要

现在这次 Build123d 迁移，已经有足够真实的证据支撑一次清楚、诚实、可复盘的汇报。

今天最合理的结论不是“整个仓库已经彻底完成迁移”，而是下面这 3 点：

1. 运行时已经形成了更强的 Build123d 契约表面
2. 我们已经有一套小而清晰的演示案例，能直接对应最近解决过的运行时问题
3. 基准评测与外部探针已经能说明：Build123d 在哪些地方带来了收益，哪些 family 仍然没有完全收口

最适合在汇报里直接说的一句话是：

Build123d 正在给这个仓库带来更清晰的局部坐标控制、更干净的构建器级布尔表达，以及更可确定的预检修复面，这些都比旧的 CadQuery 契约更适合我们的运行时。

## 当前架构

这个仓库仍然是一个隔离出来的迭代式 CAD 运行时：

1. 上游先给出归一化后的 requirement
2. `IterativeSubAgentRunner` 和 V2 循环组装上下文
3. 模型选择 `execute_build123d` 或读取类工具
4. sandbox 执行 Build123d 代码
5. validator 和 kernel refresh 工具生成写后证据
6. 全部中间产物继续保留在 `prompts/`、`plans/`、`actions/`、`queries/`、`trace/`、`outputs/` 和 `summary.json`

从迁移角度看，Build123d 的实际价值在于它和这个运行时的需求更贴合：

1. `Plane` 和 `Locations` 很适合局部坐标特征
2. `Mode.SUBTRACT` 和 `Mode.INTERSECT` 很适合同一构建器内的修复配方
3. `Mode.PRIVATE` 很适合 cavity / lip 这类需要暂存几何而又不想提前污染 host 的场景
4. 无效 API 的使用可以更容易被前移成确定性的 preflight lint，而不是变成 opaque 的内核报错

## 这次具体补了什么

这次交付主要围绕 3 件事组织：

1. 把仓库身份和说明面统一到 `aicad.subagent.build123d`
2. 增加 `demos/build123d_foundations/` 作为可演示的 Build123d 说明面
3. 增加 `report-20260413-build123d-experiments/`，让这轮工作不依赖口头补充也能看懂

这套演示案例刻意保持很小，只做 3 个：

1. 局部坐标 countersink 板
2. 半壳体与定向打孔
3. 壳体本体与 lip-fit 盖子

它们并不是随意挑的，而是当前最能代表真实运行时问题的最小样例。

## 负例

这次最值得讲的负例是：

1. run：`test_runs/20260413_094502`
2. prompt：一个带 shelled body、lip-fit cover 和 top-face countersunk fastener holes 的紧凑 enclosure

这个案例之所以重要，是因为：

1. 它是未知风格的外部 requirement，不是基准评测里的熟悉锚点
2. 它同时包含 shelling、body/lid 关系、top-face fastener 这几类容易相互影响的语义
3. 它能很好地暴露迁移是否真的在往通用能力方向前进

这次发生的事情是有价值的：

1. 首轮写入被 preflight lint 直接拦住
2. 失败被归类成 `execute_build123d_api_lint_failure`
3. 具体规则是 `legacy_api.countersink_workplane_method`
4. 运行时返回了明确的修复 recipe：`explicit_anchor_hole_countersink_array_safe_recipe`

这比旧的黑盒失败路径明显更好，因为它给运行时留下了可教学、可验证、可复用的修复表面。

## 正例

### 正例 A：`benchmark/runs/20260413_102600/L2_172`

这是当前最干净、最适合对外讲的正例。

关键结果：

1. `planner_rounds=1`
2. `first_write_tool=execute_build123d`
3. `validation_complete=true`
4. evaluator `passed=true`

为什么能通过：

1. 模型明确把 corner-based 草图坐标翻译到了居中宿主坐标系
2. 最终几何在 1 次 Build123d 写入中就满足了 countersink plate requirement
3. 这正是我们希望从 Build123d 获得的局部坐标清晰度

### 正例 B：`benchmark/runs/20260413_141000/L1_218`

这个案例比纯粹的一轮成功更适合讲“系统质量”。

关键结果：

1. `planner_rounds=5`
2. `converged=true`
3. `validation_complete=true`

为什么它重要：

1. groove 路径是在循环内部被修回来的
2. 运行时成功把这个案例拉回到了更清晰的 Build123d 契约表达
3. 它说明迁移的价值不只是 pass rate 提升，也包括把修复过程变得更结构化

## 工件链路

这次仍然保留了完整的可汇报工件链：

1. `prompts/`：每一轮模型实际看到的请求
2. `plans/`：模型的决策摘要和工具调用载荷
3. `actions/`：Build123d 执行结果或 lint 失败
4. `queries/`：validator、kernel state、probe 证据
5. `trace/`：轮次时间线和停止原因
6. `outputs/`：生成的 STEP 产物
7. `evaluation/`：启用评分时的基准评测对照输出

这很重要，因为这次迁移改变的不只是几何代码本身，也改变了这个运行时被调试、被解释、被汇报的难易度。

## 结构化状态与运行时回放

有 3 个回放片段特别值得在汇报里提：

1. `L2_172` 的 decision summary 明确写出了：它会先把 corner-frame 坐标映射到居中宿主坐标系，再写 Build123d 代码
2. 外部 enclosure 探针给出了结构化修复 recipe，而不是在一个泛化 sandbox failure 里直接终止
3. `L2_130` 说明了为什么打孔 frame 契约很重要：
   - 在 `Plane.XZ` 上，局部坐标是 `(x, z)`
   - `offset(...)` 沿的是平面法向，而不是 feature 高度变量本身

这些都说明 Build123d 正在帮助我们把原本模糊的建模行为，收束成更显式的运行时契约。

## 当前判断

这次迁移已经跨过了“只能内部试试”的阶段，进入了“足以拿出来做团队汇报”的阶段。

但结论必须保持克制：

1. Build123d 已经改善了几个核心运行时表面
2. 仓库现在已经有演示材料和真实证据来解释这些改善
3. 这项工作仍处于实验推进阶段，而不是最终完成阶段

当前剩余最明显的开放 family，仍然是 enclosure 风格的 shell + lid 组合推理。

## 下一步计划

1. 保持演示套件小而稳定，让它成为默认的 Build123d 解释面
2. 继续把基准评测和外部探针当成通用化信号，而不是只优化少量锚点案例
3. 持续聚焦 shell opening 语义、body/lid 拆解、top-face fastener placement
4. 只保留那些能提升多个案例共享运行时契约的改动

## 关键证据

1. `benchmark/runs/20260413_102600/L2_172`
2. `benchmark/runs/20260413_141000/L1_218`
3. `benchmark/runs/20260413_142700/L2_130`
4. `test_runs/20260413_094502`
5. `demos/build123d_foundations/demo_local_frame_countersink.py`
6. `demos/build123d_foundations/demo_half_shell_directional_holes.py`
7. `demos/build123d_foundations/demo_enclosure_body_lid.py`
