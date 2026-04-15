## 执行摘要

这轮 Build123d 迁移已经从“换库实验”推进到“运行时契约重建”。

最准确的结论不是“Build123d 已经在所有 benchmark 上全面赢了 CadQuery”，而是下面三点：

1. 迁移已经不再是语法替换，而是把底层建模过程从 CadQuery 风格的隐含 workplane 状态，改造成 Build123d 风格的显式 `Plane / BuildPart / Locations / Mode.*` 契约。
2. 这些契约已经真正进入运行时主路径，开始影响首轮写模、写前校验、validator closure、repair lane 和 benchmark 报表。
3. 当前已经有一批可直接打开目录验证的正证据，包括一轮成功、健康修复闭环、family 级提升和 validator/read-stall 收口；同时也明确保留了 half-shell 和 enclosure family 的边界。

一句话结论可以压缩成：

> Build123d 迁移的真正价值，不是换了一个 CAD 库，而是把原来隐含、脆弱、难验证的几何行为，改造成显式、可拦截、可修复、可验证、可复盘的运行时契约。

## 当前架构

这个仓库的上层容器并没有被推倒重写。

保留下来的内容包括：

1. benchmark 入口
2. `planner → write tool → validator → repair` 主循环
3. `prompts/`、`plans/`、`actions/`、`queries/`、`trace/`、`outputs/`、`evaluation/` 这套 artifact 布局

真正被替换的是底层建模契约：

1. 从旧的 CadQuery 风格链式状态
2. 切到 Build123d 的显式 `Plane / BuildPart / BuildSketch / Locations / Mode.*`

迁移之所以有工程价值，不是因为 Build123d “更新”，而是因为这套显式对象更适合被 runtime 编程。对这个项目来说，最重要的不是“手写 CAD 舒不舒服”，而是：

1. 首轮写模能不能被约束到更可靠的 skeleton
2. 错误能不能在执行前就被写前校验拦住
3. validator 和 feature probe 能不能围绕同一套几何语义工作
4. 失败能不能收束成可复用的 family repair lane

对应代码位置：

1. `src/sub_agent/prompts/codegen.md`
2. `src/sub_agent_runtime/skill_pack.py`
3. `src/sub_agent_runtime/tool_runtime.py`
4. `src/sub_agent_runtime/agent_loop_v2.py`
5. `docs/cad_iteration/TOOL_SURFACE.md`

## 这次改了什么

### 4/13：先把迁移说明面和第一批契约面搭起来

这一天完成了三件事：

1. 建立 `demos/build123d_foundations/`，把局部坐标、半壳体、enclosure body/lid 三类问题拆成最小演示。
2. 拿 `L2_172` 和 `L1_218` 两个真实 run，分别证明一轮成功和健康修复闭环。
3. 把未知 prompt `test_runs/20260413_094502` 转成结构化失败，而不是黑盒 sandbox error。

关键目录：

1. `benchmark/runs/20260413_102600/L2_172`
2. `benchmark/runs/20260413_141000/L1_218`
3. `test_runs/20260413_094502`
4. `docs/work_logs/2026-04-13_build123d迁移实验与演示说明.md`

### 4/14：把“值不值得继续投”这件事讲清楚

这一天的重点不是继续报喜，而是明确说明当前 Build123d 相比 CadQuery 的真实位置。

完成的事情包括：

1. 用 fresh run 做 Build123d / CadQuery 同题 benchmark 对照。
2. 继续围绕共享 family 补产品级规则，而不是补单 case 提示词。
3. 用 `L2_88` 的前后对照证明：一条通用规则可以把同一 family 从 fresh run 失败拉到一轮满分。

关键目录：

1. `benchmark/runs/20260414_091747`
2. `benchmark/runs/20260414_130005/L2_88`
3. `report-20260414-build123d-vs-cadquery-benchmark/`

### 4/15：把 validator closure 和 canary 指标接进主路径

这一天的重点是让“项目能写出几何”进一步升级成“项目能稳定判断自己已经做对”。

完成的事情包括：

1. 新增 canary case set 和 baseline 指标。
2. 收紧 `validate_requirement` 的超时、取消和 repeated validation 空转。
3. 用 `L1_122` 证明 read-stall / insufficient-evidence family 已经能回到一轮闭环。

关键目录：

1. `benchmark/runs/20260415_205500/L1_122`
2. `docs/work_logs/2026-04-15.md`
3. `docs/cad_iteration/CANONICAL_BASELINE.md`

## 这些改动如何在流程里起作用

### 1. 首轮写模先被约束成 Build123d 契约

通过 `src/sub_agent/prompts/codegen.md` 和 `src/sub_agent_runtime/skill_pack.py`，首轮写模不再沿用旧的 workplane 链式心智，而是优先落到显式的 Build123d 对象上。

最典型的证据是：

1. `benchmark/runs/20260413_102600/L2_172/plans/round_01_response.json`
2. 其中 `decision_summary` 已经明确写出要先完成 corner-frame 到 centered host frame 的坐标映射。

### 2. 写前校验会先拦住已知错法

当模型仍然会猜错 Build123d API、helper、关键字或 builder 上下文时，`src/sub_agent_runtime/tool_runtime.py` 会在真正执行前把这些错误拦下来，返回结构化 `rule_id`、`repair_hint` 和 repair recipe。

最典型的证据是：

1. `test_runs/20260413_094502/actions/round_01_execute_build123d.json`
2. 其中保留了 `failure_kind=execute_build123d_api_lint_failure`
3. 还保留了 `legacy_api.countersink_workplane_method`
4. 以及 `explicit_anchor_hole_countersink_array_safe_recipe`

### 3. 写成功以后，validator 和 feature probe 决定是否继续修

迁移后的关键区别，不是“写出来了一个 solid”，而是写成功后能不能进一步知道“还缺哪一类 family”。

最典型的证据是：

1. `benchmark/runs/20260413_141000/L1_218/queries/round_04_query_feature_probes.json`
2. 这个文件直接把问题缩小到 `annular_groove: 0/4 relevant checks currently pass`

### 4. repair lane 不再泛泛重写，而是沿 family 收口

一旦 probe 和 validator 已经把问题缩小到具体 family，运行时会退出泛泛的重写，进入更窄的 repair lane。

最典型的证据是：

1. `benchmark/runs/20260413_141000/L1_218/plans/round_05_response.json`
2. `decision_summary` 明确写出改走 annular-band subtraction，而不是继续盲猜 revolve 写法

### 5. benchmark 和汇报证据也跟着统一

4/15 以后，run 目录除了保留完整 artifact 外，还开始统一输出 baseline 指标，方便做 canary 回归和版本对比。

最典型的证据是：

1. `benchmark/runs/20260415_205500/brief_report.md`
2. `benchmark/runs/20260415_205500/run_diagnostics.md`

其中已经可以直接看到：

1. `first_solid_success_rate`
2. `requirement_complete_rate`
3. `runtime_rewrite_rate`
4. `tokens_per_successful_case`

## 负例

最值得讲的负例仍然是：

1. `test_runs/20260413_094502`

它的重要性不在于“这个 prompt 没过”，而在于它说明系统已经开始具备结构化失败能力。

建议打开的文件：

1. `actions/round_01_execute_build123d.json`
2. `trace/events.jsonl`

需要强调的关键信息：

1. 首轮失败被明确归类为 `execute_build123d_api_lint_failure`
2. 不是模糊的内核错误，而是具体规则 `legacy_api.countersink_workplane_method`
3. runtime 还同时返回了 `explicit_anchor_hole_countersink_array_safe_recipe`

这条负例最能说明：Build123d 迁移开始让失败本身也变成可以复用的系统资产。

## 正例

### 正例 A：`benchmark/runs/20260413_102600/L2_172`

这条 run 证明 Build123d 对局部坐标阵列已经开始直接产生收益。

建议打开的文件：

1. `benchmark/runs/20260413_102600/brief_report.md`
2. `plans/round_01_response.json`
3. `actions/round_01_execute_build123d.json`

建议强调的关键信息：

1. `brief_report.md` 显示 `L2_172 | PASS | rounds=1 | score=0.8565`
2. `decision_summary` 写明要先做 corner-frame 到 centered host frame 的映射
3. `actions/round_01_execute_build123d.json` 记录了 `solids=1`、`bbox=[100.0, 60.0, 8.0]`

这条正例证明的是：当问题本质是显式坐标变换时，Build123d 的 `Plane + Locations` 表面已经足够强。

### 正例 B：`benchmark/runs/20260413_141000/L1_218`

这条 run 更适合解释“Build123d 迁移为什么不仅仅是一轮命中率”。

建议打开的文件：

1. `benchmark/runs/20260413_141000/brief_report.md`
2. `queries/round_04_query_feature_probes.json`
3. `plans/round_05_response.json`
4. `queries/round_05_validate_requirement_post_write.json`
5. `trace/round_digest.md`

建议强调的关键信息：

1. `brief_report.md` 记录 `PASS | rounds=5 | writes=4 | probes=1`
2. `round_04_query_feature_probes.json` 把问题缩小到 `annular_groove`
3. `round_05_response.json` 明确改走 annular-band subtraction
4. `round_05_validate_requirement_post_write.json` 返回 `Requirement validation passed`

这条正例证明的是：系统已经开始具备“先缩小 family，再走更窄 repair lane”的能力。

### 正例 C：`benchmark/runs/20260414_130005/L2_88`

这条 run 是“共享 family 提升”最硬的证据。

建议同时对照两个目录：

1. `benchmark/runs/20260414_093041/L2_88`
2. `benchmark/runs/20260414_130005/L2_88`

建议打开的文件：

1. `benchmark/runs/20260414_130005/brief_report.md`
2. `plans/round_01_response.json`
3. `queries/round_01_validate_requirement_post_write.json`
4. `evaluation/benchmark_eval.json`

建议强调的关键信息：

1. 修复前是 fresh run 失败
2. 修复后 `brief_report.md` 记录 `PASS | rounds=1 | score=1.0000 | tokens=4462`
3. `queries/round_01_validate_requirement_post_write.json` 返回 `Requirement validation passed`
4. `evaluation/benchmark_eval.json` 记录 `STEP geometric signatures are closely aligned`

这条正例证明的是：一条产品级规则已经能改善整个 family，而不是只对一个 case 生效。

### 正例 D：`benchmark/runs/20260415_205500/L1_122`

这条 run 用来说明最近两天的重点成果：closure 收口。

建议打开的文件：

1. `benchmark/runs/20260415_205500/brief_report.md`
2. `run_diagnostics.md`
3. `trace/round_digest.md`
4. `queries/round_01_validate_requirement_post_write.json`

建议强调的关键信息：

1. `baseline_metrics` 已经写入聚合报告
2. `first_solid_success_rate=1.0`
3. `requirement_complete_rate=1.0`
4. `tokens_per_successful_case=4888.0`
5. `round_digest.md` 显示 `stop_reason=post_write_validated_complete`

这条正例证明的是：系统开始稳定地把“已经做对”收成一轮结束，而不是继续停留在 repeated validation 或 read-stall。

## 演示入口

演示面已经整理成独立目录：

1. `demos/build123d_foundations/`

推荐按下面顺序讲：

### Demo A：局部坐标沉头孔板

1. 文件：`demos/build123d_foundations/demo_local_frame_countersink.py`
2. 运行命令：

```bash
uv run python demos/build123d_foundations/demo_local_frame_countersink.py
```

3. 对应 STEP：
   `demos/build123d_foundations/artifacts/demo_01_local_frame_countersink.step`
4. 对应真实 run：
   `benchmark/runs/20260413_102600/L2_172`

### Demo B：半壳体与定向打孔

1. 文件：`demos/build123d_foundations/demo_half_shell_directional_holes.py`
2. 运行命令：

```bash
uv run python demos/build123d_foundations/demo_half_shell_directional_holes.py
```

3. 对应 STEP：
   `demos/build123d_foundations/artifacts/demo_02_half_shell_directional_holes.step`
4. 对应真实 run：
   `benchmark/runs/20260413_142700/L2_130`

### Demo C：body / lid / cavity / lip 生命周期

1. 文件：`demos/build123d_foundations/demo_enclosure_body_lid.py`
2. 运行命令：

```bash
uv run python demos/build123d_foundations/demo_enclosure_body_lid.py
```

3. 对应 STEP：
   `demo_03_enclosure_body.step` 与 `demo_03_enclosure_lid.step`
4. 对应真实 run：
   `test_runs/20260413_094502`

### 一次性生成全部产物

```bash
uv run python demos/build123d_foundations/run_all.py
```

关键输出：

1. `demos/build123d_foundations/artifacts/summary.json`

这个文件已经整理好了：

1. `title`
2. `narrative`
3. `talking_points`
4. `bbox`
5. `step_path`

## Artifact Chain

建议按下面顺序看 artifact，不要从目录树盲翻：

1. `prompts/`
   - 看模型到底收到了什么
   - 代表文件：`benchmark/runs/20260415_205500/L1_122/prompts/round_01_user_prompt.txt`
2. `plans/`
   - 看这一轮为什么选这条路
   - 代表文件：`benchmark/runs/20260413_102600/L2_172/plans/round_01_response.json`
3. `actions/`
   - 看写模是否成功，失败是哪条规则拦住的
   - 代表文件：`test_runs/20260413_094502/actions/round_01_execute_build123d.json`
4. `queries/`
   - 看 validator 和 feature probe 对当前模型怎么判断
   - 代表文件：`benchmark/runs/20260413_141000/L1_218/queries/round_04_query_feature_probes.json`
5. `trace/`
   - 看整条回合链如何收口
   - 代表文件：`benchmark/runs/20260415_205500/L1_122/trace/round_digest.md`
6. `outputs/`
   - 看最终 STEP 和几何信息
   - 代表文件：`demos/build123d_foundations/artifacts/summary.json`
7. `evaluation/`
   - 看 benchmark 对比和差异说明
   - 代表文件：`benchmark/runs/20260414_130005/L2_88/evaluation/benchmark_eval.json`

## 当前判断

已经证明的事情：

1. Build123d 迁移已经形成了真实可运行的主路径。
2. 局部坐标、annular groove repair、revolve family、validator closure 这几条线已经出现明确正证据。
3. 错误开始变成可归类、可修复、可复盘的运行时资产。

仍未证明的事情：

1. Build123d 尚未在 full L1/L2 上稳定全面领先 CadQuery。
2. `L2_130` 代表的 half-shell + directional hole family 还需要继续提高首轮 skeleton 命中率。
3. enclosure body/lid/cavity/lip 组合推理还没有收口到稳定通用能力。

## 下一步计划

1. 继续沿共享 family 做规则和 repair lane 收口，不接受单 case 提示词补丁。
2. 持续跑 canary 和 full L1/L2，重点关注 first solid、closure、validator mismatch 和 read-stall。
3. 把已经生效的 contract 继续同步到 `docs/cad_iteration/`、demo 说明和汇报页面中。

## 关键证据总表

1. `benchmark/runs/20260413_102600/L2_172`
2. `benchmark/runs/20260413_141000/L1_218`
3. `test_runs/20260413_094502`
4. `benchmark/runs/20260414_130005/L2_88`
5. `benchmark/runs/20260415_205500/L1_122`
6. `demos/build123d_foundations/`
7. `report-20260414-build123d-vs-cadquery-benchmark/`
