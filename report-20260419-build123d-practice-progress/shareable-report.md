# Build123d 4/19 Practice 进展汇报

## 今日结论

今天的工作可以压缩成三句话：

1. practice lane 已经不是“只能在 benchmark 里看日志”的半成品，而是能在本机真实跑出 Build123d 几何、STEP、trace 和多视图工件的 live lane。
2. 当前主瓶颈已经从“环境不可用 / API 完全不会用”收敛到两个更窄的问题：`explicit_anchor_hole` 的 countersink 几何没有被 validator 承认，以及 local-finish 尾段在短预算下容易把最后一轮浪费在 read/validate 上。
3. 今天新增的 policy 与 grounding 改动已经在真实 Kimi run 中留下两条清晰证据链：
   - `20260419_154837 -> 20260419_155523 -> 20260419_155831 -> 20260419_160521`
     证明 closure 问题被逐层压缩。
   - `20260419_161804 -> 20260419_162442 -> 20260419_162814`
     证明 first-write 的 Build123d 幻觉已经从 manual cutter / broad fillet / side-face plane 猜错，收敛到 `hallucination_events = 0`。

## 今天做了什么

今天不是继续围着固定 benchmark 堆规则，而是沿着 practice lane 的共享能力面做了三类改动。

### 1. 补强 local-face grounding，避免“前表面需求被模糊通过”

代码：

1. `src/sandbox_mcp_server/feature_probe_grounding.py`
2. `src/sandbox_mcp_server/service.py`
3. `tests/unit/sandbox_mcp_server/test_feature_probe_grounding.py`

这部分新增了 `named_face_local_edit` 的 side-specific grounding：

1. `requested_face_targets`
2. `requested_side_face_targets`
3. `specific_side_target_grounded`
4. `local_host_target_not_grounded`

目的不是给某个 case 写特判，而是把“用户明确要求 front/side face，本轮 probe 到底有没有把它真实落到几何上”变成通用可观测面。

### 2. 补强短预算 closure，避免 semantic refresh 链把最后两轮耗尽

代码：

1. `src/sub_agent_runtime/agent_loop_v2.py`
2. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`

今天连续补了三类共享能力：

1. `repair_after_topology_refresh_under_budget`
   - 含义：whole-part repair 之后如果已经做过 topology refresh，且预算只剩 2 轮，不要继续困在 `graph_refresh`，应直接重开 actionable repair。
2. `repair_after_local_finish_semantic_refresh_under_budget`
   - 含义：successful `apply_cad_action` 之后如果又做过 semantic refresh，且只剩最后 1 轮，不要把最后一轮浪费在 `validate_requirement`，应直接重开 actionable repair。
3. `code_escape_after_local_finish_semantic_refresh_under_budget`
   - 含义：successful `apply_cad_action` 之后如果又做过 semantic refresh、只剩最后 1 轮、仍有 concrete blockers，但拿不到 actionable patch，就直接放行 `execute_build123d` 做 whole-part escape，而不是退回 validation-only closure。
4. `code_first_local_finish_tail_contract`
   - 含义：当 prompt 本身就说明 topology-aware local finishing pass 应该保留时，首轮 code-first 写入优先稳定 host、front recess、hole/countersink helper contract，把最容易漂移的 broad fillet 和 side-face sketch 猜测从第一写里拿掉。

这两条都是 budget-aware closure，不是 case rule。

### 3. 继续把 write/read/repair 的边界压清楚

代码：

1. `src/sub_agent_runtime/agent_loop_v2.py`
2. `src/sub_agent_runtime/practice_runner.py`
3. `src/sub_agent_runtime/hallucination.py`

今天继续围绕 practice lane 关注这三个问题：

1. 本轮有没有真实 local targeting
2. local targeting 用到的 ref 是不是 fresh
3. 剩余失败到底属于 write、read、repair 还是 validation

这使得今天的汇报不再停留在“模型还是不稳定”，而是能明确说出到底卡在哪一层。

## 如何证明这些改动有效

### 证据 1：单测先锁住，再进入真实 run

今天补的两个最关键回归已经被单测锁住：

1. `tests/unit/sandbox_mcp_server/test_feature_probe_grounding.py`
2. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`

关键命令：

```bash
./.venv/bin/pytest tests/unit/sandbox_mcp_server/test_feature_probe_grounding.py -q
./.venv/bin/pytest tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py -q -k 'validate_after_local_finish_semantic_refresh or local_finish_validation_evidence_gap or last_round_after_successful_local_finish_semantic_refresh_reopens_repair_lane or repair_last_round_after_feature_probe_assessment or under_grounded_kernel_patch'
```

结果：

1. feature probe grounding 相关测试通过
2. local-finish / budget-closure 相关 policy 回归 `5 passed`
3. 新增 fallback 红绿测：

```bash
./.venv/bin/pytest tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py -q -k 'last_round_after_successful_local_finish_semantic_refresh_falls_back_to_code_escape_without_patch or last_round_after_successful_local_finish_semantic_refresh_reopens_repair_lane'
```

结果：

1. `code_escape_after_local_finish_semantic_refresh_under_budget` 先红后绿
2. 原有 `repair_after_local_finish_semantic_refresh_under_budget` 未被打坏

### 证据 2：`20260419_162814` 证明首轮 Build123d 幻觉已经压到 0

最值得打开的文件：

1. `practice_runs/20260419_162814/brief_report.md`
2. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`
3. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_01_response.json`
4. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_01_execute_build123d.json`
5. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_03_query_topology.json`
6. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_06_validate_requirement.json`

这条 run 证明了三件更关键的事：

1. `hallucination_events = 0`
2. 首轮 `execute_build123d` 直接成功产出：
   - `model.step`
   - `solids = 1`
   - `bbox = [66.0, 42.0, 16.0]`
3. 整条 run 只用了两次真实写：
   - `execute_build123d`
   - `apply_cad_action`

更重要的是，round 1 的决策已经明显变化：

1. 主动声明“先建 host，再把 top-opening fillet 留给后续 topology-aware local finish”
2. 不再手写 manual `Cylinder(...)` countersink cutter
3. 不再在 side-face plane 上乱用 `shift_origin((0, 0, 0))`

这说明今天后半段新增的 focused skill 已经不只是“文案存在”，而是开始改变真实 Kimi 的首轮写法。

### 证据 3：`20260419_162442` 证明新 skill 先把错误形态往正确方向推

最值得打开的文件：

1. `practice_runs/20260419_162442/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_01_response.json`
2. `practice_runs/20260419_162442/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_01_execute_build123d.json`
3. `practice_runs/20260419_162442/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_02_execute_build123d.json`

这条 run 虽然还没有收口，但它非常有价值，因为它说明新 skill 先改变了模型的“犯错方式”：

1. round 1 已经不再用 manual cutter，而是改成 `CounterSinkHole(...)`
2. round 1 已经主动把 top-opening fillet 留到后续 local finish
3. 下一层新暴露的问题变成了：
   - `CounterSinkHole` keyword alias 猜错
   - side-face workplane 的 `shift_origin(...)` 猜错

这正是合理的中间态：先把更粗的 manual-cutter / broad-fillet 幻觉推出去，再继续压更窄的 helper keyword 与 side-face plane contract。

### 证据 4：`20260419_154837` 把旧瓶颈定位清楚

最值得打开的文件：

1. `practice_runs/20260419_154837/brief_report.md`
2. `practice_runs/20260419_154837/run_diagnostics.md`
3. `practice_runs/20260419_154837/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_03_validate_requirement_post_write.json`
4. `practice_runs/20260419_154837/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_06_query_feature_probes.json`
5. `practice_runs/20260419_154837/front_face_sketch_recess_focus_v00_194ed1dd/trace/events.jsonl`

这条 run 证明了两件事：

1. `named_face_local_edit` 的 front face grounding 在 probe 里已经能被识别，不再是“根本没读到 front face”。
2. 当时真正卡住的是 policy：runtime 在 `semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap` 里来回打转，最后 `max_rounds_reached`，没有把剩余预算重新打回 repair lane。

换句话说，`154837` 暴露的是“closure 不够”，不是“Build123d 完全不会用”。

### 证据 5：`20260419_155523` 证明第一条 budget closure 已进入 live lane

最值得打开的文件：

1. `practice_runs/20260419_155523/brief_report.md`
2. `practice_runs/20260419_155523/run_diagnostics.md`
3. `practice_runs/20260419_155523/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_execute_build123d.json`
4. `practice_runs/20260419_155523/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_05_query_feature_probes.json`
5. `practice_runs/20260419_155523/front_face_sketch_recess_focus_v00_194ed1dd/trace/events.jsonl`

这条 run 的关键变化：

1. `hallucination_events = 1`
2. round 4 已经真实重开 `execute_build123d`
3. 不再困在重复 graph refresh

也就是说，`repair_after_topology_refresh_under_budget` 不是纸面策略，已经能改变真实 Kimi run 的回合轨迹。

### 证据 6：`20260419_155831` 证明 local targeting 已真正落地

最值得打开的文件：

1. `practice_runs/20260419_155831/brief_report.md`
2. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`
3. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_05_query_topology.json`
4. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_apply_cad_action.json`
5. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_06_validate_requirement.json`
6. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/trace/failure_bundle.json`

这条 run 的重点不是“已经完成”，而是“它终于跑到了哪一步”：

1. `local_targeting_action_count = 1`
2. `fresh_targeting_action_count = 1`
3. `exact_ref_consumption_rate = 1.0`
4. `query_topology` 已经给出精确前表面 ref：
   - `face:2:F_d510e00ff44d`
5. round 4 已真实执行 `apply_cad_action(hole, face_ref=...)`
6. 剩余 blocker 收敛到 3 个：
   - `feature_countersink`
   - `two_mounting_holes_on_the_bottom_face`
   - `countersinks_on_the_mounting_holes`

这说明最新瓶颈已经不再是“找不到 front face”或“局部修改根本执行不了”，而是：

1. `explicit_anchor_hole` 的 countersink 几何仍未被 validator 承认
2. local-finish 尾段在短预算下还会把最后一轮花在 validation，而不是 repair

### 证据 7：今天新补的最后一轮 closure 已被测试锁住

新增测试：

1. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py::test_last_round_after_successful_local_finish_semantic_refresh_reopens_repair_lane`
2. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py::test_last_round_after_successful_local_finish_semantic_refresh_falls_back_to_code_escape_without_patch`

这条测试直接复现 `155831` 的问题形态：

1. whole-part write 成功
2. validation 留下 `explicit_anchor_hole` blocker
3. `apply_cad_action` 成功执行
4. semantic refresh 已经跑过
5. 只剩最后一轮

旧行为：

1. `policy_id = validate_after_local_finish_semantic_refresh`

新行为：

1. 有 actionable patch：
   - `policy_id = repair_after_local_finish_semantic_refresh_under_budget`
2. 没有 actionable patch，但 blocker 还在：
   - `policy_id = code_escape_after_local_finish_semantic_refresh_under_budget`

这说明今天不是只写了“建议”，而是把 `155831` 暴露出来的 closure 缺口正式收入 policy，并且把“最后一轮该 repair 还是该 code escape”的共享边界锁成了回归行为。

### 证据 8：`20260419_160521` 证明最后一轮已经切回真实修复

最值得打开的文件：

1. `practice_runs/20260419_160521/brief_report.md`
2. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`
3. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_05_response.json`
4. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_05_apply_cad_action.json`
5. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_06_response.json`
6. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_06_execute_build123d.json`
7. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/trace/failure_bundle.json`

这条 rerun 进一步证明：

1. local targeting 仍然成立：
   - `local_targeting_action_count = 1`
   - `fresh_targeting_action_count = 1`
   - `exact_ref_consumption_rate = 1.0`
2. round 5 已真实执行：
   - `apply_cad_action(create_sketch, face_ref='face:1:F_0c4e17d39333')`
3. 最后一轮不再是 `validate_requirement`，而是：
   - `execute_build123d`
4. `failure_bundle.json` 中 `last_good_write.round = 6`，说明最后一轮预算已经花在真实修复，而不是纯读/纯验。

这说明今天后半段的尾段 closure 已经更健康：即使 case 还未收敛，最后一轮也优先用于真实修复。

## 现场演示建议

如果现场只开 5 个文件，推荐这个顺序：

1. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_01_execute_build123d.json`
   - 用来说明首轮 hallucination 已压到 0，第一写直接成功
2. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_06_validate_requirement.json`
   - 用来说明当前主瓶颈已经转成 validator / closure，而不是 Build123d API 猜错
3. `practice_runs/20260419_154837/brief_report.md`
   - 用来说明旧瓶颈：卡在 graph-refresh closure
4. `practice_runs/20260419_155523/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_execute_build123d.json`
   - 用来说明第一条 budget closure 已进入 live lane
5. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_05_query_topology.json`
   - 用来说明 exact front-face ref 已经拿到
6. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_apply_cad_action.json`
   - 用来说明 local finish 已真实消费具体 face_ref
7. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_06_execute_build123d.json`
   - 用来说明最新 rerun 的最后一轮已经切回真实修复
8. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`
   - 用来说明“有 patch 走 repair、没 patch 走 code escape”这两种尾段 closure 已被正式锁成回归行为

## 后续方向

下一步不应该回到 L1/L2 拟合，也不应该对单 case 加硬规则。当前最值得继续压的是三个共享问题。

### 1. `explicit_anchor_hole` 的 countersink 几何承认链

当前最明确的剩余失败就是：

1. `countersink_action=True`
2. `snapshot_countersink_geometry=False`
3. `cone_like_face_present=False`

这说明 write 已经尝试做 countersink，但 validator / geometry read-model 还没有把结果稳定地承认为“沉头孔已满足”。

主证据：

1. `practice_runs/20260419_162814/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_06_validate_requirement.json`

### 2. local-finish 尾段的 budget closure

今天已经把：

1. `repair_after_topology_refresh_under_budget`
2. `repair_after_local_finish_semantic_refresh_under_budget`

纳入 policy。

下一步就是继续看真实 rerun 是否把最后一轮从 `validate` 真正切成 `execute_build123d` 或 `apply_cad_action`。

`20260419_160521` 已经给出了第一条正面证据：最后一轮已真实切到 `execute_build123d`。而 `20260419_162814` 说明当 first-write 幻觉被压平以后，剩余矛盾更集中地表现为 `validate_after_local_finish_semantic_refresh` 仍然会占掉最后一轮。下一步继续看的不是“能不能切回修复”，而是“在 first-write 已稳定的前提下，最后一轮能否更多地落到 repair/code escape，而不是 validation-only closure”。

### 3. validator core check 的 broad aggregation

`155831` 里还有一个值得继续跟踪的问题：即使前表面 local edit 已经真实进入局部链路，validator 的一些高层 checks 仍然比较宽泛，例如：

1. `feature_target_face_edit`
2. `feature_target_face_subtractive_merge`

这说明后续仍需继续推进 evidence-first grounding，而不是让高层 check 过早“模糊通过”。

## 附录：工件链路

如果需要继续下钻，一条完整 run 的工件顺序建议如下：

1. `prompts/round_01_request.json`
2. `plans/round_01_response.json`
3. `actions/round_01_execute_build123d.json`
4. `queries/round_03_query_feature_probes.json`
5. `queries/round_05_query_topology.json`
6. `actions/round_04_apply_cad_action.json`
7. `queries/round_06_validate_requirement.json`
8. `trace/round_digest.md`
9. `trace/failure_bundle.json`

最适合当主案例的目录：

1. `practice_runs/20260419_155831/front_face_sketch_recess_focus_v00_194ed1dd/`
2. `practice_runs/20260419_160521/front_face_sketch_recess_focus_v00_194ed1dd/`
